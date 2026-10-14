"""One task of the closed loop agent: a draft plan, then execution with tools.

The conversation is kept for the whole task, so the execution phase sees the
reasoning that produced the draft, and every later call sees the results of
the earlier ones. ROS free: the camera, deprojection and executor come in
through the Toolbox backend, the model through a ToolChatClient (or a fake).
"""

import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from robo_reason_planner.agent import prompts
from robo_reason_planner.agent.tools import ACTION_TOOLS, SUBMIT_PLAN, Toolbox, ToolResult
from robo_reason_planner.planning_cancel import PlanningCancelled


class AgentCancelled(PlanningCancelled):
    """The operator stopped the task."""


@dataclass
class Outcome:
    success: bool
    summary: str
    model_calls: int
    tool_calls: int
    duration_s: float
    actions: list = field(default_factory=list)


class AgentSession:
    def __init__(self, client, toolbox: Toolbox, command: str, *,
                 max_model_calls: int = 60, max_duration_s: float = 900.0,
                 keep_images: int = 1, max_nudges: int = 3, history_turns: int = 8,
                 max_input_tokens: int = 0, max_request_tokens: int = 0, max_repeats: int = 3,
                 log=print):
        self.client = client
        self.toolbox = toolbox
        self.command = command
        self.max_model_calls = max_model_calls
        self.max_duration_s = max_duration_s
        self.keep_images = keep_images
        self.max_nudges = max_nudges
        self.history_turns = history_turns
        self.max_input_tokens = max_input_tokens
        self.max_request_tokens = max_request_tokens
        self.max_repeats = max_repeats
        self.input_tokens = 0
        self._last_failure = (None, 0)   # (call signature, times in a row)
        self._compacted_until = 0
        self.log = log
        self.messages = [{'role': 'system', 'content': prompts.SYSTEM}]
        # Every message as sent, with image paths instead of base64, for the debug trace.
        self.transcript = []
        self.model_calls = 0
        self.tool_calls = 0

    # ── phase 1: draft ─────────────────────────────────────────────────────

    def draft_plan(self, cancelled=None) -> list:
        image = self.toolbox.capture()
        w, h = self.toolbox.image_size
        text = prompts.PLAN_REQUEST.format(command=self.command, width=w, height=h)
        if self.toolbox.config.coords == 'norm1000':
            text += '\n' + prompts.PLAN_REQUEST_NORM
        self._add_user(text, image)

        for _ in range(3):
            self._check_cancel(cancelled)
            reply = self._chat([SUBMIT_PLAN], tool_choice='required', cancelled=cancelled)
            submitted = False
            for call in reply.tool_calls:
                result = self._run_call(call)
                submitted = submitted or (call.name == 'submit_plan' and not result.text.startswith('ERROR'))
            if submitted and self.toolbox.plan:
                return self.toolbox.plan
            if not reply.tool_calls:
                self._add_user('Call submit_plan with the steps.')
        raise RuntimeError('the model did not submit a plan')

    # ── phase 2: execute ───────────────────────────────────────────────────

    def execute(self, cancelled=None) -> Outcome:
        t0 = time.monotonic()
        self._add_user(prompts.EXECUTE_REQUEST, self.toolbox.capture())
        nudges = 0
        finished, success, summary = False, False, ''

        while not finished:
            self._check_cancel(cancelled)
            if self.model_calls >= self.max_model_calls:
                summary = f'gave up: reached {self.max_model_calls} model calls'
                break
            if time.monotonic() - t0 > self.max_duration_s:
                summary = f'gave up: exceeded {self.max_duration_s:.0f} s'
                break
            if self.max_input_tokens and self.input_tokens >= self.max_input_tokens:
                summary = f'gave up: used the token budget ({self.input_tokens} input tokens)'
                break
            self._compact_history()

            reply = self._chat(self.toolbox.execution_tools(), cancelled=cancelled)
            if reply.content.strip():
                self.log(f'[Agent] says: {reply.content.strip()[:300]}')
            if not reply.tool_calls:
                nudges += 1
                if nudges > self.max_nudges:
                    summary = 'gave up: the model stopped calling tools'
                    break
                self._add_user(prompts.NUDGE)
                continue
            nudges = 0

            new_image = None
            action_failed = False
            for call in reply.tool_calls:
                self._check_cancel(cancelled)
                if action_failed and call.name in ACTION_TOOLS:
                    # Its preconditions were decided before the failure was known.
                    self._skip_call(call, 'SKIPPED: an earlier action in this turn failed; '
                                          'look at the latest image and decide again.')
                    continue
                result = self._run_call(call)
                if call.name in ACTION_TOOLS and result.text.startswith(('ERROR', 'REFUSED')):
                    action_failed = True
                    if self._repeated_failure(call):
                        summary = (f'gave up: the same {call.name} call failed {self.max_repeats} times '
                                   f'in a row ({result.text[:120]})')
                        finished, success = True, False
                        break
                if result.image_path:
                    new_image = result.image_path
                if result.finished:
                    finished, success = True, result.success
                    summary = _arg(call, 'summary', '')
            if new_image and not finished:
                self._add_user('Latest camera image:', new_image)

        return Outcome(
            success=finished and success, summary=summary,
            model_calls=self.model_calls, tool_calls=self.tool_calls,
            duration_s=round(time.monotonic() - t0, 1), actions=list(self.toolbox.actions),
        )

    # ── plumbing ───────────────────────────────────────────────────────────

    def _chat(self, tools, tool_choice='auto', cancelled=None):
        self.model_calls += 1
        if cancelled is None:
            reply = self.client.chat(self.messages, tools, tool_choice=tool_choice)
        else:
            # Run the call aside so a stop doesn't wait for a slow (or rate
            # limited, retrying) model: the late reply is simply dropped.
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(self.client.chat, list(self.messages), tools, tool_choice=tool_choice)
            pool.shutdown(wait=False)
            while True:
                try:
                    reply = future.result(timeout=0.2)
                    break
                except FuturesTimeout:
                    self._check_cancel(cancelled)
        self.messages.append(reply.message)
        self.input_tokens += reply.input_tokens
        # Close to the provider's per request cap: keep fewer turns verbatim.
        if self.max_request_tokens and reply.input_tokens > 0.8 * self.max_request_tokens:
            self.history_turns = max(2, self.history_turns - 2)
        self.transcript.append({
            **{k: v for k, v in reply.message.items() if k != 'reasoning_content'},
            'reasoning': reply.reasoning[:4000] if reply.reasoning else '',
            'latency_s': round(reply.latency_s, 2),
            'tokens': [reply.input_tokens, reply.output_tokens],
        })
        self.log(f'[Agent] model call {self.model_calls}: {reply.latency_s:.1f}s, '
                 f'{len(reply.tool_calls)} tool call(s): {", ".join(c.name for c in reply.tool_calls) or "-"}')
        return reply

    def _run_call(self, call):
        self.tool_calls += 1
        try:
            args = call.parsed_arguments()
        except ValueError as exc:
            result = ToolResult(f'ERROR: {exc}')
        else:
            result = self.toolbox.run(call.name, args)
        self.messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': result.text})
        self.transcript.append({'role': 'tool', 'name': call.name, 'arguments': call.arguments,
                                'content': result.text, 'image': result.image_path})
        return result

    def _repeated_failure(self, call) -> bool:
        sig = (call.name, call.arguments)
        n = self._last_failure[1] + 1 if self._last_failure[0] == sig else 1
        self._last_failure = (sig, n)
        return n >= self.max_repeats

    def _skip_call(self, call, text):
        self.tool_calls += 1
        self.messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': text})
        self.transcript.append({'role': 'tool', 'name': call.name, 'arguments': call.arguments,
                                'content': text, 'image': None})

    def _add_user(self, text, image_path=None):
        if image_path is None:
            self.messages.append({'role': 'user', 'content': text})
        else:
            self.messages.append({'role': 'user', 'content': [
                {'type': 'text', 'text': text},
                {'type': 'image_url', 'image_url': {'url': _data_uri(image_path)}},
            ]})
            self._prune_images()
        self.transcript.append({'role': 'user', 'content': text, 'image': image_path})

    def _compact_history(self):
        """Shrink turns older than the last history_turns model replies.

        Every call resends the whole conversation, so old turns are paid for
        again and again. Their tool calls stay (the protocol needs each call
        paired with its result) but long results and texts are cut short and
        old reasoning is dropped. The request and the draft plan (the first
        three messages after the system prompt) are never touched."""
        replies = [i for i, m in enumerate(self.messages) if m['role'] == 'assistant']
        if len(replies) <= self.history_turns:
            return
        cutoff = replies[-self.history_turns]
        for m in self.messages[max(4, self._compacted_until):cutoff]:
            if m['role'] == 'tool' and len(m['content']) > _OLD_RESULT_CHARS:
                m['content'] = m['content'][:_OLD_RESULT_CHARS] + ' ...'
            elif m['role'] == 'assistant':
                m.pop('reasoning_content', None)
                if len(m.get('content') or '') > _OLD_RESULT_CHARS:
                    m['content'] = m['content'][:_OLD_RESULT_CHARS] + ' ...'
            elif m['role'] == 'user' and isinstance(m['content'], str) and len(m['content']) > _OLD_RESULT_CHARS:
                m['content'] = m['content'][:_OLD_RESULT_CHARS] + ' ...'
        self._compacted_until = max(self._compacted_until, cutoff)

    def _prune_images(self):
        """Keep only the latest keep_images images in the context."""
        seen = 0
        for msg in reversed(self.messages):
            if msg['role'] != 'user' or not isinstance(msg['content'], list):
                continue
            for i, part in enumerate(msg['content']):
                if part.get('type') == 'image_url':
                    seen += 1
                    if seen > self.keep_images:
                        msg['content'][i] = {'type': 'text', 'text': prompts.OLD_IMAGE}

    @staticmethod
    def _check_cancel(cancelled):
        if cancelled is not None and cancelled.is_set():
            raise AgentCancelled('stopped by the operator')

    def save_transcript(self, path: Path):
        path.write_text(json.dumps(self.transcript, indent=2, default=str))


_OLD_RESULT_CHARS = 160


def _arg(call, key, default):
    try:
        return call.parsed_arguments().get(key, default)
    except ValueError:
        return default


def _data_uri(path) -> str:
    raw = Path(path).read_bytes()
    mime = 'image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()
