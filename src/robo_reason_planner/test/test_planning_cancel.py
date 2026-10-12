"""Tests for cancelling a plan request while it is still planning (ROBOAI-34)."""
from types import SimpleNamespace

import pytest

from robo_reason_planner.agent_runner import run_plan_loop
from robo_reason_planner.planning_cancel import PlanningCancellation, PlanningCancelled


def _step(name, end=False):
    return SimpleNamespace(action=SimpleNamespace(
        action_name=name, model_dump=lambda exclude_none=True: {'action_name': name}),
        end_of_simulation=end)


def _fake_node():
    services = {}
    logger = SimpleNamespace(warn=lambda m: None)
    node = SimpleNamespace(
        get_logger=lambda: logger,
        create_service=lambda _type, name, cb, callback_group=None: services.setdefault(name, cb),
    )
    return node, services


def _cancel(services):
    cb = next(iter(services.values()))
    return cb(None, SimpleNamespace(success=False, message=''))


def test_cancel_stops_the_loop_before_the_next_model_call():
    node, services = _fake_node()
    cancellation = PlanningCancellation(node, None)
    flag = cancellation.begin()
    calls = []

    class Agent:
        def step(self, observation):
            calls.append(1)
            _cancel(services)  # the user presses Stop during this model call
            return _step('approach')

    with pytest.raises(PlanningCancelled):
        run_plan_loop(Agent(), {}, cancelled=flag)
    assert len(calls) == 1


def test_loop_without_cancel_runs_to_the_end():
    script = iter([_step('approach'), _step('pick'), _step('idle', end=True)])
    agent = SimpleNamespace(step=lambda observation: next(script))
    steps = run_plan_loop(agent, {}, cancelled=PlanningCancellation(_fake_node()[0], None).begin())
    assert [s['action_name'] for s in steps] == ['approach', 'pick']


def test_cancel_reaches_every_request_in_progress_and_only_those():
    node, services = _fake_node()
    cancellation = PlanningCancellation(node, None)
    first, second = cancellation.begin(), cancellation.begin()
    finished = cancellation.begin()
    cancellation.end(finished)

    response = _cancel(services)

    assert first.is_set() and second.is_set()
    assert not finished.is_set()
    assert response.success and '2' in response.message
    # A request started after the cancel is not born cancelled.
    assert not cancellation.begin().is_set()
