"""Chain-of-Thought Self-Consistency (CoT-SC) — adapted for UR5 from RoboReason-Lab."""
import json
from collections import Counter, namedtuple

from robo_reason_real.reasoning.reasoning_method import LLMReasoningMethod
from robo_reason_real.reasoning.extraction_classes import UR5Action
from robo_reason_real.reasoning.prompts.cot_sc_prompts import plan_prompt
from robo_reason_real.reasoning.llm_client import LLMClient


class CoTSC(LLMReasoningMethod):
    """
    CoT-SC: generates K independent plans, selects the most consistent one.
    Returns one action per call from the selected plan.
    """

    def __init__(self, skills: str = '', action_placeholder: str = '',
                 verbose: bool = False, llm_parameters: dict = {},
                 k: int = 5, **kwargs):
        assert k >= 2, "K must be >= 2."
        self.llm = LLMClient(**llm_parameters)
        self.method_name = 'cot-sc'
        self.k = k
        self.user_request = ''
        self.verbose = verbose
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.sc_plan_ranking = {}
        self.task_plan = []
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])

    def set_user_request(self, user_request: str):
        self._verbose_print(f"Setting user request: {user_request}")
        self.user_request = user_request

    def get_llm_usage_metrics(self):
        return self.llm.usage_metrics

    def generate_plan(self, environment_map: str, user_request: str) -> list:
        msg = plan_prompt.format(
            environment_map=environment_map,
            skills=self.skills,
            user_request=user_request,
            action_placeholder1=self.action_placeholder,
            action_placeholder2=self.action_placeholder,
        )
        resp = self.llm(
            system_message="You are a planning agent. Generate plans based on the user's request and environment.",
            user_message=msg,
            force_json=True,
        )
        try:
            return json.loads(resp).get('plan', [])
        except json.JSONDecodeError:
            return []

    def aggregate_plans(self, plans: list) -> list:
        all_actions = [json.dumps(a, sort_keys=True) for plan in plans for a in plan]
        avg_len = max(1, int(len(all_actions) / self.k))
        counts = Counter(all_actions)
        return [json.loads(a) for a, _ in counts.most_common(avg_len)]

    def select_consistent_plan(self, plans: list, most_common: list) -> list:
        best_plan, best_score = None, -1
        for idx, plan in enumerate(plans):
            score = sum(1 for a in plan if a in most_common)
            self.sc_plan_ranking[idx] = score
            if score > best_score:
                best_score = score
                best_plan = plan
        return best_plan or []

    def __call__(self, force_replan: bool = False, **kwargs):
        assert 'user_request' in kwargs
        assert 'environment_map' in kwargs

        env_map = kwargs['environment_map']
        user_req = kwargs['user_request']

        if (user_req != self.user_request and not self.task_plan) or force_replan:
            self.set_user_request(user_req)
            plans = [self.generate_plan(env_map, user_req) for _ in range(self.k)]
            most_common = self.aggregate_plans(plans)
            self.task_plan = self.select_consistent_plan(plans, most_common)
            self._verbose_print('CoT-SC trace', {
                'generated_plans': plans,
                'most_common_actions': most_common,
                'selected_plan': self.task_plan,
            })

        if self.task_plan:
            action = UR5Action(**self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home', score=1.0),
            end_of_simulation=True,
        )
