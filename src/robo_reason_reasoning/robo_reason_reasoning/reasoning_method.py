"""Abstract base class for all reasoning methods — ported from RoboReason-Lab."""
import json
from abc import ABC, abstractmethod

from robo_reason_reasoning.FoundationClients.src.llm_client import LLMClient
from robo_reason_reasoning.FoundationClients.src.vlm_client import VLMClient


class ReasoningMethod(ABC):
    """Base class for all reasoning methods."""

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm', **kwargs):
        self.step_counter = 0
        self.actions_memory = {}
        self.client_type = client_type.lower()
        self.use_vlm = self.client_type == 'vlm'
        
        if client_parameters is None:
            client_parameters = {}
            
        if self.use_vlm:
            self.client = VLMClient(**client_parameters)
        else:
            self.client = LLMClient(**client_parameters)

    def _select_prompts(self, prompt_cls):
        """Return the VLM or LLM prompt tuple for the active client type."""
        getter = prompt_cls.get_vlm_prompts if self.use_vlm else prompt_cls.get_llm_prompts
        return getter()

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

    def _call_client(self, user_message: str, system_message: str, force_json: bool = False, image=None, **kwargs):
        if self.use_vlm:
            text_prompt = f"**System Message**:\n{system_message}\n\n**User Message**: \n{user_message}"
            return self.client(text_prompt=text_prompt, image=image, force_json_response=force_json, **kwargs)
        else:
            return self.client(user_message=user_message, system_message=system_message, force_json=force_json, **kwargs)

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
