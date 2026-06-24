import threading

import rclpy
from rclpy.executors import MultiThreadedExecutor
import uvicorn

from robo_reason_gui.app import create_app
from robo_reason_gui.bridge_node import GuiBridgeNode

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

    app = create_app(bridge)
    bridge.get_logger().info(
        f'[GuiBridgeNode] serving GUI on http://{HOST}:{PORT}'
    )

    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level='info')
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        bridge.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
