"""Shared planning loop used by both the LLM and VLM planner nodes."""
import logging as _logging

_log = _logging.getLogger(__name__)


def run_plan_loop(agent, observation: dict, max_steps: int = 25) -> list:
    """Step an EmbodiedAgent until end-of-simulation and collect the actions.

    Each non-idle action is serialized via model_dump(exclude_none=True) and
    tagged with a 1-based 'step' index. Returns the list of action dicts.
    """
    steps = []
    for step_idx in range(max_steps):
        result = agent.step(observation=observation)
        action = result.action

        if action.action_name.lower() not in ('idle', 'end_of_simulation'):
            action_dict = action.model_dump(exclude_none=True)
            action_dict['step'] = step_idx + 1
            steps.append(action_dict)

        if result.end_of_simulation:
            break
    else:
        _log.warning("plan_loop exhausted max_steps=%d without end_of_simulation", max_steps)

    return steps
