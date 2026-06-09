"""
ur5_skill_executor_node — real UR5cb skill executor.

Exposes the /execute_skill action server interface and drives the physical
robot via:

  - /scaled_joint_trajectory_controller/follow_joint_trajectory  (ROS2 action)
  - OnRobot RG2 gripper via /io_and_status_controller/set_io service
    (digital output 0: HIGH=close, LOW=open)

The gripper is controlled by a URScript thread running on the pendant
(gripper_thread.script) that watches digital output 0 and calls RG2()
accordingly. This avoids dropping the External Control connection.

IK is computed with roboticstoolbox-python.

Install:
  /usr/bin/pip3 install roboticstoolbox-python spatialmath-python
"""

import json
import threading
import time

import rclpy
from rclpy.action import ActionClient, ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from robo_reason_interfaces.action import ExecuteSkill
from trajectory_msgs.msg import JointTrajectoryPoint
from ur_msgs.srv import SetIO

try:
    import roboticstoolbox as rtb
    from spatialmath import SE3, UnitQuaternion
    from spatialmath.base import rt2tr
    IK_AVAILABLE = True
except ImportError as _e:
    IK_AVAILABLE = False
    _IK_IMPORT_ERROR = str(_e)

# Joint names as published by the UR driver
UR5_JOINT_NAMES = [
    'shoulder_pan_joint',
    'shoulder_lift_joint',
    'elbow_joint',
    'wrist_1_joint',
    'wrist_2_joint',
    'wrist_3_joint',
]

# Gripper digital output pin (standard I/O, not tool I/O)
# HIGH (1.0) = close gripper, LOW (0.0) = open gripper
# The pendant program (gripper_thread.script) watches this pin and calls RG2()
_GRIPPER_PIN = 0
_IO_FUN_DIGITAL_OUT = 1  # SetIO fun=1 → standard digital output


