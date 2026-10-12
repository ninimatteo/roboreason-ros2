"""
gripper_shim_node: the simulated gripper, behind the same SetIO service as the UR driver.

On the real robot the executor closes the RG2 by setting a digital output
through the driver's set_io service, and a pendant program moves the fingers.
This node serves that same service in the simulation, so the executor runs
unchanged:

  pin HIGH (close)  attach the nearest cube to the wrist, but only if its
                    centre is within grasp_tolerance_m of the TCP. Otherwise
                    the gripper closes on nothing and the cube stays put,
                    which is the failure a closed loop has to notice.
  pin LOW (open)    detach the held cube; it falls onto whatever is below.

Like the real driver, the IO write itself always succeeds: success says the
pin was set, not that something was grasped. The TCP comes from the same
roboticstoolbox model and TCP offset the executor uses, and cube positions
from Gazebo's ground-truth poses.
"""

import json
import threading

import numpy as np
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Empty
from tf2_msgs.msg import TFMessage
from ur_msgs.srv import SetIO

import roboticstoolbox as rtb
from spatialmath import SE3

from robo_reason_bringup.config import settings
from robo_reason_bringup.grasp_geometry import tcp_offset_z_for_width
from robo_reason_bringup.interfaces import names
from robo_reason_executor.ur5_skill_executor_node import (
    UR5_JOINT_NAMES, _GRIPPER_PIN, _IO_FUN_DIGITAL_OUT,
)
from robo_reason_sim.world_builder import (
    default_scene_path, graspable_objects, gripper_topic, load_sim_config,
)

# DetachableJoint attaches every cube to the wrist as soon as the world
# starts. They are released once Gazebo is up and the bridge listens, with
# the detach repeated a few times since a message sent while the bridge is
# still connecting is silently lost (detaching a free cube is a no-op).
_STARTUP_CHECK_PERIOD_S = 0.5
_STARTUP_DETACH_REPEATS = 3


class GripperShimNode(Node):
    def __init__(self):
        super().__init__('gripper_shim_node')
        cfg = load_sim_config()
        self._tolerance = float(cfg['gripper']['grasp_tolerance_m'])
        scene = json.loads(default_scene_path().read_text())
        self._cubes = sorted(graspable_objects(scene))

        self._model = rtb.models.DH.UR5()
        self._model.tool = SE3(settings.TCP_OFFSET_X, settings.TCP_OFFSET_Y,
                               tcp_offset_z_for_width(None))

        self._lock = threading.Lock()
        self._joints = None
        self._cube_positions = {}
        self._held = None

        group = ReentrantCallbackGroup()
        self.create_subscription(JointState, names.joint_states, self._on_joints, 10,
                                 callback_group=group)
        self.create_subscription(TFMessage, 'ground_truth/poses', self._on_poses, 10,
                                 callback_group=group)
        ns = self.get_namespace().strip('/')
        self._attach = {c: self.create_publisher(Empty, gripper_topic(ns, c, 'attach'), 10)
                        for c in self._cubes}
        self._detach = {c: self.create_publisher(Empty, gripper_topic(ns, c, 'detach'), 10)
                        for c in self._cubes}
        self.create_service(SetIO, names.gripper_io, self._on_set_io, callback_group=group)

        self._startup_detaches_sent = 0
        self._startup_timer = self.create_timer(_STARTUP_CHECK_PERIOD_S, self._release_all)
        self.get_logger().info(
            f'[GripperShim] Ready: cubes={self._cubes}, tolerance={self._tolerance:.3f} m'
        )

    def _release_all(self):
        with self._lock:
            gazebo_up = len(self._cube_positions) == len(self._cubes)
        bridge_up = all(pub.get_subscription_count() > 0 for pub in self._detach.values())
        if not (gazebo_up and bridge_up):
            return
        for cube in self._cubes:
            self._detach[cube].publish(Empty())
        self._startup_detaches_sent += 1
        if self._startup_detaches_sent >= _STARTUP_DETACH_REPEATS:
            self._startup_timer.cancel()
            self.get_logger().info('[GripperShim] Released all cubes from the startup attach.')

    def _on_joints(self, msg: JointState):
        by_name = dict(zip(msg.name, msg.position))
        joints = [by_name.get(n) for n in UR5_JOINT_NAMES]
        if all(j is not None for j in joints):
            with self._lock:
                self._joints = joints

    def _on_poses(self, msg: TFMessage):
        with self._lock:
            for tf in msg.transforms:
                if tf.child_frame_id in self._attach:
                    t = tf.transform.translation
                    self._cube_positions[tf.child_frame_id] = np.array([t.x, t.y, t.z])

    def _tcp(self):
        with self._lock:
            joints = self._joints
        if joints is None:
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
        with self._lock:
            positions = dict(self._cube_positions)
        if tcp is None or not positions:
            self.get_logger().warn('[GripperShim] Close: no joint state or cube poses yet.')
            return
        cube, dist = min(((c, float(np.linalg.norm(p - tcp))) for c, p in positions.items()),
                         key=lambda item: item[1])
        if dist <= self._tolerance:
            self._attach[cube].publish(Empty())
            self._held = cube
            self.get_logger().info(f'[GripperShim] Grasped {cube} (TCP distance {dist:.3f} m).')
        else:
            self.get_logger().warn(
                f'[GripperShim] Closed on nothing: nearest is {cube} at {dist:.3f} m '
                f'> tolerance {self._tolerance:.3f} m (TCP at {np.round(tcp, 3).tolist()}).'
            )

    def _open(self):
        if self._held is None:
            return
        self._detach[self._held].publish(Empty())
        self.get_logger().info(f'[GripperShim] Released {self._held}.')
        self._held = None


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
