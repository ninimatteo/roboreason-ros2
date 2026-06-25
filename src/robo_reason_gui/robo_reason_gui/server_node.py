import threading

import rclpy
from rclpy.executors import MultiThreadedExecutor
import uvicorn

from robo_reason_gui.app import create_app
from robo_reason_gui.bridge_node import GuiBridgeNode
from robo_reason_gui.stack_supervisor import StackSupervisor
from robo_reason_gui.ur_driver_supervisor import UrDriverSupervisor

# Bind to 0.0.0.0 so the server is reachable from the host. With the
# container's --network host mode, http://localhost:8080 on the host hits this.
HOST = '0.0.0.0'
PORT = 8080


def main(args=None):
    rclpy.init(args=args)
    bridge = GuiBridgeNode()

    executor = MultiThreadedExecutor()
    executor.add_node(bridge)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    # The GUI owns the ROS2 stack as a child process (B1).
    supervisor = StackSupervisor(logger=bridge.get_logger())

    # The GUI also owns the flaky UR driver, retrying startup until the robot
    # is reachable through the bridge's connectivity probes (B5 / Phase 5).
    driver = UrDriverSupervisor(
        ready_check=bridge.robot_ready, logger=bridge.get_logger()
    )

    app = create_app(bridge, supervisor, driver)
    bridge.get_logger().info(
        f'[GuiBridgeNode] serving GUI on http://{HOST}:{PORT}'
    )

    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level='info')
    except KeyboardInterrupt:
        pass
    finally:
        # Tear the launched stack + UR driver down so no orphan nodes linger.
        driver.stop()
        supervisor.stop()
        executor.shutdown()
        bridge.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
