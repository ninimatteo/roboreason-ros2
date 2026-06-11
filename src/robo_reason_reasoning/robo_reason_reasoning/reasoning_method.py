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


class LLMReasoningMethod(ReasoningMethod):
    pass


class ReasoningMethodTester:
    """Helper for offline testing of reasoning methods."""

    observations = {
        "non_symbolic": {
            "environment_map": (
                '{"objects": {"red_cube": {"type": "cube", "color": "red", '
                '"position": [0.45, -0.15, 0.025], "state": "on_table", "graspable": true}}, '
                '"targets": {"zone_a": {"type": "zone", "position": [0.72, -0.22, 0.0]}}}'
            ),
            "user_request": "Move the red cube to zone_a.",
        }
    }

    @staticmethod
    def get_test_observation():
        return ReasoningMethodTester.observations["non_symbolic"]

    @staticmethod
    def get_test_llm_parameters(**kwargs):
        return {
            'temperature': kwargs.get('temperature', 0.0),
            'model_name': kwargs.get('model_name', 'groq/llama4-scout-17b'),
        }
