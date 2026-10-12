"""Regression test for ROBOAI-16: a cancelled plan must not resume after the cancel completes.

The plan loop only checks the stop flag between steps. If the emergency stop
handler cleared the flag when it finished, a skill that was still running
during the cancel (a `wait` is a plain sleep and cannot be interrupted) would
return, find the flag cleared, and the rest of the cancelled plan would run
from the home position. The skill executor is replaced by a scripted
_send_skill_goal_sync, so no ROS graph is needed.
"""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from robo_reason_interfaces.srv import CancelExecution, ExecutePlan

from robo_reason_manager.plan_manager_node import PlanManagerNode

SCENE_JSON = (
    Path(__file__).resolve().parents[2]
    / 'robo_reason_task_interface' / 'config' / 'scene_mock.json'
).read_text()


def _fake_manager():
    node = PlanManagerNode.__new__(PlanManagerNode)  # skip Node.__init__: no ROS graph
    logger = SimpleNamespace(info=lambda m: None, warn=lambda m: None, error=lambda m: None)
    node.get_logger = lambda: logger
    node._mode = 'LLM'
    node._skill_client = SimpleNamespace(wait_for_server=lambda timeout_sec: True)
    node._world_state_pub = SimpleNamespace(publish=lambda msg: None)
    node._log_pub = SimpleNamespace(publish=lambda msg: None)
    node._active_goal_handle = None
    node._active_goal_lock = threading.Lock()
    node._stop_requested = threading.Event()
    node._plan_lock = threading.Lock()
    return node


def test_cancel_during_uninterruptible_skill_stops_the_plan():
    node = _fake_manager()
    sent = []

    def scripted_send(goal, timeout_sec=60.0):
        sent.append(goal.skill_name)
        if len(sent) == 1:
            # The emergency stop arrives while the first `wait` is still sleeping,
            # and runs to completion (including return to home) before it returns.
            node._cancel_execution_callback(CancelExecution.Request(), CancelExecution.Response())
        return SimpleNamespace(success=True, error_message='')

    node._send_skill_goal_sync = scripted_send

    plan = [{'step': i, 'action_name': 'wait', 'time': 0} for i in (1, 2, 3)]
    request = ExecutePlan.Request()
    request.plan_json = json.dumps({'plan': plan})
    request.scene_json = SCENE_JSON

    response = node._execute_plan_callback(request, ExecutePlan.Response())

    assert sent == ['wait', 'move_home'], f'plan resumed after cancel: {sent}'
    assert response.success is False
    assert 'cancelled' in response.error_message