class UR5SkillExecutorNode(Node):
    """
    Real UR5cb executor with OnRobot RG2 gripper.

    Skill → Robot mapping:
      approach      → IK → move_to_joints()
      pick          → IK → move_to_joints() → gripper_close()
      release       → IK → move_to_joints() → gripper_open()
      open_gripper  → gripper_open()
      close_gripper → gripper_close()
      move_home     → move_to_joints(HOME_JOINTS)
      wait          → time.sleep(t)

    Gripper is controlled via /io_and_status_controller/set_io (digital out 0).
    The pendant runs gripper_thread.script which watches pin 0 and calls RG2().
    External Control stays connected throughout — no interruption.
    """

    # Home configuration (radians)
    HOME_JOINTS = [-1.5708, -1.5708, -1.5708, -1.5708, 1.5708, 0.0]

    # Fixed gripper orientation: pointing straight down (top-down grasp)
    _GRIP_QUAT = [0.0, -0.707, 0.707, 0.0]

    # TCP offset (metres) — adjust to match your tool + gripper stack
    # _TCP_OFFSET = (0.148, -0.128, 0.265)
    _TCP_OFFSET = (0.0, 0.0, -0.16) #OnRobot Gripper

    def __init__(self):
        super().__init__('ur5_skill_executor_node')

        if not IK_AVAILABLE:
            self.get_logger().fatal(
                f'[UR5Executor] roboticstoolbox not available: {_IK_IMPORT_ERROR}\n'
                'Run: /usr/bin/pip3 install roboticstoolbox-python spatialmath-python'
            )
            raise RuntimeError('roboticstoolbox not available.')

        self.declare_parameter('robot_ip', '192.168.2.60')
        self._robot_ip = self.get_parameter('robot_ip').get_parameter_value().string_value

        self._cb_group = ReentrantCallbackGroup()

        # Robot model for IK
        self._robot_model = rtb.models.DH.UR5()
        self._robot_model.tool = SE3(*self._TCP_OFFSET)

        # --- Action client: joint trajectory controller ---
        self._traj_client = ActionClient(
            self,
            FollowJointTrajectory,
            '/scaled_joint_trajectory_controller/follow_joint_trajectory',
            callback_group=self._cb_group,
        )

        # --- Service client: gripper via digital output ---
        self._set_io_client = self.create_client(
            SetIO,
            '/io_and_status_controller/set_io',
            callback_group=self._cb_group,
        )

        # --- Action server: /execute_skill ---
        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            '/execute_skill',
            self._execute_skill_callback,
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f'[UR5Executor] Ready — REAL ROBOT mode (robot_ip={self._robot_ip}).'
        )

    # -------------------------------------------------------------------------
    # IK helpers
    # -------------------------------------------------------------------------

    def _compute_ik(self, x: float, y: float, z: float):
        """Return joint angles for a top-down end-effector pose at [x, y, z]."""
        qw, qx, qy, qz = self._GRIP_QUAT
        uq = UnitQuaternion([qw, qx, qy, qz])
        T = SE3(x, y, z) * SE3(rt2tr(uq.R, [0.0, 0.0, 0.0]))
        sol = self._robot_model.ikine_LM(T, q0=self.HOME_JOINTS)
        if sol.success:
            return list(sol.q)
        self.get_logger().warn(f'[UR5Executor] IK failed for [{x:.3f}, {y:.3f}, {z:.3f}]')
        return None

    def _approach_pose(self, target: list, offset: float, direction: str) -> list:
        """Return hover position above/beside the target."""
        x, y, z = target
        if direction == 'x':
            return [x - offset, y, z]
        elif direction == 'y':
            return [x, y - offset, z]
        else:  # default: z
            return [x, y, z + offset]

    # -------------------------------------------------------------------------
    # Joint trajectory
    # -------------------------------------------------------------------------

    def _move_to_joints(self, joints: list, duration_sec: float = 3.0) -> bool:
        """Send a joint trajectory goal and block until it completes."""
        if not self._traj_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error('[UR5Executor] Trajectory action server not available.')
            return False

        point = JointTrajectoryPoint()
        point.positions = joints
        point.velocities = [0.0] * 6
        secs = int(duration_sec)
        nsecs = int((duration_sec - secs) * 1e9)
        point.time_from_start = Duration(sec=secs, nanosec=nsecs)

        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = UR5_JOINT_NAMES
        goal.trajectory.points = [point]

        done = threading.Event()
        result_holder = [None]

        def _done_cb(future):
            result_holder[0] = future.result()
            done.set()

        future = self._traj_client.send_goal_async(goal)
        future.add_done_callback(
            lambda f: f.result().get_result_async().add_done_callback(_done_cb)
        )
        if not done.wait(timeout=duration_sec + 10.0):
            self.get_logger().error('[UR5Executor] Trajectory goal timed out.')
            return False

        return result_holder[0] is not None

    # -------------------------------------------------------------------------
    # Gripper
    # -------------------------------------------------------------------------

    def _gripper_set_pin(self, state: float) -> bool:
        """Set digital output 0 to trigger the pendant gripper_thread."""
        if not self._set_io_client.wait_for_service(timeout_sec=3.0):
            self.get_logger().error('[UR5Executor] /io_and_status_controller/set_io not available.')
            return False
        req = SetIO.Request()
        req.fun = _IO_FUN_DIGITAL_OUT
        req.pin = _GRIPPER_PIN
        req.state = state
        done = threading.Event()
        self._set_io_client.call_async(req).add_done_callback(lambda f: done.set())
        done.wait(timeout=5.0)
        time.sleep(2.0)  # wait for gripper motion to complete
        return True

    def _gripper_close(self, force: float = 40.0) -> bool:
        """Close the gripper (pin HIGH). Force threshold is set in gripper_thread.script."""
        self.get_logger().info(f'[UR5Executor] Gripper close (pin HIGH)')
        return self._gripper_set_pin(1.0)

    def _gripper_open(self, force: float = 20.0) -> bool:
        """Open the gripper fully (pin LOW)."""
        self.get_logger().info('[UR5Executor] Gripper open (pin LOW)')
        return self._gripper_set_pin(0.0)


    # -------------------------------------------------------------------------
    # Action server
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
            hover = self._approach_pose(target, offset, direction)
            joints = self._compute_ik(*hover)
            if joints is None:
                raise ValueError(f'IK failed for approach position {hover}')
            feedback.status = 'Moving to approach position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._move_to_joints(joints, duration_sec=3.0):
                raise RuntimeError('move_to failed during approach')

        elif skill_name == 'pick':
            target = args['target_position']
            force = float(args.get('force', 40.0))
            joints = self._compute_ik(*target)
            if joints is None:
                raise ValueError(f'IK failed for pick position {target}')
            feedback.status = 'Moving to grasp position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._move_to_joints(joints, duration_sec=3.0):
                raise RuntimeError('move_to failed during pick')
            feedback.status = f'Closing gripper (force_threshold={force}N)'
            feedback.progress = 0.7
            goal_handle.publish_feedback(feedback)
            if not self._gripper_close(force=force):
                raise RuntimeError('gripper close failed')

        elif skill_name == 'release':
            release_pos = args['release_position']
            open_force = float(args.get('open_force', 20.0))
            joints = self._compute_ik(*release_pos)
            if joints is None:
                raise ValueError(f'IK failed for release position {release_pos}')
            feedback.status = 'Moving to release position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._move_to_joints(joints, duration_sec=3.0):
                raise RuntimeError('move_to failed during release')
            feedback.status = 'Opening gripper'
            feedback.progress = 0.7
            goal_handle.publish_feedback(feedback)
            if not self._gripper_open(force=open_force):
                raise RuntimeError('gripper open failed')

        elif skill_name == 'move_home':
            feedback.status = 'Moving to home'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._move_to_joints(self.HOME_JOINTS, duration_sec=4.0):
                raise RuntimeError('move_to home failed')

        elif skill_name == 'open_gripper':
            force = float(args.get('force', 20.0))
            feedback.status = 'Opening gripper'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._gripper_open(force=force):
                raise RuntimeError('gripper open failed')

        elif skill_name == 'close_gripper':
            force = float(args.get('force', 40.0))
            feedback.status = f'Closing gripper (force_threshold={force}N)'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._gripper_close(force=force):
                raise RuntimeError('gripper close failed')

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
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
