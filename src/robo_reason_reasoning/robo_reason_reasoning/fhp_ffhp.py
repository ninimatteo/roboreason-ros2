"""FHP / FFHP reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import LLMReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_prompts.fhp_ffhp_prompts import (
    fhp_ffhp_system_message, task_planning_prompt
)
from robo_reason_prompts.predicates_prompts import predicates_prompt
from robo_reason_reasoning.llm_client import LLMClient


class FHP(LLMReasoningMethod):
    """
    Finite Horizon Planning (FHP) and Feasible FHP (FFHP).
    Generates the full plan upfront; returns one action per call.
    """

    def __init__(self, llm_parameters: dict = {}, reasoning_mode: str = 'fhp',
                 predicates: str = '', skills: str = '',
                 action_placeholder: str = '', **kwargs):

        assert reasoning_mode in ('fhp', 'ffhp'), f"reasoning_mode must be 'fhp' or 'ffhp'."
        self.reasoning_mode = reasoning_mode
        self.method_name = reasoning_mode
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.predicates = predicates
        self.verbose = kwargs.get('verbose', False)
        self.llm = LLMClient(**llm_parameters)
        self.task_plan = []
        self.user_request = ''
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def get_llm_usage_metrics(self):
        return self.llm.usage_metrics

    def predict_predicates(self, environment_map: str) -> str:
        msg = predicates_prompt.format(
            predicates_library=self.predicates,
            environment_description=json.dumps(environment_map),
        )
        return self.llm(user_message=msg, system_message=fhp_ffhp_system_message, force_json=True)

    def plan_task(self, env_config: str, predicates: str) -> list:
        msg = task_planning_prompt.format(
            skills=self.skills,
            action_placeholder1=self.action_placeholder,
            action_placeholder2=self.action_placeholder,
            user_request=self.user_request,
            current_env_config=env_config,
            current_predicates=predicates,
        )
        raw = self.llm(user_message=msg, system_message=fhp_ffhp_system_message, force_json=True)
        return json.loads(raw)['plan']

    def __call__(self, force_replan: bool = False, **kwargs):
        assert 'user_request' in kwargs
        assert 'environment_map' in kwargs

        env_map = kwargs['environment_map']
        user_req = kwargs['user_request']

        needs_plan = (
            (user_req != self.user_request and len(self.task_plan) == 0)
            or (force_replan and self.reasoning_mode == 'ffhp')
        )

        if needs_plan:
            self.set_user_request(user_req)
            preds = self.predict_predicates(env_map)
            self.task_plan = self.plan_task(env_map, preds)
            self._verbose_print('Generated plan', self.task_plan)

        if self.task_plan:
            action = UR5Action(**self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home'),
            end_of_simulation=True,
        )
