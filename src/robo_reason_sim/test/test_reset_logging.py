"""A reset that meets a problem after a clean one must not kill the shim (ROBOAI-40).

rclpy refuses a severity change on the same log call site, so the outcome of
_on_reset has to go through separate warn and info calls.
"""
from types import SimpleNamespace

import rclpy.logging
from std_srvs.srv import Trigger

from robo_reason_sim.gripper_shim_node import GripperShimNode


def _shim(home_ok):
    publisher = SimpleNamespace(publish=lambda _msg: None)
    logger = rclpy.logging.get_logger('test_reset_logging')
    return SimpleNamespace(
        _held=None, _drop_timer=None, _cubes=['red'], _detach={'red': publisher},
        _send_home=lambda: home_ok(), _rest_positions={'red': (0.0, 0.0, 0.0, 0.0)},
        _set_pose=lambda *_args: True, get_logger=lambda: logger)


def test_reset_with_a_problem_after_a_clean_one():
    results = iter([True, False])
    shim = _shim(lambda: next(results))
    first = GripperShimNode._on_reset(shim, None, Trigger.Response())
    second = GripperShimNode._on_reset(shim, None, Trigger.Response())
    assert first.success
    assert not second.success and 'arm did not reach home' in second.message
