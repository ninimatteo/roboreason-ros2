"""
ur5_skill_executor_node — real UR5cb skill executor.

Drop-in replacement for fake_skill_executor_node.
Exposes the same /execute_skill action server interface, but drives the physical robot
using UR5CBPrimitives (from ur5cb_interface_node package).

To use: in dry_run_services.launch.py, replace fake_skill_executor_node with this node.

Dependencies:
  - ur5cb_interface_node (from ros2_ws_GPT, must be built in the same workspace)
  - roboticstoolbox-python
  - spatialmath-python
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
import json
import time
import threading

from robo_reason_interfaces.action import ExecuteSkill

try:
    from ur5cb_interface_node.ur5_primitives import UR5CBPrimitives
    import roboticstoolbox as rtb
    from spatialmath import SE3
    from spatialmath import UnitQuaternion
    from spatialmath.base import rt2tr
    UR5_AVAILABLE = True
except ImportError as _e:
    UR5_AVAILABLE = False
    _UR5_IMPORT_ERROR = str(_e)


class UR5SkillExecutorNode(Node):
    """
    Real UR5cb executor.

    Skill → Robot mapping:
      approach  → compute approach pose → IK → move_to()
      pick      → move_to(target) → grab()
      release   → move_to(release_pos) → release()
      move_home → move_to(home_joints)
      wait      → time.sleep(t)
    """

    # Home configuration (radians) — matches ur5_translator.py initial_position
    HOME_JOINTS = [-1.5708, -1.5708, -1.5708, -1.5708, 1.5708, 0.0]

    # Fixed gripper orientation: pointing straight down (top-down grasp)
    _GRIP_QUAT = [0.0, -0.707, 0.707, 0.0]   # [qw, qx, qy, qz]

    # TCP offset from ur5_translator.py
    _TCP_OFFSET = (0.148, -0.128, 0.265)

    def __init__(self):
        super().__init__('ur5_skill_executor_node')

        if not UR5_AVAILABLE:
            self.get_logger().fatal(
                f'[UR5Executor] ur5cb_interface_node not available: {_UR5_IMPORT_ERROR}\n'
                'Build ur5cb_interface_node in this workspace or use fake_skill_executor_node.'
            )
            raise RuntimeError('ur5cb_interface_node not available.')

        self._cb_group = ReentrantCallbackGroup()

        # Robot model for IK
        self._robot = rtb.models.DH.UR5()
        self._robot.tool = SE3(*self._TCP_OFFSET)

        # UR5 primitives node
        self._primitives = UR5CBPrimitives(callback_group=self._cb_group)

        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            '/execute_skill',
            self._execute_skill_callback,
            callback_group=self._cb_group,
        )

        self.get_logger().info('[UR5Executor] Ready — REAL ROBOT mode.')

    # -------------------------------------------------------------------------
    # IK helpers
    # -------------------------------------------------------------------------

    def _compute_ik(self, x: float, y: float, z: float):
        """Compute joint configuration for top-down end-effector pose at [x, y, z]."""
        qw, qx, qy, qz = self._GRIP_QUAT
        uq = UnitQuaternion([qw, qx, qy, qz])
        rotation = rt2tr(uq.R, [0.0, 0.0, 0.0])
        T = SE3(x, y, z) * rotation
        sol = self._robot.ikine_LM(T, q0=self.HOME_JOINTS)
        if sol.success:
            return list(sol.q)
        self.get_logger().warn(f'[UR5Executor] IK failed for [{x:.3f}, {y:.3f}, {z:.3f}]')
        return None

    def _approach_pose(self, target_position: list, offset: float, direction: str) -> list:
        """Compute the hover position before grasping/releasing."""
        x, y, z = target_position
        if direction == 'z':
            return [x, y, z + offset]
        elif direction == 'x':
            return [x - offset, y, z]
        elif direction == 'y':
            return [x, y - offset, z]
        return [x, y, z + offset]

    # -------------------------------------------------------------------------
    # Future awaiting helper (thread-safe blocking wait)
    # -------------------------------------------------------------------------

    def _wait_future(self, future, timeout: float = 30.0) -> bool:
        """Block the current thread until an rclpy Future completes."""
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        return done.wait(timeout=timeout)

    # -------------------------------------------------------------------------
    # Action server callback
    # -------------------------------------------------------------------------

    def _execute_skill_callback(self, goal_handle):
        skill_name = goal_handle.request.skill_name.lower()
        skill_args = json.loads(goal_handle.request.skill_args_json)

        self.get_logger().info(f'[UR5Executor] {skill_name.upper()} args={skill_args}')

        feedback = ExecuteSkill.Feedback()
        result = ExecuteSkill.Result()

        try:
            self._dispatch_skill(skill_name, skill_args, goal_handle, feedback)
            feedback.progress = 1.0
            feedback.status = 'Done'
            goal_handle.publish_feedback(feedback)
            goal_handle.succeed()
            result.success = True
            result.result_json = json.dumps({'skill': skill_name, 'status': 'ok'})

        except Exception as e:
            self.get_logger().error(f'[UR5Executor] Skill {skill_name} failed: {e}')
            goal_handle.abort()
            result.success = False
            result.error_message = str(e)

        return result

    def _dispatch_skill(self, skill_name: str, args: dict, goal_handle, feedback):
        if skill_name == 'approach':
            target = args['target_position']
            offset = args.get('offset', 0.1)
            direction = args.get('approach_direction', 'z')
            approach_pos = self._approach_pose(target, offset, direction)
            joints = self._compute_ik(*approach_pos)
            if joints is None:
                raise ValueError(f'IK failed for approach position {approach_pos}')
            feedback.status = 'Moving to approach position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._wait_future(self._primitives.move_to(joints, duration=3.0)):
                raise TimeoutError('move_to timed out during approach')

        elif skill_name == 'pick':
            target = args['target_position']
            joints = self._compute_ik(*target)
            if joints is None:
                raise ValueError(f'IK failed for pick position {target}')
            feedback.status = 'Moving to grasp position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._wait_future(self._primitives.move_to(joints, duration=3.0)):
                raise TimeoutError('move_to timed out during pick')
            feedback.status = 'Closing gripper'
            feedback.progress = 0.7
            goal_handle.publish_feedback(feedback)
            if not self._wait_future(self._primitives.set_digital_output(pin=4, state=False)):
                raise TimeoutError('grab timed out')

        elif skill_name == 'release':
            release_pos = args['release_position']
            joints = self._compute_ik(*release_pos)
            if joints is None:
                raise ValueError(f'IK failed for release position {release_pos}')
            feedback.status = 'Moving to release position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._wait_future(self._primitives.move_to(joints, duration=3.0)):
                raise TimeoutError('move_to timed out during release')
            feedback.status = 'Opening gripper'
            feedback.progress = 0.7
            goal_handle.publish_feedback(feedback)
            if not self._wait_future(self._primitives.set_digital_output(pin=4, state=True)):
                raise TimeoutError('release timed out')

        elif skill_name == 'move_home':
            feedback.status = 'Moving to home'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._wait_future(self._primitives.move_to(self.HOME_JOINTS, duration=4.0)):
                raise TimeoutError('move_to home timed out')

        elif skill_name == 'wait':
            duration = float(args.get('time', 1.0))
            feedback.status = f'Waiting {duration:.1f}s'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            time.sleep(duration)

        else:
            raise ValueError(f'Unknown skill: {skill_name}')


def main(args=None):
    rclpy.init(args=args)
    node = UR5SkillExecutorNode()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    executor.add_node(node._primitives)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
