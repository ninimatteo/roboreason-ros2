"""Abstract base class for all reasoning methods — ported from RoboReason-Lab."""
import json
import re
from abc import ABC, abstractmethod

from pydantic import ValidationError

from robo_reason_reasoning.FoundationClients.src.llm_client import LLMClient
from robo_reason_reasoning.FoundationClients.src.vlm_client import VLMClient
from robo_reason_reasoning.extraction_classes import UR5Action


class ActionParsingError(RuntimeError):
    """Raised when the LLM/VLM's raw action JSON fails UR5Action validation.

    Wraps the underlying pydantic ValidationError with context (which
    reasoning method, which raw payload) so a malformed field (e.g. a
    mashed-together number like '-0.50.03') is diagnosable from the error
    message alone instead of surfacing as a bare, hard-to-place
    ValidationError deep in pydantic internals.
    """


class ResponseParsingError(RuntimeError):
    """Raised when the LLM/VLM's raw response can't be parsed as JSON.

    Most often this means the model exhausted its max_tokens budget on
    internal reasoning before ever emitting the final JSON answer, leaving
    an empty or truncated response — not a legitimate empty answer. See
    cot_sc.py's per-sample handling, which tolerates this because it
    already samples k independent responses and only raises once every
    sample fails; single-shot methods have no such fallback, so they must
    fail loud here instead of crashing json.loads with a bare, hard-to-place
    JSONDecodeError.
    """


