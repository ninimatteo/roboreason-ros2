"""Regression tests for ROBOAI-13: a trajectory abort must not be reported as success.

FollowJointTrajectory delivers a result even when the controller aborts
(tolerance violation, scaling stop), so "a result arrived" is not the same
as "the motion completed". These tests drive _send_trajectory_and_wait with
a scripted action client, no robot or controller needed.
"""
from types import SimpleNamespace

import pytest
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory

from robo_reason_executor.ur5_skill_executor_node import UR5SkillExecutorNode

SUCCESSFUL = FollowJointTrajectory.Result.SUCCESSFUL
PATH_TOLERANCE_VIOLATED = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value

    def add_done_callback(self, cb):
        cb(self)


class _ScriptedTrajClient:
    """Accepts (or rejects) the goal and immediately delivers a scripted result."""

    def __init__(self, status=None, error_code=None, accepted=True):
        wrapped = SimpleNamespace(status=status, result=SimpleNamespace(error_code=error_code))
        self._goal_handle = SimpleNamespace(
            accepted=accepted,
            get_result_async=lambda: _ImmediateFuture(wrapped),
            cancel_goal_async=lambda: None,
        )

    def send_goal_async(self, goal):
        return _ImmediateFuture(self._goal_handle)


def _fake_node(traj_client):
    logger = SimpleNamespace(error=lambda msg: None, info=lambda msg: None, warn=lambda msg: None)
    return SimpleNamespace(_traj_client=traj_client, get_logger=lambda: logger)


def _send(traj_client):
    return UR5SkillExecutorNode._send_trajectory_and_wait(
        _fake_node(traj_client), FollowJointTrajectory.Goal(), duration_sec=0.1
    )


def test_succeeded_with_successful_error_code_is_success():
    assert _send(_ScriptedTrajClient(GoalStatus.STATUS_SUCCEEDED, SUCCESSFUL)) is True


def test_aborted_goal_is_failure():
    assert _send(_ScriptedTrajClient(GoalStatus.STATUS_ABORTED, PATH_TOLERANCE_VIOLATED)) is False


def test_succeeded_status_with_controller_error_code_is_failure():
    assert _send(_ScriptedTrajClient(GoalStatus.STATUS_SUCCEEDED, PATH_TOLERANCE_VIOLATED)) is False


def test_rejected_goal_is_failure():
    assert _send(_ScriptedTrajClient(accepted=False)) is False


def test_failed_motion_fails_the_skill():
    """A False from the motion helper must surface as an exception, not a silent success."""
    node = SimpleNamespace(HOME_JOINTS=[0.0] * 6, _move_to_joints=lambda *a, **kw: False)
    goal_handle = SimpleNamespace(publish_feedback=lambda fb: None)
    with pytest.raises(RuntimeError, match='move_to home failed'):
        UR5SkillExecutorNode._dispatch_skill(
            node, 'move_home', {}, goal_handle, SimpleNamespace(status='', progress=0.0)
        )
