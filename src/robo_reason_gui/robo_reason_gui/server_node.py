import os
import threading

import rclpy
from rclpy.executors import MultiThreadedExecutor
import uvicorn
from ament_index_python.packages import get_package_share_directory

from robo_reason_gui.app import create_app
from robo_reason_gui.bridge_node import GuiBridgeNode
from robo_reason_gui.camera_service_supervisor import CameraServiceSupervisor
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

    # The GUI owns the ROS2 stack as a child process (B1). The graph guard uses
    # the bridge's live /execute_skill server count to refuse launching on top
    # of a leftover executor — no button combo can spawn a second one.
    supervisor = StackSupervisor(
        logger=bridge.get_logger(),
        executor_count=bridge.execute_skill_server_count,
    )

    # The GUI also owns the flaky UR driver, retrying startup until the robot
    # is reachable through the bridge's connectivity probes (B5 / Phase 5).
    driver = UrDriverSupervisor(
        ready_check=bridge.robot_ready, logger=bridge.get_logger()
    )

    # Let the LED reflect the teach pendant, not just the controllers: green
    # only once the driver reports the reverse interface connected.
    bridge.set_connection_sources(driver.robot_connected, driver.is_running)

    # The Orbbec camera service is started on demand from the GUI; readiness is
    # confirmed through the bridge's /camera/get_image service probe.
    _cam_script = os.path.join(
        get_package_share_directory('robo_reason_gui'), 'scripts', 'run_orbbec_registered.sh'
    )
    camera = CameraServiceSupervisor(
        script_path=_cam_script,
        ready_check=bridge.camera_available,
        logger=bridge.get_logger(),
    )

    app = create_app(bridge, supervisor, driver, camera)
    bridge.get_logger().info(
        f'[GuiBridgeNode] serving GUI on http://{HOST}:{PORT}'
    )

    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level='info')
    except KeyboardInterrupt:
        pass
    finally:
        # Tear the launched stack + UR driver + camera down cleanly.
        camera.stop()
        driver.stop()
        supervisor.stop()
        executor.shutdown()
        bridge.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