class ReasoningMethod(ABC):
    """Base class for all reasoning methods."""

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm',
                 grounding_mode: str = 'point', **kwargs):
        self.step_counter = 0
        self.actions_memory = {}
        self.client_type = client_type.lower()
        self.use_vlm = self.client_type == 'vlm'
        # VLM-only: 'point' (single [x, y] pixel click, default) or 'bbox'
        # ([x_min, y_min, x_max, y_max] pixel box). See VLM_GROUNDING_MODE in
        # config.py. Ignored in LLM mode.
        self.grounding_mode = (grounding_mode or 'point').lower()

        if client_parameters is None:
            client_parameters = {}

        if self.use_vlm:
            self.client = VLMClient(**client_parameters)
        else:
            self.client = LLMClient(**client_parameters)

    def _select_prompts(self, prompt_cls):
        """Return the VLM or LLM prompt tuple for the active client type."""
        if self.use_vlm:
            return prompt_cls.get_vlm_prompts(grounding_mode=self.grounding_mode)
        return prompt_cls.get_llm_prompts()

    def _image_pixel_dims(self, image) -> tuple:
        """Return (width, height) of the image file, or (0, 0) if unavailable.

        Used to fill the {pixels_width}/{pixels_height} placeholders in VLM
        prompts so the model knows the valid pixel coordinate range for the
        image it's reasoning about.
        """
        if not image:
            return 0, 0
        try:
            from PIL import Image
            with Image.open(image) as img:
                return img.size
        except Exception:
            return 0, 0

    _THINK_BLOCK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)
    # Catches a <think> block that never closes — the model exhausted
    # max_tokens mid-reasoning before emitting </think> or any JSON. Applied
    # only after _THINK_BLOCK_RE has already removed every well-formed
    # (closed) block, so this only ever matches a genuinely unclosed
    # trailing block, truncating from '<think>' to the end of the string.
    _UNCLOSED_THINK_BLOCK_RE = re.compile(r'<think>.*$', re.DOTALL)

    @classmethod
    def _strip_think_blocks(cls, text: str) -> str:
        """Strip <think>...</think> chain-of-thought blocks (Qwen3-family
        reasoning models) before JSON extraction/blank-response checks.

        Ported from a colleague's VLMClient implementation, where this same
        pattern is used to keep <think> preambles from contaminating JSON
        parsing. Also strips a trailing *unclosed* <think> block (real
        failure mode: max_tokens ran out mid-reasoning before the model
        ever closed the tag or emitted JSON) so it's treated the same as a
        closed pure-think response by _is_blank_response, instead of
        sailing through unstripped because the closing tag never arrived.
        """
        if not text:
            return text
        stripped = cls._THINK_BLOCK_RE.sub('', text)
        stripped = cls._UNCLOSED_THINK_BLOCK_RE.sub('', stripped)
        return stripped.strip()

    @classmethod
    def _extract_json(cls, raw: str) -> str:
        """Robustly extract a JSON object/array from a raw LLM/VLM response.

        Strips <think>...</think> blocks first, then tries in order: a
        brace/bracket-matching scan (handles leading/trailing prose around
        the JSON), a direct parse, markdown-fence stripping, and a final
        regex scan for a {...}/[...] block. Raises ValueError with the first
        200 raw chars if every strategy fails. Ported from a colleague's more
        battle-tested VLMClient, replacing the previous bare fence-strip +
        json.loads which only handled the "whole response is one JSON fence"
        case.
        """
        if not raw or not raw.strip():
            raise ValueError("Empty response from model")

        cleaned = cls._strip_think_blocks(raw)

        for start_idx, char in enumerate(cleaned):
            if char not in ('{', '['):
                continue
            end_char = '}' if char == '{' else ']'
            depth = 0
            in_string = False
            escape = False
            for idx in range(start_idx, len(cleaned)):
                c = cleaned[idx]
                if escape:
                    escape = False
                    continue
                if c == '\\':
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if c == char:
                        depth += 1
                    elif c == end_char:
                        depth -= 1
                        if depth == 0:
                            candidate = cleaned[start_idx:idx + 1]
                            try:
                                json.loads(candidate)
                                return candidate
                            except json.JSONDecodeError:
                                break

        try:
            json.loads(cleaned)
            return cleaned
        except json.JSONDecodeError:
            pass

        stripped = re.sub(r'^```(?:json)?\s*', '', cleaned.strip(), flags=re.IGNORECASE)
        stripped = re.sub(r'```\s*$', '', stripped.strip())
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            pass

        for pattern in (r'(\{.*\})', r'(\[.*\])'):
            match = re.search(pattern, cleaned, flags=re.DOTALL)
            if match:
                candidate = match.group(1)
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue

        raise ValueError(
            f"Could not extract valid JSON from model response. "
            f"First 200 chars: {raw[:200]!r}"
        )

    @classmethod
    def _is_blank_response(cls, raw) -> bool:
        """True if raw is empty, or entirely consumed by <think> reasoning
        with nothing left after stripping it — the signature of a
        reasoning-heavy model exhausting max_tokens before emitting an
        answer.
        """
        if not isinstance(raw, str):
            return not raw
        return not cls._strip_think_blocks(raw).strip()

    # A blank/unrecoverable force_json response is retried once with a larger max_tokens
    # budget before we give up and let _parse_json_response raise. Capped so
    # a persistently-empty model can't be retried into an unbounded bill.
    _RETRY_MAX_TOKENS_MULTIPLIER = 2
    _RETRY_MAX_TOKENS_CAP = 32768

    def _dispatch_client(self, user_message: str, system_message: str, force_json: bool, image, **kwargs):
        if self.use_vlm:
            text_prompt = f"**System Message**:\n{system_message}\n\n**User Message**: \n{user_message}"
            return self.client(text_prompt=text_prompt, image=image, force_json=force_json, **kwargs)
        else:
            return self.client(user_message=user_message, system_message=system_message, force_json=force_json, **kwargs)

    def _call_client(self, user_message: str, system_message: str, force_json: bool = False, image=None, **kwargs):
        raw = self._dispatch_client(user_message, system_message, force_json, image, **kwargs)

        if force_json and self._is_blank_response(raw):
            # Most often a reasoning-heavy model (e.g. Groq's qwen3.6-27b)
            # exhausted its whole max_tokens budget on internal <think>
            # reasoning before emitting the final JSON — either leaving a
            # blank response, or a huge (possibly unclosed) <think> dump
            # with no JSON anywhere in it; _strip_think_blocks handles both.
            # Retry once with a bigger budget instead of every call site
            # needing its own retry/backoff logic — only raises
            # ResponseParsingError downstream if this retry also comes back
            # unusable.
            base_max_tokens = kwargs.get('max_tokens', getattr(self.client, 'max_tokens', 8192))
            retry_max_tokens = min(base_max_tokens * self._RETRY_MAX_TOKENS_MULTIPLIER, self._RETRY_MAX_TOKENS_CAP)
            if retry_max_tokens > base_max_tokens:
                retry_kwargs = dict(kwargs, max_tokens=retry_max_tokens)
                self._verbose_print(
                    'Blank force_json response — retrying once with a larger max_tokens budget',
                    {'base_max_tokens': base_max_tokens, 'retry_max_tokens': retry_max_tokens},
                )
                raw = self._dispatch_client(user_message, system_message, force_json, image, **retry_kwargs)

        return raw

    def _parse_json_response(self, raw: str, *, context: str):
        """Extract and parse the JSON payload from an LLM/VLM raw response.

        Raises ResponseParsingError (instead of a bare json.JSONDecodeError
        or ValueError) on empty/truncated/malformed output, with the raw
        text and calling context included so the failure is diagnosable from
        the message alone.
        """
        try:
            return json.loads(self._extract_json(raw))
        except (ValueError, json.JSONDecodeError) as exc:
            method_name = getattr(self, 'method_name', self.__class__.__name__)
            raise ResponseParsingError(
                f"[{method_name}] {context}: LLM/VLM response could not be "
                f"parsed as JSON (raw={raw!r}). This usually means the model "
                "exhausted its max_tokens budget on internal reasoning before "
                "emitting the final answer — try raising max_tokens/"
                "REQUEST_TIMEOUT_S, passing reasoning_effort='none' for "
                "reasoning-heavy Groq models, or a less reasoning-heavy model."
            ) from exc

    def _build_action(self, action_dict) -> UR5Action:
        """Construct a UR5Action from a raw LLM/VLM dict, failing loud with
        full context instead of a bare, hard-to-place pydantic ValidationError
        (or, for a non-dict entry — e.g. CoT-SC's most-common-actions voting
        picking a raw int/str that leaked into a "plan" list from a malformed
        model response — a bare, hard-to-place TypeError from `**action_dict`).
        """
        method_name = getattr(self, 'method_name', self.__class__.__name__)
        if not isinstance(action_dict, dict):
            raise ActionParsingError(
                f"[{method_name}] LLM/VLM returned a non-dict action "
                f"({type(action_dict).__name__}): {action_dict!r}"
            )
        try:
            return UR5Action(**action_dict)
        except ValidationError as exc:
            raise ActionParsingError(
                f"[{method_name}] LLM/VLM returned an action that failed "
                f"UR5Action validation: {action_dict!r}"
            ) from exc

    def _update_step_counter(self):
        self.step_counter += 1

    def _update_actions_memory(self, step: int, action):
        self.actions_memory[step] = action

    def _verbose_print(self, message: str, data=None):
        if getattr(self, 'verbose', False):
            print("-" * 50)
            method_name = getattr(self, 'method_name', 'REASONING_METHOD')
            print(f"[{method_name.upper()} VERBOSE] {message}")
            if data is not None:
                if isinstance(data, (dict, list)):
                    print(json.dumps(data, indent=2))
                else:
                    print(data)
            print("-" * 50)

    @abstractmethod
    def __call__(self, *args, **kwargs):
        pass

    @abstractmethod
    def set_user_request(self, user_request: str):
        pass

    def get_llm_usage_metrics(self):
        return getattr(self.client, 'usage_metrics', {})
