"""Tree of Thoughts (ToT) reasoning method — adapted for UR5 from RoboReason-Lab."""
import json
from collections import namedtuple

from treelib import Tree

from robo_reason_reasoning.reasoning_method import LLMReasoningMethod
from robo_reason_reasoning.extraction_classes import UR5Action
from robo_reason_prompts.tot_prompts import (
    plan_generation_prompt, action_generation_prompt,
    thought_evaluation_prompt, thoughts_evaluation_in_batch_prompt,
    thought_sorting_prompt,
)
from robo_reason_reasoning.llm_client import LLMClient


class TreeOfThought(LLMReasoningMethod):
    """
    ToT: explores a tree of plan candidates and selects the best one.
    Returns one action per call from the selected plan.
    """

    def __init__(self, llm_parameters: dict = {}, verbose: bool = False,
                 skills: str = '', action_placeholder: str = '',
                 eos_placeholder: str = '', k: int = 3, b: int = 2, t: int = 10,
                 use_iid_evaluation: bool = True, **kwargs):

        self.method_name = 'tot'
        self.llm = LLMClient(**llm_parameters)
        self.k = k
        self.b = b
        self.t = t
        self.skills = skills
        self.action_placeholder = action_placeholder
        self.eos_placeholder = eos_placeholder
        self.verbose = verbose
        self.use_iid_evaluation = use_iid_evaluation
        self.task_plan = []
        self.thoughts_tree = None
        self.user_request = ''
        self._output = namedtuple('ReasoningOutput', ['action', 'end_of_simulation'])
        self._scores_map = {
            'insufficient': 1, 'poor': 2, 'fair': 3, 'good': 4, 'excellent': 5
        }

    def set_user_request(self, user_request: str):
        self.user_request = user_request

    def get_llm_usage_metrics(self):
        return self.llm.usage_metrics

    # -------------------------------------------------------------------------

    def _generate_action_thought(self, environment_map: str, user_request: str,
                                  previous_thought: str, num_actions: int) -> list:
        msg = action_generation_prompt.format(
            environment_map=environment_map,
            skills=self.skills,
            user_request=user_request,
            previous_thought=previous_thought,
            num_actions=num_actions,
            action_placeholder1=self.action_placeholder,
            eos_action_placeholder=self.eos_placeholder,
        )
        resp = self.llm(
            user_message=msg,
            system_message="You are a planning agent.",
            temperature=1.5, top_p=0.4, force_json=True,
        )
        return json.loads(resp.strip()).get('sampled_actions', [])

    def _evaluate_thought(self, thought, environment_map: str, user_request: str) -> int:
        msg = thought_evaluation_prompt.format(
            environment_map=environment_map,
            skills=self.skills,
            user_request=user_request,
            thought=thought,
        )
        resp = self.llm(
            user_message=msg,
            system_message="You are an expert evaluator for robotic planning.",
            temperature=0.0, force_json=True,
        )
        score = 0
        data = json.loads(resp)
        for key in ('user_request_consistency', 'environment_feasibility', 'embodiment_feasibility'):
            score += self._scores_map.get(data.get(key, '').strip().lower(), 0)
        return score

    def _evaluate_thoughts_batch(self, plans: dict, environment_map: str,
                                  user_request: str) -> dict:
        msg = thoughts_evaluation_in_batch_prompt.format(
            environment_map=environment_map,
            user_request=user_request,
            skills=self.skills,
            thoughts=json.dumps(list(plans.values()), indent=2),
        )
        resp = self.llm(
            user_message=msg,
            system_message="You are an expert judge for robotic planning.",
            temperature=0.0, force_json=True,
        )
        evals = json.loads(resp).get('scores', [])
        assert len(evals) == len(plans), f"Eval count mismatch: {len(evals)} vs {len(plans)}"
        scores = {}
        for idx, ev in enumerate(evals):
            key = list(plans.keys())[idx]
            scores[key] = sum(self._scores_map.get(v.strip().lower(), 0) for v in ev.values())
        return scores

    def _retrieve_chain(self, thought_id: str, tree: Tree, db: dict) -> list:
        chain = [db[thought_id]]
        curr = thought_id
        while curr != '0-0':
            node = tree.get_node(curr)
            if node and node.predecessor(tree.identifier):
                curr = node.predecessor(tree.identifier)
                if curr == '0-0':
                    break
                chain.append(db[curr])
            else:
                break
        chain.reverse()
        return chain

    def generate_tree_based_solution(self, environment_map: str, user_request: str):
        tree = Tree()
        tree.create_node("Tree of Thoughts", "0-0")
        db = {}
        best_ids = ['none']

        for iteration in range(self.t):
            for idx, parent_id in enumerate(best_ids):
                chain = (
                    "You are producing the very first thought."
                    if parent_id == 'none'
                    else self._retrieve_chain(parent_id, tree, db)
                )
                if parent_id == 'none':
                    best_ids = []

                new_thoughts = self._generate_action_thought(
                    environment_map, user_request, chain, self.k
                )
                for i, thought in enumerate(new_thoughts):
                    tid = f'{iteration+1}-{self.k * idx + i + 1}'
                    db[tid] = thought
                    parent = parent_id if parent_id != 'none' else '0-0'
                    tree.create_node(tid, tid, parent=parent)

                self.thoughts_tree = tree

            if self.use_iid_evaluation:
                evaluated = []
                for tid in db:
                    if tid.startswith(f'{iteration+1}-'):
                        chain = self._retrieve_chain(tid, tree, db)
                        score = self._evaluate_thought(chain, environment_map, user_request)
                        evaluated.append((tid, score))
            else:
                plans = {
                    tid: self._retrieve_chain(tid, tree, db)
                    for tid in db if tid.startswith(f'{iteration+1}-')
                }
                scores = self._evaluate_thoughts_batch(plans, environment_map, user_request)
                evaluated = list(scores.items())

            evaluated.sort(key=lambda x: x[1], reverse=True)
            best_ids = [e[0] for e in evaluated[:self.b]]

            # Early exit if EoS action found
            for tid in best_ids:
                if isinstance(db[tid], dict) and db[tid].get('action_name', '').lower() in ('idle', 'move_home'):
                    chain = self._retrieve_chain(tid, tree, db)
                    return chain[:-1], tree

            self._verbose_print(f'Iteration {iteration+1} best', {'best_ids': best_ids})

        chain = self._retrieve_chain(best_ids[0], tree, db)
        return chain, tree

    def __call__(self, force_replan: bool = False, verbose: bool = False, **kwargs):
        assert 'user_request' in kwargs
        assert 'environment_map' in kwargs

        env_map = kwargs['environment_map']
        user_req = kwargs['user_request']

        if force_replan or (user_req != self.user_request and not self.task_plan):
            self.set_user_request(user_req)
            self.task_plan, self.thoughts_tree = self.generate_tree_based_solution(env_map, user_req)
            self._verbose_print('ToT final plan', {'plan': self.task_plan})

        if self.task_plan:
            action = UR5Action(**self.task_plan[0])
            self.task_plan = self.task_plan[1:]
            return self._output(action=action, end_of_simulation=False)

        return self._output(
            action=UR5Action(action_name='move_home'),
            end_of_simulation=True,
        )
