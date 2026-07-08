"""Chain-of-Thought Self-Consistency (CoT-SC) — adapted for UR5 from RoboReason-Lab."""
import json
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor

from robo_reason_reasoning.reasoning_method import ReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_reasoning.EmbodiedAgentsPrompts.cot_sc_prompts import CotScPrompts


class CoTSC(ReasoningMethod):
    """
    CoT-SC: generates K independent plans, selects the most consistent one.
    Returns one action per call from the selected plan.
    """

    def __init__(self, client_parameters: dict = None, client_type: str = 'llm', skills: str = '', action_placeholder: str = '',
                 verbose: bool = False, k: int = 5, **kwargs):
        super().__init__(client_parameters=client_parameters, client_type=client_type, **kwargs)
        assert k >= 2, "K must be >= 2."
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

    def generate_plan(self, environment_map: str, user_request: str, image=None) -> list:
        plan_prompt = self._select_prompts(CotScPrompts)
        
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

        msg = plan_prompt.format(**format_args)
        resp = self._call_client(
            system_message="You are a planning agent. Generate plans based on the user's request and environment.",
            user_message=msg,
            force_json=True,
            image=image
        )
        try:
            parsed = json.loads(self._extract_json(resp))
            # LLMs sometimes return the plan as a bare array instead of
            # {"plan": [...]}.  Accept both shapes.
            if isinstance(parsed, list):
                return parsed
            return parsed.get('plan', [])
        except (ValueError, json.JSONDecodeError, AttributeError):
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

        env_map = kwargs.get('environment_map', '')
        user_req = kwargs['user_request']
        image = kwargs.get('image', None)

        if (user_req != self.user_request and not self.task_plan) or force_replan:
            self.set_user_request(user_req)
            # The k samples are independent LLM/VLM calls — run them concurrently
            # instead of sequentially. Each call is a blocking HTTP request that
            # releases the GIL while waiting on the network, so a thread pool
            # turns the total wall-clock cost from ~k x (one call) into ~1 x
            # (the slowest call), which is what keeps CoT-SC's latency in the
            # same ballpark as single-call methods like FHP instead of a
            # multiple of it.
            with ThreadPoolExecutor(max_workers=self.k) as pool:
                plans = list(pool.map(
                    lambda _: self.generate_plan(env_map, user_req, image=image),
                    range(self.k),
                ))

            if not any(plans):
                # Every one of the k samples failed to parse as JSON — almost
                # always the model exhausting its token budget on internal
                # reasoning before emitting the final plan (see max_tokens /
                # REQUEST_TIMEOUT_S in config.py), not a legitimate "no
                # actions needed" answer. Raise instead of silently falling
                # through to the move_home fallback below, which used to
                # masquerade a total generation failure as a confident,
                # successful plan.
                raise RuntimeError(
                    f"CoT-SC: all {self.k} sampled plans for \"{user_req}\" came back "
                    "empty (none parsed as valid JSON). The model likely ran out of "
                    "its max_tokens budget mid-reasoning before emitting the final "
                    "plan — try raising max_tokens/REQUEST_TIMEOUT_S or a less "
                    "reasoning-heavy model."
                )

            most_common = self.aggregate_plans(plans)
            self.task_plan = self.select_consistent_plan(plans, most_common)
            self._verbose_print('CoT-SC trace', {
                'generated_plans': plans,
                'most_common_actions': most_common,
                'selected_plan': self.task_plan,
            })

        if self.task_plan:
            action = self._build_action(self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home', score=1.0),
            end_of_simulation=True,
        )
