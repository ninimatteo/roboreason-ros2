"""ReAct reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import ReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_reasoning.EmbodiedAgentsPrompts.react_prompts import ReActPrompts


class React(ReasoningMethod):
    """
    ReAct (Reasoning + Acting): alternates between reasoning thoughts and actions.
    Generates one action per call based on the current environment state.
    """

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm', verbose: bool = False,
                 skills: str = '', action_placeholder: str = '', **kwargs):
        super().__init__(client_parameters=client_parameters, client_type=client_type, **kwargs)
        self.method_name = 'react'
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.verbose = verbose
        self.task_plan = []
        self.user_request = ''
        self.reasoning_thought = 'No reasoning thought yet.'
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def react_step(self, environment_map: str, user_request: str, image=None):
        react_system_message, react_prompt_message = self._select_prompts(ReActPrompts)

        format_args = {
            'user_request': user_request,
            'skills': self.skills,
            'last_reasoning_step': self.reasoning_thought,
            'actions_memory': self.actions_memory,
            'action_placeholder': self.action_placeholder,
        }
        if self.use_vlm:
            format_args['pixels_width'], format_args['pixels_height'] = self._image_pixel_dims(image)
        else:
            format_args['current_env_config'] = environment_map

        msg = react_prompt_message.format(**format_args)

        raw = self._call_client(
            system_message=react_system_message,
            user_message=msg,
            force_json=True,
            image=image
        )

        output = dict(json.loads(self._strip_json_fence(raw)))
        decision = output.get('react_decision')

        if decision == 'reasoning':
            self.reasoning_thought = output.get('reasoning', '')
            action = UR5Action(action_name='wait', time=0.0)
            eos = output.get('end_of_simulation', False)
        elif decision == 'action':
            action = self._build_action(output.get('action', {'action_name': 'move_home'}))
            eos = output.get('end_of_simulation', False)
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
        return self.react_step(
            environment_map=kwargs.get('environment_map', ''),
            user_request=kwargs['user_request'],
            image=kwargs.get('image', None)
        )
