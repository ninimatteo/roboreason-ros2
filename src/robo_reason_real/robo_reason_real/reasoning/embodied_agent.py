"""
EmbodiedAgent — adapted for UR5 real robot from RoboReason-Lab.

Changes from original:
- Skill set: approach/pick/release/move_home/wait (replaces Navigate/Pick/Place/…)
- Action type: UR5Action (replaces Action/SymbolicAction)
- No symbolic mode (always coordinate-based for real robot)
- Import paths use robo_reason_real.reasoning.*
"""
from collections import namedtuple

from robo_reason_real.reasoning.fhp_ffhp import FHP
from robo_reason_real.reasoning.react import React
from robo_reason_real.reasoning.cot_sc import CoTSC
from robo_reason_real.reasoning.always_act import StepAction
from robo_reason_real.reasoning.self_refine import SelfRefine
from robo_reason_real.reasoning.tot import TreeOfThought
from robo_reason_real.reasoning.predicates import Predicates
from robo_reason_real.reasoning.skills import UR5Skills
from robo_reason_real.reasoning.extraction_classes import UR5Action


class EmbodiedAgent:
    """
    Orchestrates LLM-based reasoning for UR5 manipulation tasks.

    Supported reasoning modes:
      fhp, ffhp, react, cot_sc, tot, always_act, self_refine
    """

    def __init__(self, reasoning_mode: str, llm_parameters: dict,
                 verbose: bool = False, **kwargs):

        self.verbose = verbose
        self.reasoning_mode = reasoning_mode.lower()
        self.instance_name = kwargs.get("instance_name", "EmbodiedAgent")

        self.predicates = Predicates.get_all_predicates()
        self.skills, self.action_placeholder = UR5Skills.get_embodiment_data()
        self.eos_placeholder = UR5Skills.get_eos_example()

        self.llm_parameters = llm_parameters
        assert 'model_name' in llm_parameters, "llm_parameters must include 'model_name'."

        self.step_counter = 0
        self._output = namedtuple('AgentOutput', ['action', 'end_of_simulation'])

        mode = self.reasoning_mode
        common = dict(
            llm_parameters=llm_parameters,
            skills=self.skills,
            action_placeholder=self.action_placeholder,
            verbose=verbose,
        )

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
                use_iid_evaluation=True,
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

    # -------------------------------------------------------------------------

    def get_used_tokens(self) -> int:
        return self.reasoning_method.llm.get_total_used_tokens()

    def get_detailed_token_usage(self):
        return self.reasoning_method.llm.usage_metrics

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
          - 'environment_map': str (scene JSON or description)

        Returns a namedtuple(action: UR5Action, end_of_simulation: bool).
        """
        assert all(k in observation for k in ['user_request', 'environment_map']), \
            "Observation must include 'user_request' and 'environment_map'."

        print(f"\n({self.instance_name}) Thinking...\n")

        action, end_of_simulation = self.reasoning_method(
            **observation,
            verbose=self.verbose,
            force_replanning=force_replanning,
        )

        assert isinstance(action, UR5Action), \
            f"Expected UR5Action, got {type(action)}."

        return self._output(action=action, end_of_simulation=end_of_simulation)
