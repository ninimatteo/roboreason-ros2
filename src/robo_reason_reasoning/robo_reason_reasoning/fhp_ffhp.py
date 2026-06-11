"""FHP / FFHP reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import ReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
# pyrefly: ignore [missing-import]
from robo_reason_prompts.fhp_ffhp_prompts import FHP_FFHP_Prompts
# pyrefly: ignore [missing-import]
from robo_reason_prompts.predicates_prompts import PredicatesPrompts


class FHP(ReasoningMethod):
    """
    Finite Horizon Planning (FHP) and Feasible FHP (FFHP).
    Generates the full plan upfront; returns one action per call.
    """

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm', reasoning_mode: str = 'fhp',
                 predicates: str = '', skills: str = '',
                 action_placeholder: str = '', **kwargs):
        super().__init__(client_parameters=client_parameters, client_type=client_type, **kwargs)
        assert reasoning_mode in ('fhp', 'ffhp'), f"reasoning_mode must be 'fhp' or 'ffhp'."
        self.reasoning_mode = reasoning_mode
        self.method_name = reasoning_mode
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.predicates = predicates
        self.verbose = kwargs.get('verbose', False)
        self.task_plan = []
        self.user_request = ''
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def predict_predicates(self, environment_map: str, image=None) -> str:
        predicates_prompt, _ = PredicatesPrompts.get_prompts(use_vlm=self.use_vlm)
        fhp_ffhp_system_message, _ = FHP_FFHP_Prompts.get_prompts(use_vlm=self.use_vlm)
        
        format_args = {
            'predicates_library': self.predicates,
        }
        if not self.use_vlm:
            format_args['environment_description'] = json.dumps(environment_map)
            
        msg = predicates_prompt.format(**format_args)
        return self._call_client(
            user_message=msg, 
            system_message=fhp_ffhp_system_message, 
            force_json=True, 
            image=image
        )

    def plan_task(self, env_config: str, predicates: str, image=None) -> list:
        fhp_ffhp_system_message, task_planning_prompt = FHP_FFHP_Prompts.get_prompts(
            use_vlm=self.use_vlm
        )
        
        format_args = {
            'skills': self.skills,
            'action_placeholder1': self.action_placeholder,
            'action_placeholder2': self.action_placeholder,
            'user_request': self.user_request,
            'current_predicates': predicates,
        }
        if not self.use_vlm:
            format_args['current_env_config'] = env_config

        msg = task_planning_prompt.format(**format_args)
        raw = self._call_client(
            user_message=msg, 
            system_message=fhp_ffhp_system_message, 
            force_json=True, 
            image=image
        )
        return json.loads(raw)['plan']

    def __call__(self, force_replan: bool = False, **kwargs):
        assert 'user_request' in kwargs

        env_map = kwargs.get('environment_map', '')
        user_req = kwargs['user_request']
        image = kwargs.get('image', None)

        needs_plan = (
            (user_req != self.user_request and len(self.task_plan) == 0)
            or (force_replan and self.reasoning_mode == 'ffhp')
        )

        if needs_plan:
            self.set_user_request(user_req)
            preds = self.predict_predicates(env_map, image=image)
            self.task_plan = self.plan_task(env_map, preds, image=image)
            self._verbose_print('Generated plan', self.task_plan)

        if self.task_plan:
            action = UR5Action(**self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home'),
            end_of_simulation=True,
        )
