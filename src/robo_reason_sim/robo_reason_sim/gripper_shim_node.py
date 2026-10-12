"""
gripper_shim_node: the simulated gripper, behind the same SetIO service as the UR driver.

On the real robot the executor closes the RG2 by setting a digital output
through the driver's set_io service, and a pendant program moves the fingers.
This node serves that same service in the simulation, so the executor runs
unchanged:

  pin HIGH (close)  attach the nearest cube to the wrist, but only if its
                    centre is within grasp_tolerance_m of the TCP and nothing
                    rests on it (see grasp_rules). Otherwise the gripper
                    closes on nothing and the cube stays put, which is the
                    failure a closed loop has to notice.
  pin LOW (open)    detach the held cube; it falls onto whatever is below.

Like the real driver, the IO write itself always succeeds: success says the
pin was set, not that something was grasped. The TCP comes from the same
roboticstoolbox model and TCP offset the executor uses, and cube positions
from Gazebo's ground-truth poses. Both are read once, when the gripper closes,
rather than followed continuously: joint_states and the poses stream at
100 and 60 Hz, which kept this Python node at half a core doing nothing.

Since it owns the gripper state, this node also serves reset_simulation:
release whatever is held, send the arm home, and put every cube back where
scene_mock.json places it, ready for the next run.
"""

import json
import subprocess
import threading

import numpy as np
import rclpy
from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.wait_for_message import wait_for_message
from sensor_msgs.msg import JointState
from std_msgs.msg import Empty
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage
from trajectory_msgs.msg import JointTrajectoryPoint
from ur_msgs.srv import SetIO

import roboticstoolbox as rtb
from spatialmath import SE3

from robo_reason_bringup.config import settings
from robo_reason_bringup.grasp_geometry import tcp_offset_z_for_width
from robo_reason_bringup.interfaces import names
from robo_reason_executor.ur5_skill_executor_node import (
    UR5_JOINT_NAMES, _GRIPPER_PIN, _IO_FUN_DIGITAL_OUT,
)
from robo_reason_sim.grasp_rules import choose_grasp
from robo_reason_sim.world_builder import (
    default_scene_path, graspable_objects, gripper_topic, load_sim_config,
    object_rest_positions,
)

# How long to wait for one joint_states / ground-truth message on demand.
_READ_TIMEOUT_S = 2.0

# DetachableJoint attaches every cube to the wrist as soon as the world
# starts. They are released once Gazebo is up and the bridge listens, with
# the detach repeated a few times since a message sent while the bridge is
# still connecting is silently lost (detaching a free cube is a no-op).
_STARTUP_CHECK_PERIOD_S = 0.5
_STARTUP_DETACH_REPEATS = 3
# Time the arm gets to go home on a reset.
_RESET_HOME_DURATION_S = 4.0


