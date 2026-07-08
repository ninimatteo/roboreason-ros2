"""Always-Act (StepAction) reasoning method — adapted for UR5 from RoboReason-Lab."""
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import ReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_reasoning.EmbodiedAgentsPrompts.always_act_prompts import AlwaysActPrompts


class StepAction(ReasoningMethod):
    """
    Always-Act: generates one action per step without caching a plan.
    Suitable for reactive/online execution where environment feedback is available.
    """

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm', verbose: bool = False,
                 skills: str = '', action_placeholder: str = '', **kwargs):
        super().__init__(client_parameters=client_parameters, client_type=client_type, **kwargs)
        self.method_name = 'always-act'
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.verbose = verbose
        self.user_request = ''
        self.task_plan = []
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def step_action(self, environment_map: str, user_request: str, image=None):
        step_action_prompt = self._select_prompts(AlwaysActPrompts)
        
        format_args = {
            'user_request': user_request,
            'skills': self.skills,
            'action_placeholder': self.action_placeholder,
            'actions_memory': self.actions_memory,
        }
        if self.use_vlm:
            format_args['pixels_width'], format_args['pixels_height'] = self._image_pixel_dims(image)
        else:
            format_args['environment_map'] = environment_map

        msg = step_action_prompt.format(**format_args)

        raw = self._call_client(
            system_message="You are an expert in embodied reasoning and decision-making.",
            user_message=msg,
            force_json=True,
            image=image
        )

        output = dict(self._parse_json_response(raw, context='step_action'))
        action_dict = output.get('action', {'action_name': 'move_home'})
        action = self._build_action(action_dict)
        eos = output.get('end_of_simulation', False)

        self.task_plan.append(action)
        self._update_step_counter()
        self._update_actions_memory(self.step_counter, action)
        self._verbose_print(f'Action: {action}')
        self._verbose_print(f'EoS: {eos}')

        return self._output(action=action, end_of_simulation=eos)

    def __call__(self, **kwargs):
        assert 'user_request' in kwargs
        return self.step_action(
            environment_map=kwargs.get('environment_map', ''),
            user_request=kwargs['user_request'],
            image=kwargs.get('image', None)
        )
