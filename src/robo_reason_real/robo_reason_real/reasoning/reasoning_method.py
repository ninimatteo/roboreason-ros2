"""Abstract base class for all LLM reasoning methods — ported from RoboReason-Lab."""
import json
from abc import ABC, abstractmethod


class LLMReasoningMethod(ABC):
    """Base class for all reasoning methods."""

    def __init__(self):
        self.step_counter = 0
        self.actions_memory = {}

    def _update_step_counter(self):
        self.step_counter += 1

    def _update_actions_memory(self, step: int, action):
        self.actions_memory[step] = action

    def _verbose_print(self, message: str, data=None):
        if self.verbose:
            print("-" * 50)
            print(f"[{self.method_name.upper()} VERBOSE] {message}")
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

    @abstractmethod
    def get_llm_usage_metrics(self):
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
