"""Regression tests for ROBOAI-14: a gripper command must be able to fail.

The audited _gripper_set_pin returned True unconditionally, ignoring the IO
timeout, the SetIO response's success field and exceptions from the future,
which made the 'gripper close failed' branch of pick unreachable. These
tests drive it with a scripted SetIO client, no robot or driver needed.
"""
import threading
from types import SimpleNamespace

import pytest

import robo_reason_executor.ur5_skill_executor_node as executor_module
from robo_reason_executor.ur5_skill_executor_node import UR5SkillExecutorNode

_LOGGER = SimpleNamespace(error=lambda msg: None, info=lambda msg: None, warn=lambda msg: None)


class _ScriptedFuture:
    def __init__(self, response=None, exc=None, completes=True):
        self._response, self._exc, self._completes = response, exc, completes

    def add_done_callback(self, cb):
        if self._completes:
            cb(self)

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._response


class _ScriptedSetIOClient:
    def __init__(self, future=None, available=True):
        self._future, self._available = future, available

    def wait_for_service(self, timeout_sec):
        return self._available

    def call_async(self, request):
        return self._future


@pytest.fixture(autouse=True)
def _no_gripper_delay(monkeypatch):
    # _gripper_set_pin sleeps 2 s for the fingers to move; irrelevant here.
    monkeypatch.setattr(executor_module.time, 'sleep', lambda s: None)


def _set_pin(client):
    node = SimpleNamespace(_set_io_client=client, get_logger=lambda: _LOGGER)
    return UR5SkillExecutorNode._gripper_set_pin(node, 1.0)


def test_successful_io_write_is_success():
    assert _set_pin(_ScriptedSetIOClient(_ScriptedFuture(SimpleNamespace(success=True)))) is True


def test_rejected_io_write_is_failure():
    assert _set_pin(_ScriptedSetIOClient(_ScriptedFuture(SimpleNamespace(success=False)))) is False


def test_io_exception_is_failure():
    assert _set_pin(_ScriptedSetIOClient(_ScriptedFuture(exc=RuntimeError('driver died')))) is False


def test_io_service_unavailable_is_failure():
    assert _set_pin(_ScriptedSetIOClient(available=False)) is False


def test_io_timeout_is_failure(monkeypatch):
    class _FastTimeoutEvent(threading.Event):
        def wait(self, timeout=None):
            return super().wait(timeout=0.01)

    monkeypatch.setattr(executor_module.threading, 'Event', _FastTimeoutEvent)
    assert _set_pin(_ScriptedSetIOClient(_ScriptedFuture(completes=False))) is False


def test_failed_gripper_close_fails_the_pick():
    node = SimpleNamespace(
        _robot_model=SimpleNamespace(tool=None),
        _last_approach_R=None,
        get_logger=lambda: _LOGGER,
        _gripper_open=lambda **kw: True,
        _move_linear=lambda *a, **kw: True,
        _gripper_close=lambda **kw: False,
    )
    goal_handle = SimpleNamespace(publish_feedback=lambda fb: None)
    with pytest.raises(RuntimeError, match='gripper close failed'):
        UR5SkillExecutorNode._dispatch_skill(
            node, 'pick', {'target_position': [0.3, -0.4, 0.0]}, goal_handle,
            SimpleNamespace(status='', progress=0.0),
        )
