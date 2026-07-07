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

    _JSON_FENCE_RE = re.compile(r'^```(?:json)?\s*\n?(.*?)\n?```$', re.DOTALL)

    @classmethod
    def _strip_json_fence(cls, text: str) -> str:
        """Strip a ```json ... ``` / ``` ... ``` markdown code fence.

        Some models wrap their answer in a fence even when JSON-only output
        was requested, which makes a plain json.loads() fail. Applying this
        before parsing keeps that a no-op for models that already return bare
        JSON, while fixing the ones that don't.
        """
        if not isinstance(text, str):
            return text
        stripped = text.strip()
        match = cls._JSON_FENCE_RE.match(stripped)
        return match.group(1).strip() if match else stripped

    def _call_client(self, user_message: str, system_message: str, force_json: bool = False, image=None, **kwargs):
        if self.use_vlm:
            text_prompt = f"**System Message**:\n{system_message}\n\n**User Message**: \n{user_message}"
            return self.client(text_prompt=text_prompt, image=image, force_json=force_json, **kwargs)
        else:
            return self.client(user_message=user_message, system_message=system_message, force_json=force_json, **kwargs)

    def _build_action(self, action_dict: dict) -> UR5Action:
        """Construct a UR5Action from a raw LLM/VLM dict, failing loud with
        full context instead of a bare, hard-to-place pydantic ValidationError.
        """
        try:
            return UR5Action(**action_dict)
        except ValidationError as exc:
            method_name = getattr(self, 'method_name', self.__class__.__name__)
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
