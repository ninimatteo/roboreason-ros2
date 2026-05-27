"""Self-Refine reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from robo_reason_reasoning.reasoning_method import LLMReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_prompts.self_refine_prompts import (
    initial_solution_prompt, feedback_prompt, refinement_prompt
)
from robo_reason_reasoning.llm_client import LLMClient


class SelfRefine(LLMReasoningMethod):
    """
    Self-Refine: generates an initial plan then iteratively refines it via LLM feedback.
    Returns one action per call from the final refined plan.
    """

    def __init__(self, llm_parameters: dict = {}, verbose: bool = False,
                 skills: str = '', action_placeholder: str = '',
                 max_iterations: int = 3, **kwargs):
        self.method_name = 'self_refine'
        self.llm = LLMClient(**llm_parameters)
        self.max_iterations = max_iterations
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

    def generate_initial_solution(self, environment_map: str, user_request: str) -> str:
        msg = initial_solution_prompt.format(
            skills=self.skills,
            environment_map=environment_map,
            user_request=user_request,
            action_placeholder1=self.action_placeholder,
            action_placeholder2=self.action_placeholder,
        )
        return self.llm(
            user_message=msg,
            system_message="You are a planning agent. Generate plans based on the user's request.",
            temperature=0.7,
            force_json=True,
        ).strip()

    def generate_feedback(self, solution: str, environment_map: str, user_request: str) -> str:
        msg = feedback_prompt.format(
            skills=self.skills,
            environment_map=environment_map,
            user_request=user_request,
            solution=solution,
        )
        return self.llm(
            user_message=msg,
            system_message="You are an expert evaluator for robotic planning.",
            temperature=0.3,
            force_json=True,
        ).strip()

    def refine_solution(self, environment_map: str, user_request: str,
                        initial_solution: str, current_solution: str,
                        all_feedback: list, current_feedback: str) -> str:
        msg = refinement_prompt.format(
            skills=self.skills,
            environment_map=environment_map,
            user_request=user_request,
            initial_solution=initial_solution,
            current_solution=current_solution,
            feedback_history='\n'.join(f'Feedback {i+1}: {fb}' for i, fb in enumerate(all_feedback)),
            current_feedback=current_feedback,
            action_placeholder1=self.action_placeholder,
            action_placeholder2=self.action_placeholder,
        )
        return self.llm(
            user_message=msg,
            system_message="You are a planning agent that refines plans based on feedback.",
            temperature=0.5,
            force_json=True,
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

    def generate_self_refined_solution(self, environment_map: str, user_request: str) -> list:
        y0 = self.generate_initial_solution(environment_map, user_request)
        current = y0
        all_feedback = []

        self._verbose_print('Initial solution', {'solution': y0})

        for t in range(self.max_iterations):
            feedback = self.generate_feedback(current, environment_map, user_request)
            all_feedback.append(feedback)

            if self.stop_condition(feedback, t):
                self._verbose_print(f'Stop at iteration {t+1}', {'feedback': feedback})
                break

            current = self.refine_solution(
                environment_map, user_request, y0, current, all_feedback, feedback
            )
            self._verbose_print(f'Iteration {t+1} refined', {'solution': current})

        try:
            return json.loads(current).get('plan', [])
        except json.JSONDecodeError:
            return []

    def __call__(self, force_replan: bool = False, **kwargs):
        assert 'user_request' in kwargs
        assert 'environment_map' in kwargs

        env_map = kwargs['environment_map']
        user_req = kwargs['user_request']

        if force_replan or (user_req != self.user_request and not self.task_plan):
            self.set_user_request(user_req)
            self.task_plan = self.generate_self_refined_solution(env_map, user_req)

        if self.task_plan:
            action = UR5Action(**self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home', score=0.0),
            end_of_simulation=True,
        )
