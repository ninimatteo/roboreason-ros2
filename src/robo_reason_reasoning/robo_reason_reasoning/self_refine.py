"""Self-Refine reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import ReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action  
from robo_reason_reasoning.EmbodiedAgentsPrompts.self_refine_prompts import SelfRefinePrompts


class SelfRefine(ReasoningMethod):
    """
    Self-Refine: generates an initial plan then iteratively refines it via LLM feedback.
    Returns one action per call from the final refined plan.
    """

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm', verbose: bool = False,
                 skills: str = '', action_placeholder: str = '',
                 max_iterations: int = 3, **kwargs):
        super().__init__(client_parameters=client_parameters, client_type=client_type, **kwargs)
        self.method_name = 'self_refine'
        self.max_iterations = max_iterations
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.verbose = verbose
        self.user_request = ''
        self.task_plan = []
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def generate_initial_solution(self, environment_map: str, user_request: str, image=None) -> str:
        initial_solution_prompt, _, _ = self._select_prompts(SelfRefinePrompts)

        format_args = {
            'skills': self.skills,
            'user_request': user_request,
            'action_placeholder1': self.action_placeholder,
            'action_placeholder2': self.action_placeholder,
        }
        if self.use_vlm:
            format_args['pixels_width'], format_args['pixels_height'] = self._image_pixel_dims(image)
        else:
            format_args['environment_map'] = environment_map

        msg = initial_solution_prompt.format(**format_args)
        return self._call_client(
            user_message=msg,
            system_message="You are a planning agent. Generate plans based on the user's request.",
            temperature=0.7,
            force_json=True,
            image=image
        ).strip()

    def generate_feedback(self, solution: str, environment_map: str, user_request: str, image=None) -> str:
        _, feedback_prompt, _ = self._select_prompts(SelfRefinePrompts)

        format_args = {
            'skills': self.skills,
            'user_request': user_request,
            'solution': solution,
        }
        if not self.use_vlm:
            format_args['environment_map'] = environment_map

        msg = feedback_prompt.format(**format_args)
        return self._call_client(
            user_message=msg,
            system_message="You are an expert evaluator for robotic planning.",
            temperature=0.3,
            force_json=True,
            image=image
        ).strip()

    def refine_solution(self, environment_map: str, user_request: str,
                        initial_solution: str, current_solution: str,
                        all_feedback: list, current_feedback: str, image=None) -> str:
        _, _, refinement_prompt = self._select_prompts(SelfRefinePrompts)

        format_args = {
            'skills': self.skills,
            'user_request': user_request,
            'initial_solution': initial_solution,
            'current_solution': current_solution,
            'feedback_history': '\n'.join(f'Feedback {i+1}: {fb}' for i, fb in enumerate(all_feedback)),
            'current_feedback': current_feedback,
            'action_placeholder1': self.action_placeholder,
            'action_placeholder2': self.action_placeholder,
        }
        if self.use_vlm:
            format_args['pixels_width'], format_args['pixels_height'] = self._image_pixel_dims(image)
        else:
            format_args['environment_map'] = environment_map

        msg = refinement_prompt.format(**format_args)
        return self._call_client(
            user_message=msg,
            system_message="You are a planning agent that refines plans based on feedback.",
            temperature=0.5,
            force_json=True,
            image=image
        ).strip()

    def stop_condition(self, feedback: str, iteration: int) -> bool:
        if iteration >= self.max_iterations:
            return True
        try:
            if json.loads(feedback).get('is_satisfactory', False):
                return True
        except (json.JSONDecodeError, KeyError):
            pass
        return False

    def generate_self_refined_solution(self, environment_map: str, user_request: str, image=None) -> list:
        y0 = self.generate_initial_solution(environment_map, user_request, image=image)
        current = y0
        all_feedback = []

        self._verbose_print('Initial solution', {'solution': y0})

        for t in range(self.max_iterations):
            feedback = self.generate_feedback(current, environment_map, user_request, image=image)
            all_feedback.append(feedback)

            if self.stop_condition(feedback, t):
                self._verbose_print(f'Stop at iteration {t+1}', {'feedback': feedback})
                break

            current = self.refine_solution(
                environment_map=environment_map,
                user_request=user_request,
                initial_solution=y0,
                current_solution=current,
                all_feedback=all_feedback,
                current_feedback=feedback,
                image=image
            )
            self._verbose_print(f'Iteration {t+1} refined', {'solution': current})

        try:
            return json.loads(current).get('plan', [])
        except json.JSONDecodeError:
            return []

    def __call__(self, force_replan: bool = False, **kwargs):
        assert 'user_request' in kwargs

        env_map = kwargs.get('environment_map', '')
        user_req = kwargs['user_request']
        image = kwargs.get('image', None)

        if force_replan or (user_req != self.user_request and not self.task_plan):
            self.set_user_request(user_req)
            self.task_plan = self.generate_self_refined_solution(env_map, user_req, image=image)

        if self.task_plan:
            action = UR5Action(**self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home', score=0.0),
            end_of_simulation=True,
        )
