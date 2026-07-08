"""
EmbodiedAgent — orchestrates Foundation Model reasoning for UR5 manipulation.

Supported reasoning modes: fhp, ffhp, react, cot_sc, tot, always_act, self_refine
Supported client types: llm (text-only), vlm (vision-language)
"""

from collections import namedtuple

import dotenv

from robo_reason_reasoning.fhp_ffhp import FHP
from robo_reason_reasoning.react import React
from robo_reason_reasoning.cot_sc import CoTSC
from robo_reason_reasoning.always_act import StepAction
from robo_reason_reasoning.self_refine import SelfRefine
from robo_reason_reasoning.tot import TreeOfThought
from robo_reason_reasoning.predicates import Predicates
from robo_reason_reasoning.skills import UR5Skills
from robo_reason_reasoning.extraction_classes import UR5Action

dotenv.load_dotenv()


class EmbodiedAgent:
    """
    Orchestrates Foundation Model-based reasoning for robotic manipulation tasks.

    Args:
        reasoning_mode: One of fhp | ffhp | react | cot_sc | tot | always_act | self_refine
        client_parameters: Dict with at least 'model_name'. Passed to the underlying client.
        client_type: 'llm' (text-only, default) or 'vlm' (vision-language).
        verbose: If True, reasoning methods print intermediate outputs.
    """

    def __init__(
        self,
        reasoning_mode: str,
        client_parameters: dict,
        client_type: str = 'llm',
        verbose: bool = False,
        grounding_mode: str = 'point',
        **kwargs,
    ):
        assert 'model_name' in client_parameters, "client_parameters must include 'model_name'."

        self.verbose = verbose
        self.reasoning_mode = reasoning_mode.lower()
        self.client_type = client_type.lower()
        # VLM-only pixel-grounding format: 'point' or 'bbox' (see reasoning_method.py).
        self.grounding_mode = grounding_mode

        self.predicates = Predicates.get_all_predicates()
        self.skills, self.action_placeholder = UR5Skills.get_embodiment_data(
            use_vlm=(self.client_type == 'vlm'),
            grounding_mode=self.grounding_mode,
        )
        self.eos_placeholder = UR5Skills.get_eos_example()

        self._output = namedtuple('AgentOutput', ['action', 'end_of_simulation'])

        common = dict(
            client_parameters=client_parameters,
            client_type=client_type,
            skills=self.skills,
            action_placeholder=self.action_placeholder,
            verbose=verbose,
            grounding_mode=grounding_mode,
        )

        mode = self.reasoning_mode
        if mode in ('finite_horizon_planning', 'fhp'):
            self.reasoning_method = FHP(reasoning_mode='fhp', predicates=self.predicates, **common)
        elif mode in ('feasible_finite_horizon_planning', 'ffhp'):
            self.reasoning_method = FHP(reasoning_mode='ffhp', predicates=self.predicates, **common)
        elif mode in ('react', 'ra'):
            self.reasoning_method = React(predicates=self.predicates, **common)
        elif mode in ('cot_sc', 'cot-sc', 'csc'):
            self.reasoning_method = CoTSC(k=5, **common)
        elif mode in ('treeofthoughts', 'tot'):
            self.reasoning_method = TreeOfThought(
                eos_placeholder=self.eos_placeholder,
                predicates=self.predicates,
                b=2, k=3, t=20,
                **common,
            )
        elif mode in ('always_act', 'always-act', 'aa'):
            self.reasoning_method = StepAction(**common)
        elif mode in ('self_refine', 'self-refine', 'sr'):
            self.reasoning_method = SelfRefine(
                predicates=self.predicates,
                max_iterations=3,
                **common,
            )
        else:
            raise ValueError(
                f"Unknown reasoning mode: '{reasoning_mode}'. "
                "Supported: fhp, ffhp, react, cot_sc, tot, always_act, self_refine"
            )

    def get_used_tokens(self) -> int:
        if hasattr(self.reasoning_method.client, 'get_total_usage'):
            return self.reasoning_method.client.get_total_usage().get('total_tokens', 0)
        return 0

    def get_detailed_token_usage(self):
        return self.reasoning_method.get_llm_usage_metrics()

    def get_current_step(self) -> int:
        return self.reasoning_method.step_counter

    def get_current_plan(self):
        if hasattr(self.reasoning_method, 'task_plan'):
            return self.reasoning_method.task_plan
        return None

    def step(self, observation: dict, force_replanning: bool = False, **kwargs):
        """
        Perform one reasoning step.

        observation must contain:
          - 'user_request': str
          - 'environment_map': str (scene JSON)  — required for LLM mode
          - 'image': str (file path)             — required for VLM mode

        Returns a namedtuple(action: UR5Action, end_of_simulation: bool).
        """
        assert 'user_request' in observation, "Observation must include 'user_request'."
        if self.client_type != 'vlm':
            assert 'environment_map' in observation, \
                "Observation must include 'environment_map' when using LLM mode."
        else:
            assert 'image' in observation or 'environment_map' in observation, \
                "Observation must include 'image' when using VLM mode."

        action, end_of_simulation = self.reasoning_method(
            **observation,
            verbose=self.verbose,
            force_replan=force_replanning,
        )

        assert isinstance(action, UR5Action), \
            f"Expected UR5Action, got {type(action)}."

        return self._output(action=action, end_of_simulation=end_of_simulation)
