"""Always-Act (StepAction) reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import LLMReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_prompts.always_act_prompts import step_action_prompt
from robo_reason_reasoning.llm_client import LLMClient


class StepAction(LLMReasoningMethod):
    """
    Always-Act: generates one action per step without caching a plan.
    Suitable for reactive/online execution where environment feedback is available.
    """

    def __init__(self, llm_parameters: dict = {}, verbose: bool = False,
                 skills: str = '', action_placeholder: str = '', **kwargs):
        super().__init__()
        self.method_name = 'always-act'
        self.llm = LLMClient(**llm_parameters)
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.verbose = verbose
        self.user_request = ''
        self.task_plan = []
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def get_llm_usage_metrics(self):
        return self.llm.usage_metrics

    def step_action(self, environment_map: str, user_request: str):
        msg = step_action_prompt.format(
            environment_map=environment_map,
            user_request=user_request,
            skills=self.skills,
            action_placeholder=self.action_placeholder,
            actions_memory=self.actions_memory,
        )

        raw = self.llm(
            system_message="You are an expert in embodied reasoning and decision-making.",
            user_message=msg,
            force_json=True,
        )

        output = dict(json.loads(raw))
        action_dict = output.get('action', {'action_name': 'move_home'})
        action = UR5Action(**action_dict)
        eos = output.get('end_of_simulation', False)

        self.task_plan.append(action)
        self._update_step_counter()
        self._update_actions_memory(self.step_counter, action)
        self._verbose_print(f'Action: {action}')
        self._verbose_print(f'EoS: {eos}')

        return self._output(action=action, end_of_simulation=eos)

    def __call__(self, **kwargs):
        assert 'user_request' in kwargs
        assert 'environment_map' in kwargs
        return self.step_action(
            environment_map=kwargs['environment_map'],
            user_request=kwargs['user_request'],
        )
