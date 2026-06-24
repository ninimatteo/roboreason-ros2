import time

from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from control_msgs.action import FollowJointTrajectory
from sensor_msgs.msg import JointState
from ur_msgs.srv import SetIO

# Endpoints that indicate the UR5cb / gripper are reachable.
TRAJ_ACTION = '/scaled_joint_trajectory_controller/follow_joint_trajectory'
GRIPPER_IO_SERVICE = '/io_and_status_controller/set_io'
JOINT_STATES_TOPIC = '/joint_states'

# A /joint_states message older than this (seconds) is considered stale.
JOINT_STATES_TIMEOUT_S = 2.0


class GuiBridgeNode(Node):
    """ROS2 bridge between the web GUI and the RoboReason stack.

    Phase 1 adds read-only robot-connectivity probes. There is no formal
    "robot connected" status in the system, so connectivity is derived from
    three signals, refreshed on a timer and cached for the (cross-thread)
    HTTP handlers to read:
      - trajectory action server readiness
      - /joint_states freshness
      - gripper SetIO service availability
    """

    def __init__(self):
        super().__init__('gui_bridge_node')

        self._traj_client = ActionClient(self, FollowJointTrajectory, TRAJ_ACTION)
        self._io_client = self.create_client(SetIO, GRIPPER_IO_SERVICE)
        self.create_subscription(
            JointState, JOINT_STATES_TOPIC, self._on_joint_states,
            qos_profile_sensor_data,
        )

        self._last_joint_states = 0.0  # time.monotonic() of last message
        self._probes = {
            'trajectory_server': False,
            'joint_states': False,
            'gripper_io': False,
        }
        self.create_timer(1.0, self._refresh_probes)

        self.get_logger().info('[GuiBridgeNode] started')

    def _on_joint_states(self, _msg):
        self._last_joint_states = time.monotonic()

    def _refresh_probes(self):
        self._probes['trajectory_server'] = self._traj_client.server_is_ready()
        self._probes['gripper_io'] = self._io_client.service_is_ready()
        self._probes['joint_states'] = (
            (time.monotonic() - self._last_joint_states) < JOINT_STATES_TIMEOUT_S
        )

    def _robot_status(self) -> dict:
        probes = dict(self._probes)
        healthy = sum(1 for ok in probes.values() if ok)
        if healthy == len(probes):
            level = 'green'
        elif healthy > 0:
            level = 'amber'
        else:
            level = 'red'
        return {'level': level, 'probes': probes}

    def health(self) -> dict:
        """Snapshot of ROS + robot connectivity for the GUI's /api/health."""
        names = self.get_node_names()
        return {
            'ros_ok': True,
            'bridge_node': self.get_name(),
            'node_count': len(names),
            'discovered_nodes': sorted(names),
            'robot': self._robot_status(),
        }
