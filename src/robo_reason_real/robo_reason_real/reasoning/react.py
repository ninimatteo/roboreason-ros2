"""ReAct reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_real.reasoning.reasoning_method import LLMReasoningMethod
from robo_reason_real.reasoning.extraction_classes import UR5Action
from robo_reason_real.reasoning.prompts.react_prompts import react_system_message, react_prompt_message
from robo_reason_real.reasoning.llm_client import LLMClient


class React(LLMReasoningMethod):
    """
    ReAct (Reasoning + Acting): alternates between reasoning thoughts and actions.
    Generates one action per call based on the current environment state.
    """

    def __init__(self, llm_parameters: dict = {}, verbose: bool = False,
                 skills: str = '', action_placeholder: str = '', **kwargs):
        super().__init__()
        self.method_name = 'react'
        self.llm = LLMClient(**llm_parameters)
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.verbose = verbose
        self.task_plan = []
        self.user_request = ''
        self.reasoning_thought = 'No reasoning thought yet.'
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def get_llm_usage_metrics(self):
        return self.llm.usage_metrics

    def react_step(self, environment_map: str, user_request: str):
        msg = react_prompt_message.format(
            user_request=user_request,
            current_env_config=environment_map,
            skills=self.skills,
            last_reasoning_step=self.reasoning_thought,
            actions_memory=self.actions_memory,
            action_placeholder=self.action_placeholder,
        )

        raw = self.llm(
            system_message=react_system_message,
            user_message=msg,
            force_json=True,
        )

        output = dict(json.loads(raw))
        decision = output.get('react_decision')

        if decision == 'reasoning':
            self.reasoning_thought = output.get('reasoning', '')
            action = UR5Action(action_name='wait', time=0.0)
            eos = output.get('end of simulation', False)
        elif decision == 'action':
            action = UR5Action(**output.get('action', {'action_name': 'move_home'}))
            eos = output.get('end of simulation', False)
        else:
            action = UR5Action(action_name='move_home')
            eos = True

        self.task_plan.append(action)
        self._update_step_counter()
        self._update_actions_memory(self.step_counter, action)
        self._verbose_print(f'Action: {action}')
        self._verbose_print(f'EoS: {eos}')

        return self._output(action=action, end_of_simulation=eos)

    def __call__(self, **kwargs):
        assert 'user_request' in kwargs
        assert 'environment_map' in kwargs
        return self.react_step(
            environment_map=kwargs['environment_map'],
            user_request=kwargs['user_request'],
        )
