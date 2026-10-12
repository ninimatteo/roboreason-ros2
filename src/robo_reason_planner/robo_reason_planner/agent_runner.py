"""Shared planning loop used by both the LLM and VLM planner nodes."""
import logging as _logging

from robo_reason_planner.planning_cancel import PlanningCancelled

_log = _logging.getLogger(__name__)


def run_plan_loop(agent, observation: dict, max_steps: int = 25, cancelled=None) -> list:
    """Step an EmbodiedAgent until end-of-simulation and collect the actions.

    Each non-idle action is serialized via model_dump(exclude_none=True) and
    tagged with a 1-based 'step' index. Returns the list of action dicts.

    ``cancelled`` (a threading.Event, see planning_cancel) is checked before
    every step; once set, PlanningCancelled is raised instead of calling the
    model again.
    """
    steps = []
    for step_idx in range(max_steps):
        if cancelled is not None and cancelled.is_set():
            raise PlanningCancelled(f'planning cancelled by user after {step_idx} step(s)')
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
