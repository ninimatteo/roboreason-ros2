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
import math
import threading
import time

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from robo_reason_bringup.config import settings
from robo_reason_interfaces.action import ExecuteSkill
from sensor_msgs.msg import JointState
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


class SkillCancelled(Exception):
    """Raised when an emergency-stop cancel request interrupts an in-progress
    skill's motion (see _move_to_joints / _move_linear cancellation polling)."""
    pass


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

    # Home configuration (radians) — default, overridden by 'home_joints' ROS param.
    _DEFAULT_HOME_JOINTS = [-1.5708, -1.5708, -1.5708, -1.5708, 1.5708, 0.0]

    # Gripper orientation for top-down (z) approach (qw, qx, qy, qz)
    _GRIP_QUAT_Z = [0.0, -0.707, 0.707, 0.0]
    # For horizontal approaches the rotation is computed dynamically from the
    # EE→object direction — no fixed quaternion needed.

    # TCP offset (metres) — configured in robo_reason_bringup/config.py
    # (ROBOREASON_TCP_OFFSET_X/Y/Z env vars, or .env file to override).
    _TCP_OFFSET = (settings.TCP_OFFSET_X, settings.TCP_OFFSET_Y, settings.TCP_OFFSET_Z)

    def __init__(self):
        super().__init__('ur5_skill_executor_node')

        if not IK_AVAILABLE:
            self.get_logger().fatal(
                f'[UR5SkillExecutorNode] roboticstoolbox not available: {_IK_IMPORT_ERROR}\n'
                'Run: /usr/bin/pip3 install roboticstoolbox-python spatialmath-python'
            )
            raise RuntimeError('roboticstoolbox not available.')

        self.declare_parameter('robot_ip', settings.ROBOT_IP)
        self.declare_parameter('home_joints', settings.HOME_JOINTS)
        self._robot_ip = self.get_parameter('robot_ip').get_parameter_value().string_value
        self.HOME_JOINTS = list(
            self.get_parameter('home_joints').get_parameter_value().double_array_value
        ) or list(self._DEFAULT_HOME_JOINTS)

        self._cb_group = ReentrantCallbackGroup()

        # Robot model for IK and FK
        self._robot_model = rtb.models.DH.UR5()
        self._robot_model.tool = SE3(*self._TCP_OFFSET)

        # Cache of current joint positions — updated by /joint_states subscriber
        self._current_joints = None
        self._joint_lock = threading.Lock()

        # State from last 'approach' — reused by 'pick' and 'release'
        self._last_approach_R = None      # rotation matrix
        self._last_approach_hover = None  # hover position [x, y, z]
        self.create_subscription(
            JointState,
            '/joint_states',
            self._joint_state_cb,
            10,
        )

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
            cancel_callback=self._handle_cancel_request,
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f'[UR5SkillExecutorNode] Ready — REAL ROBOT mode (robot_ip={self._robot_ip}).'
        )

        # Move to home once the trajectory server is available
        self._startup_timer = self.create_timer(1.0, self._startup_move_home)

    def _startup_move_home(self):
        if not self._traj_client.server_is_ready():
            return  # keep waiting
        self._startup_timer.cancel()
        self.get_logger().info('[UR5SkillExecutorNode] Moving to home position on startup.')
        self._move_to_joints(self.HOME_JOINTS, duration_sec=4.0)
        # Force a known open state regardless of the digital-output level left
        # over from a previous session (pendant script may not retrigger on a
        # LOW→LOW non-edge). Pulse HIGH→LOW to guarantee the gripper opens.
        self.get_logger().info('[UR5SkillExecutorNode] Pulsing gripper HIGH→LOW to ensure open state.')
        self._gripper_set_pin(1.0)
        self._gripper_open()

    # -------------------------------------------------------------------------
    # Joint state callback
    # -------------------------------------------------------------------------

    def _joint_state_cb(self, msg: JointState):
        """Cache the latest joint positions keyed by UR5_JOINT_NAMES order."""
        name_to_pos = dict(zip(msg.name, msg.position))
        joints = [name_to_pos.get(n) for n in UR5_JOINT_NAMES]
        if all(j is not None for j in joints):
            with self._joint_lock:
                self._current_joints = joints

    def _get_ee_position(self):
        """Return current EE [x, y, z] via FK, or None if joints not yet received."""
        with self._joint_lock:
            joints = list(self._current_joints) if self._current_joints else None
        if joints is None:
            return None
        T = self._robot_model.fkine(joints)
        return list(T.t)  # [x, y, z]

    # -------------------------------------------------------------------------
    # IK helpers
    # -------------------------------------------------------------------------

    def _compute_ik(self, x: float, y: float, z: float, R=None):
        """
        Return joint angles for end-effector at [x, y, z] with rotation matrix R.
        If R is None, uses the default top-down orientation (_GRIP_QUAT_Z).
        Uses current joint positions as IK seed for faster convergence.
        """
        if R is None:
            uq = UnitQuaternion(self._GRIP_QUAT_Z)
            R = uq.R
        T = SE3(rt2tr(R, [x, y, z]))
        with self._joint_lock:
            q0 = list(self._current_joints) if self._current_joints else self.HOME_JOINTS
        sol = self._robot_model.ikine_LM(T, q0=q0)
        if sol.success:
            return list(sol.q)
        self.get_logger().warn(f'[UR5SkillExecutorNode] IK failed for [{x:.3f}, {y:.3f}, {z:.3f}]')
        return None

    def _horizontal_approach(self, target: list, offset: float):
        """
        Compute hover position and rotation matrix for a horizontal (side) approach.

        Strategy:
          1. Get current EE position via FK.
          2. Project EE→object direction onto the horizontal plane.
          3. Hover = object_center + offset * unit_vec_toward_EE
             (approach from the side closest to the robot).
          4. Rotation: gripper horizontal, tool-z pointing from hover toward object,
             tool-x pointing down (fingers hang vertically for a stable side grasp).

        Returns (hover_pos, R) or raises RuntimeError if FK unavailable.
        """
        obj_x, obj_y, obj_z = target

        ee_pos = self._get_ee_position()
        if ee_pos is None:
            raise RuntimeError(
                'Joint states not yet received — cannot compute horizontal approach direction.'
            )

        # Horizontal unit vector from object toward EE (best approach side)
        dx = ee_pos[0] - obj_x
        dy = ee_pos[1] - obj_y
        dist_xy = math.sqrt(dx ** 2 + dy ** 2)
        if dist_xy < 1e-3:
            self.get_logger().warn(
                '[UR5SkillExecutorNode] EE is directly above object — defaulting approach to +x.'
            )
            dx, dy = 1.0, 0.0
        else:
            dx /= dist_xy
            dy /= dist_xy

        # Hover position: approach from the EE side
        hover = [obj_x + dx * offset, obj_y + dy * offset, obj_z]

        # Approach direction: from hover toward object = [-dx, -dy, 0]
        theta = math.atan2(-dy, -dx)  # angle that tool-z must point

        # Rotation matrix for horizontal gripper pointing at angle theta:
        #   R = Rz(theta) * Ry(pi/2)
        #   → tool-z = [cos θ, sin θ, 0]  (toward object)
        #   → tool-x = [0, 0, -1]          (fingers point down)
        #   → tool-y = [-sin θ, cos θ, 0]  (horizontal, perpendicular)
        R = (SE3.Rz(theta) * SE3.Ry(math.pi / 2)).R

        self.get_logger().info(
            f'[UR5SkillExecutorNode] Horizontal approach: angle={math.degrees(theta):.1f}° '
            f'hover={[round(v, 3) for v in hover]}'
        )
        return hover, R

    # -------------------------------------------------------------------------
    # Joint trajectory
    # -------------------------------------------------------------------------

    def _move_to_joints(self, joints: list, duration_sec: float = 3.0, goal_handle=None) -> bool:
        """Send a joint trajectory goal and block until it completes.

        Polls ``goal_handle.is_cancel_requested`` (when a goal_handle is
        passed) so an emergency-stop cancel interrupts the wait instead of
        only being noticed after this — potentially multi-second — motion
        finishes on its own. Raises SkillCancelled if that happens.
        """
        if not self._traj_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error('[UR5SkillExecutorNode] Trajectory action server not available.')
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

        return self._send_trajectory_and_wait(goal, duration_sec, goal_handle)

    def _send_trajectory_and_wait(self, goal, duration_sec: float, goal_handle=None) -> bool:
        """Send a FollowJointTrajectory goal, polling for cancellation while it runs."""
        done = threading.Event()
        result_holder = [None]
        traj_goal_handle_holder = [None]

        def _done_cb(future):
            result_holder[0] = future.result()
            done.set()

        def _goal_response_cb(future):
            gh = future.result()
            traj_goal_handle_holder[0] = gh
            if gh.accepted:
                gh.get_result_async().add_done_callback(_done_cb)
            else:
                done.set()

        self._traj_client.send_goal_async(goal).add_done_callback(_goal_response_cb)

        deadline = time.monotonic() + duration_sec + 10.0
        while not done.wait(timeout=0.1):
            if goal_handle is not None and goal_handle.is_cancel_requested:
                tgh = traj_goal_handle_holder[0]
                if tgh is not None:
                    tgh.cancel_goal_async()
                raise SkillCancelled('motion cancelled by user')
            if time.monotonic() > deadline:
                self.get_logger().error('[UR5SkillExecutorNode] Trajectory goal timed out.')
                return False

        return result_holder[0] is not None

    def _move_linear(self, target_xyz: list, R=None,
                     duration_sec: float = 2.0, steps: int = 40, goal_handle=None) -> bool:
        """
        Move the EE in a straight Cartesian line to target_xyz, maintaining
        orientation R throughout.

        Generates `steps` intermediate poses along the line from current EE
        position to target, solves IK for each using the previous solution as
        seed (keeping wrist configuration consistent), and sends all waypoints
        as a single trajectory.
        """
        if not self._traj_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error('[UR5SkillExecutorNode] Trajectory action server not available.')
            return False

        # Current EE position as start
        start_xyz = self._get_ee_position()
        if start_xyz is None:
            self.get_logger().error('[UR5SkillExecutorNode] No joint states — cannot compute linear path.')
            return False

        if R is None:
            uq = UnitQuaternion(self._GRIP_QUAT_Z)
            R = uq.R

        # Seed from current joints
        with self._joint_lock:
            q_seed = list(self._current_joints) if self._current_joints else self.HOME_JOINTS

        # --- Phase 1: solve IK for all waypoints ---
        q_solutions = []
        for i in range(1, steps + 1):
            alpha = i / steps
            x = start_xyz[0] + alpha * (target_xyz[0] - start_xyz[0])
            y = start_xyz[1] + alpha * (target_xyz[1] - start_xyz[1])
            z = start_xyz[2] + alpha * (target_xyz[2] - start_xyz[2])

            T = SE3(rt2tr(R, [x, y, z]))
            sol = self._robot_model.ikine_LM(T, q0=q_seed)
            if not sol.success:
                self.get_logger().warn(
                    f'[UR5SkillExecutorNode] Linear IK failed at step {i}/{steps} '
                    f'[{x:.3f},{y:.3f},{z:.3f}] — skipping waypoint.'
                )
                continue
            q_seed = list(sol.q)
            q_solutions.append(q_seed)

        if not q_solutions:
            self.get_logger().error('[UR5SkillExecutorNode] Linear path: no valid IK solutions found.')
            return False

        # --- Phase 2: assign timestamps and finite-difference velocities ---
        # Uniform time spacing.
        n = len(q_solutions)
        timestamps = [duration_sec * (idx + 1) / n for idx in range(n)]

        # Finite difference velocities:
        #   v[0]   = 0  (start from rest)
        #   v[i]   = (q[i+1] - q[i-1]) / (t[i+1] - t[i-1])  for 0 < i < n-1
        #   v[n-1] = 0  (stop at end)
        # This gives the spline controller a smooth velocity profile through all
        # waypoints without forcing a stop at each one.
        velocities = []
        for idx in range(n):
            if idx == 0 or idx == n - 1:
                velocities.append([0.0] * 6)
            else:
                dt = timestamps[idx + 1] - timestamps[idx - 1]
                v = [(q_solutions[idx + 1][j] - q_solutions[idx - 1][j]) / dt
                     for j in range(6)]
                velocities.append(v)

        points = []
        for idx, (q, v, t) in enumerate(zip(q_solutions, velocities, timestamps)):
            secs = int(t)
            nsecs = int((t - secs) * 1e9)
            pt = JointTrajectoryPoint()
            pt.positions = q
            pt.velocities = v
            pt.time_from_start = Duration(sec=secs, nanosec=nsecs)
            points.append(pt)

        if not points:
            self.get_logger().error('[UR5SkillExecutorNode] Linear path: no valid points after timing.')
            return False

        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = UR5_JOINT_NAMES
        goal.trajectory.points = points

        return self._send_trajectory_and_wait(goal, duration_sec, goal_handle)

    # -------------------------------------------------------------------------
    # Gripper
    # -------------------------------------------------------------------------

    def _gripper_set_pin(self, state: float) -> bool:
        """Set digital output 0 to trigger the pendant gripper_thread."""
        if not self._set_io_client.wait_for_service(timeout_sec=3.0):
            self.get_logger().error('[UR5SkillExecutorNode] /io_and_status_controller/set_io not available.')
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
        self.get_logger().info(f'[UR5SkillExecutorNode] Gripper close (pin HIGH)')
        return self._gripper_set_pin(1.0)

    def _gripper_open(self, force: float = 20.0) -> bool:
        """Open the gripper fully (pin LOW)."""
        self.get_logger().info('[UR5SkillExecutorNode] Gripper open (pin LOW)')
        return self._gripper_set_pin(0.0)


    # -------------------------------------------------------------------------
    # Action server
    # -------------------------------------------------------------------------

    def _handle_cancel_request(self, goal_handle):
        """Accept every cancel request — used by the GUI's emergency stop.

        Just returning ACCEPT here doesn't interrupt anything by itself; the
        motion helpers (_move_to_joints / _move_linear) poll
        goal_handle.is_cancel_requested while they wait and raise
        SkillCancelled, which _execute_skill_callback turns into a proper
        canceled result.
        """
        self.get_logger().warn('[UR5SkillExecutorNode] Cancel requested for in-progress skill.')
        return CancelResponse.ACCEPT

    def _execute_skill_callback(self, goal_handle):
        skill_name = goal_handle.request.skill_name.lower()
        skill_args = json.loads(goal_handle.request.skill_args_json)

        self.get_logger().info(f'[UR5SkillExecutorNode] {skill_name.upper()} args={skill_args}')

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

        except SkillCancelled as e:
            self.get_logger().warn(f'[UR5SkillExecutorNode] Skill {skill_name} cancelled: {e}')
            goal_handle.canceled()
            result.success = False
            result.error_message = f'cancelled: {e}'

        except Exception as e:
            self.get_logger().error(f'[UR5SkillExecutorNode] Skill {skill_name} failed: {e}')
            goal_handle.abort()
            result.success = False
            result.error_message = str(e)

        return result

    def _dispatch_skill(self, skill_name: str, args: dict, goal_handle, feedback):
        if skill_name == 'approach':
            target = args['target_position']
            offset = args.get('offset', 0.05)
            direction = args.get('approach_direction', 'z')

            if direction == 'z':
                hover = [target[0], target[1], target[2] + offset]
                R = None  # _compute_ik will use default _GRIP_QUAT_Z
                joints = self._compute_ik(*hover)
            else:
                hover, R = self._horizontal_approach(target, offset)
                joints = self._compute_ik(*hover, R=R)

            if joints is None:
                raise ValueError(f'IK failed for approach position (dir={direction})')

            # Store approach state so pick/release can maintain orientation and retract
            self._last_approach_R = R
            self._last_approach_hover = hover

            feedback.status = f'Moving to approach position (dir={direction})'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._move_to_joints(joints, duration_sec=3.0, goal_handle=goal_handle):
                raise RuntimeError('move_to failed during approach')

        elif skill_name == 'pick':
            target = args['target_position']
            force = float(args.get('force', 40.0))
            feedback.status = 'Opening gripper before grasp'
            feedback.progress = 0.1
            goal_handle.publish_feedback(feedback)
            self._gripper_open()
            feedback.status = 'Linear move to grasp position'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            # Linear Cartesian motion from hover to grasp — maintains orientation
            if not self._move_linear(target, R=self._last_approach_R, duration_sec=3.0, goal_handle=goal_handle):
                raise RuntimeError('linear move failed during pick')
            feedback.status = 'Closing gripper'
            feedback.progress = 0.7
            goal_handle.publish_feedback(feedback)
            if not self._gripper_close(force=force):
                raise RuntimeError('gripper close failed')

        elif skill_name == 'release':
            release_pos = list(args['release_position'])
            open_force = float(args.get('open_force', 20.0))
            # object_height: how tall the held object is (metres).
            # The camera sees the *surface* (z = table), so we lift the TCP by
            # object_height so the object's bottom rests on the surface rather
            # than the TCP being driven into the table.
            object_height = float(args.get('object_height', 0.0))
            if object_height > 0.0:
                release_pos[2] += object_height
                self.get_logger().info(
                    f'[UR5SkillExecutorNode] release: applying object_height={object_height:.3f} m '
                    f'→ release z = {release_pos[2]:.3f}'
                )

            # 1. Linear move down to release position (mirrors pick)
            feedback.status = 'Linear move to release position'
            feedback.progress = 0.2
            goal_handle.publish_feedback(feedback)
            if not self._move_linear(release_pos, R=self._last_approach_R, duration_sec=3.0, goal_handle=goal_handle):
                raise RuntimeError('linear move failed during release')

            # 2. Open gripper
            feedback.status = 'Opening gripper'
            feedback.progress = 0.5
            goal_handle.publish_feedback(feedback)
            if not self._gripper_open(force=open_force):
                raise RuntimeError('gripper open failed')

            # 3. Retract linearly back to the approach hover position
            retract_target = self._last_approach_hover
            if retract_target is None:
                self.get_logger().warn(
                    '[UR5SkillExecutorNode] release: no hover position stored — '
                    'call approach before release so the node knows where to retract.'
                )
            else:
                feedback.status = 'Retracting to hover position'
                feedback.progress = 0.7
                goal_handle.publish_feedback(feedback)
                if not self._move_linear(retract_target, R=self._last_approach_R, duration_sec=3.0, goal_handle=goal_handle):
                    raise RuntimeError('linear retract failed during release')

            # Clear approach state — next sequence starts fresh
            self._last_approach_R = None
            self._last_approach_hover = None

        elif skill_name == 'move_home':
            feedback.status = 'Moving to home'
            feedback.progress = 0.3
            goal_handle.publish_feedback(feedback)
            if not self._move_to_joints(self.HOME_JOINTS, duration_sec=4.0, goal_handle=goal_handle):
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
