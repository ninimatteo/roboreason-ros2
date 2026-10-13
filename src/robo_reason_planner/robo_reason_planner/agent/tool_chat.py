"""Multi-turn chat with native function calling (OpenAI-compatible tools).

The reasoning package's clients are single request/response: one prompt in,
one string out. The agent needs a conversation that grows over a task, where
the model calls tools and sees their results. Groq and Nebius both speak the
OpenAI chat.completions dialect with `tools`, so one small client covers both.

Provider setup (API key env var, base URL, model id aliases, HTTP timeout) is
reused from BaseFoundationClient so the model names are the same
`provider/model` strings the rest of the stack uses.
"""

import collections
import json
import time
from dataclasses import dataclass, field

from robo_reason_reasoning.FoundationClients.src.base_client import BaseFoundationClient

import os

# OpenAI compatible endpoints of providers BaseFoundationClient doesn't wire
# (or wires through another SDK). Key from {PROVIDER}_API_KEY as usual.
OPENAI_COMPATIBLE = {
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai/',
    'mistral': 'https://api.mistral.ai/v1',
    'openrouter': 'https://openrouter.ai/api/v1',
    'github': 'https://models.github.ai/inference',
    # needs CLOUDFLARE_ACCOUNT_ID too
    'cloudflare': 'https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1',
}

# HTTP status codes worth retrying: rate limit and transient server errors.
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, as the model wrote it

    def parsed_arguments(self) -> dict:
        """Arguments as a dict. Raises ValueError with a readable message."""
        try:
            args = json.loads(self.arguments or '{}')
        except json.JSONDecodeError as exc:
            raise ValueError(f'arguments are not valid JSON ({exc}): {self.arguments[:200]}')
        if not isinstance(args, dict):
            raise ValueError(f'arguments must be a JSON object, got: {self.arguments[:200]}')
        return args


@dataclass
class ChatReply:
    content: str
    tool_calls: list
    # The assistant message to append to the history, in the provider's own
    # shape (keeps provider extras such as reasoning_content, which some
    # thinking models require to be sent back on the next turn).
    message: dict
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    reasoning: str = ''


@dataclass
class _Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    per_call: list = field(default_factory=list)


class ToolChatClient(BaseFoundationClient):
    """chat(messages, tools) -> ChatReply, for groq/... and nebius/... models."""

    def __init__(self, max_retries: int = 4, tpm_limit: int = 0, **model_parameters):
        super().__init__(**model_parameters)
        # Tokens per minute the provider allows (0 = no pacing). Waiting here
        # is cheaper than collecting 429s and backing off blindly.
        self.tpm_limit = tpm_limit
        self._window = collections.deque()   # (time, tokens) of the last minute
        self._last_request_tokens = 0
        if self.provider not in ('groq', 'nebius', 'openai', *OPENAI_COMPATIBLE):
            raise ValueError(f'tool calling is not wired for provider {self.provider}')
        self.reasoning_effort = model_parameters.get('reasoning_effort') or None
        self.max_retries = max_retries
        self.usage = _Usage()

    def _initialize_client(self):
        if self.provider not in OPENAI_COMPATIBLE:
            return super()._initialize_client()
        from openai import OpenAI
        url = OPENAI_COMPATIBLE[self.provider].format(account=os.getenv('CLOUDFLARE_ACCOUNT_ID', ''))
        return OpenAI(api_key=self.api_key, base_url=self.base_url or url, timeout=self.request_timeout_s)

    def __call__(self, *args, **kwargs):
        raise NotImplementedError('use chat()')

    def chat(self, messages: list, tools: list, tool_choice='auto') -> ChatReply:
        kwargs = dict(
            model=self.model_name,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if self.reasoning_effort:
            kwargs['reasoning_effort'] = self.reasoning_effort

        self._pace()
        t0 = time.monotonic()
        try:
            response = self._create_with_retry(kwargs)
        except Exception as exc:
            # Some providers reject tool_choice='required'; 'auto' plus the
            # session's own retry ("call submit_plan") does the same job.
            if kwargs.get('tool_choice') != 'required' or getattr(exc, 'status_code', None) != 400:
                raise
            kwargs['tool_choice'] = 'auto'
            response = self._create_with_retry(kwargs)
        latency = time.monotonic() - t0

        msg = response.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or '{}')
            for tc in (msg.tool_calls or [])
        ]
        message = {'role': 'assistant', 'content': msg.content or ''}
        if calls:
            message['tool_calls'] = [
                {'id': c.id, 'type': 'function', 'function': {'name': c.name, 'arguments': c.arguments}}
                for c in calls
            ]
        extras = msg.model_dump(exclude_none=True)
        reasoning = extras.get('reasoning_content') or extras.get('reasoning') or ''
        if extras.get('reasoning_content'):
            message['reasoning_content'] = extras['reasoning_content']

        usage = getattr(response, 'usage', None)
        in_tok = getattr(usage, 'prompt_tokens', 0) or 0
        out_tok = getattr(usage, 'completion_tokens', 0) or 0
        self._update_metrics(in_tok, out_tok)
        self._window.append((time.monotonic(), in_tok + out_tok))
        self._last_request_tokens = in_tok + out_tok
        self.usage.calls += 1
        self.usage.input_tokens += in_tok
        self.usage.output_tokens += out_tok
        self.usage.latency_s += latency
        self.usage.per_call.append(round(latency, 2))

        return ChatReply(
            content=msg.content or '', tool_calls=calls, message=message,
            input_tokens=in_tok, output_tokens=out_tok, latency_s=latency,
            reasoning=reasoning,
        )

    def _pace(self):
        """Wait until the next request (estimated as large as the last one,
        plus some growth) fits in the provider's tokens per minute."""
        if not self.tpm_limit:
            return
        need = min(int(self._last_request_tokens * 1.15) + 300, self.tpm_limit)
        while True:
            now = time.monotonic()
            while self._window and now - self._window[0][0] > 60.0:
                self._window.popleft()
            used = sum(t for _, t in self._window)
            if used + need <= self.tpm_limit or not self._window:
                return
            time.sleep(min(60.0 - (now - self._window[0][0]) + 0.2, 5.0))

    def _create_with_retry(self, kwargs):
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                transient = status in _RETRY_STATUS or type(exc).__name__ in (
                    'APITimeoutError', 'APIConnectionError', 'InternalServerError', 'RateLimitError',
                )
                if not transient or attempt == self.max_retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