class GripperShimNode(Node):
    def __init__(self):
        super().__init__('gripper_shim_node')
        cfg = load_sim_config()
        self._tolerance = float(cfg['gripper']['grasp_tolerance_m'])
        self._world = cfg['world_name']
        scene = json.loads(default_scene_path().read_text())
        self._cubes = sorted(graspable_objects(scene))
        self._rest_positions = object_rest_positions(scene)
        self._sizes = {name: obj['size'] for name, obj in graspable_objects(scene).items()}

        self._model = rtb.models.DH.UR5()
        self._model.tool = SE3(settings.TCP_OFFSET_X, settings.TCP_OFFSET_Y,
                               tcp_offset_z_for_width(None))

        self._held = None

        group = ReentrantCallbackGroup()
        ns = self.get_namespace().strip('/')
        self._attach = {c: self.create_publisher(Empty, gripper_topic(ns, c, 'attach'), 10)
                        for c in self._cubes}
        self._detach = {c: self.create_publisher(Empty, gripper_topic(ns, c, 'detach'), 10)
                        for c in self._cubes}
        self.create_service(SetIO, names.gripper_io, self._on_set_io, callback_group=group)
        self._traj_client = ActionClient(self, FollowJointTrajectory, names.trajectory_action,
                                         callback_group=group)
        self.create_service(Trigger, names.reset_simulation, self._on_reset, callback_group=group)

        self._startup_detaches_sent = 0
        self._startup_timer = self.create_timer(_STARTUP_CHECK_PERIOD_S, self._release_all)
        self.get_logger().info(
            f'[GripperShim] Ready: cubes={self._cubes}, tolerance={self._tolerance:.3f} m'
        )

    def _release_all(self):
        bridge_up = all(pub.get_subscription_count() > 0 for pub in self._detach.values())
        # Gazebo is up once its ground-truth poses come through the bridge.
        if not (bridge_up and self._read_cube_positions()):
            return
        for cube in self._cubes:
            self._detach[cube].publish(Empty())
        self._startup_detaches_sent += 1
        if self._startup_detaches_sent >= _STARTUP_DETACH_REPEATS:
            self._startup_timer.cancel()
            self.get_logger().info('[GripperShim] Released all cubes from the startup attach.')

    def _read(self, msg_type, topic):
        ok, msg = wait_for_message(msg_type, self, topic, time_to_wait=_READ_TIMEOUT_S)
        return msg if ok else None

    def _read_cube_positions(self) -> dict:
        msg = self._read(TFMessage, 'ground_truth/poses')
        if msg is None:
            return {}
        positions = {}
        for tf in msg.transforms:
            if tf.child_frame_id in self._attach:
                t = tf.transform.translation
                positions[tf.child_frame_id] = np.array([t.x, t.y, t.z])
        return positions

    def _tcp(self):
        msg = self._read(JointState, names.joint_states)
        if msg is None:
            return None
        by_name = dict(zip(msg.name, msg.position))
        joints = [by_name.get(n) for n in UR5_JOINT_NAMES]
        if any(j is None for j in joints):
            return None
        return np.array(self._model.fkine(joints).t)

    def _on_set_io(self, request, response):
        response.success = True
        if request.fun != _IO_FUN_DIGITAL_OUT or request.pin != _GRIPPER_PIN:
            return response
        if request.state >= 0.5:
            self._close()
        else:
            self._open()
        return response

    def _close(self):
        if self._held is not None:
            return
        tcp = self._tcp()
        positions = self._read_cube_positions()
        if tcp is None or not positions:
            self.get_logger().warn('[GripperShim] Close: no joint state or cube poses yet.')
            return
        cube, explanation = choose_grasp(tcp, positions, self._sizes, self._tolerance)
        if cube is not None:
            self._attach[cube].publish(Empty())
            self._held = cube
            self.get_logger().info(f'[GripperShim] {explanation[0].upper() + explanation[1:]}.')
        else:
            self.get_logger().warn(
                f'[GripperShim] {explanation[0].upper() + explanation[1:]} (TCP at {np.round(tcp, 3).tolist()}).'
            )

    def _open(self):
        if self._held is None:
            return
        self._detach[self._held].publish(Empty())
        self.get_logger().info(f'[GripperShim] Released {self._held}.')
        self._held = None


    # ------------------------------------------------------------------ reset
    def _on_reset(self, _request, response):
        problems = []
        # 1. Release whatever is held (and anything a crashed run left attached).
        self._held = None
        for cube in self._cubes:
            self._detach[cube].publish(Empty())
        # 2. Arm home, out of the way before the cubes come back.
        if not self._send_home():
            problems.append('arm did not reach home')
        # 3. Every cube back to its starting pose, upright.
        for cube, (x, y, z) in self._rest_positions.items():
            if not self._set_pose(cube, x, y, z):
                problems.append(f'could not move {cube}')
        response.success = not problems
        response.message = '; '.join(problems) or 'scene reset to its initial state'
        log = self.get_logger().warn if problems else self.get_logger().info
        log(f'[GripperShim] Reset: {response.message}')
        return response

    def _send_home(self) -> bool:
        if not self._traj_client.wait_for_server(timeout_sec=2.0):
            return False
        point = JointTrajectoryPoint()
        point.positions = list(settings.HOME_JOINTS)
        point.velocities = [0.0] * len(UR5_JOINT_NAMES)
        point.time_from_start = Duration(sec=int(_RESET_HOME_DURATION_S))
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = UR5_JOINT_NAMES
        goal.trajectory.points = [point]

        done = threading.Event()
        outcome = {}

        def on_result(future):
            outcome['ok'] = future.result().result.error_code == FollowJointTrajectory.Result.SUCCESSFUL
            done.set()

        def on_goal(future):
            handle = future.result()
            if not handle.accepted:
                done.set()
                return
            handle.get_result_async().add_done_callback(on_result)

        self._traj_client.send_goal_async(goal).add_done_callback(on_goal)
        done.wait(_RESET_HOME_DURATION_S + 10.0)
        return outcome.get('ok', False)

    def _set_pose(self, model: str, x: float, y: float, z: float) -> bool:
        """Teleport a model through Gazebo's set_pose service (UserCommands system)."""
        request = (f'name: "{model}", position: {{x: {x}, y: {y}, z: {z}}}, '
                   f'orientation: {{x: 0, y: 0, z: 0, w: 1}}')
        result = subprocess.run(
            ['ign', 'service', '-s', f'/world/{self._world}/set_pose',
             '--reqtype', 'ignition.msgs.Pose', '--reptype', 'ignition.msgs.Boolean',
             '--timeout', '3000', '--req', request],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and 'data: true' in result.stdout


def main(args=None):
    rclpy.init(args=args)
    node = GripperShimNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
