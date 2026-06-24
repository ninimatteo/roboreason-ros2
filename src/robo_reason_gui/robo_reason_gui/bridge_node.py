from rclpy.node import Node


class GuiBridgeNode(Node):
    """ROS2 bridge between the web GUI and the RoboReason stack.

    Phase 0 only reports basic ROS connectivity. Later phases add:
      - service clients (/plan_task, /execute_plan)
      - topic subscriptions (/world_state, /execution_log)
      - robot-connectivity probes (trajectory action server, /joint_states
        freshness, gripper SetIO service)
    """

    def __init__(self):
        super().__init__('gui_bridge_node')
        self.get_logger().info('[GuiBridgeNode] started')

    def health(self) -> dict:
        """Snapshot of ROS connectivity for the GUI's /api/health endpoint."""
        names = self.get_node_names()
        return {
            'ros_ok': True,
            'bridge_node': self.get_name(),
            'node_count': len(names),
            'discovered_nodes': sorted(names),
        }
