"""
MockCameraServiceNode — fake camera node for VLM dry-runs (no hardware needed).

Exposes the same two services as camera_services_node:
  /camera/get_image  — serves PNG files from a folder in alphabetical order (cycles)
  /camera/deproject  — returns fixed Z-depth world coords; maps u,v to x,y linearly

ROS2 parameters:
  images_dir      (str,   default from settings) — folder containing .png files to serve
  mock_depth_m    (float, default 0.5)           — Z depth returned for every pixel (metres)
  workspace_w_m   (float, default 0.6)           — physical workspace width (X axis, metres)
  workspace_h_m   (float, default 0.4)           — physical workspace height (Y axis, metres)
  image_width_px  (int,   default 640)           — image width used for linear u→x mapping
  image_height_px (int,   default 480)           — image height used for linear v→y mapping
"""

import itertools
from pathlib import Path

import os
import cv2
import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import Point
from rclpy.node import Node
from std_msgs.msg import Header

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import Deproject, GetImage


class MockCameraServiceNode(Node):
    """
    Fake camera that serves PNG files and returns linearly-mapped 3D coords.

    Useful for testing the full VLM planning pipeline without a real camera or
    depth sensor. Each GetImage call advances to the next PNG in the folder
    (cycles back to the first after the last file).
    """

    def __init__(self):
        super().__init__('mock_camera_service_node')

        self.declare_parameter('images_dir', settings.MOCK_IMAGES_DIR)
        self.declare_parameter('mock_depth_m', 0.5)
        self.declare_parameter('workspace_w_m', 0.6)
        self.declare_parameter('workspace_h_m', 0.4)
        self.declare_parameter('image_width_px', 640)
        self.declare_parameter('image_height_px', 480)

        images_dir = Path(self.get_parameter('images_dir').value)
        images_dir = os.path.join(os.getcwd(), images_dir)
        self._mock_depth_m = float(self.get_parameter('mock_depth_m').value)
        self._workspace_w_m = float(self.get_parameter('workspace_w_m').value)
        self._workspace_h_m = float(self.get_parameter('workspace_h_m').value)
        self._img_w = int(self.get_parameter('image_width_px').value)
        self._img_h = int(self.get_parameter('image_height_px').value)

        assert images_dir.exists(), f'[MockCameraServiceNode] images_dir does not exist: {images_dir}'
        # Build sorted list of PNG paths and cycle through them
        png_paths = sorted(images_dir.glob('*.png'))
        if not png_paths:
            raise RuntimeError(
                f'[MockCameraServiceNode] No PNG files found in {images_dir}. '
                'Add at least one .png image to use the mock camera.'
            )
        self._image_cycle = itertools.cycle(png_paths)
        self._bridge = CvBridge()

        self._get_image_srv = self.create_service(
            GetImage, '/camera/get_image', self._get_image_callback
        )
        self._deproject_srv = self.create_service(
            Deproject, '/camera/deproject', self._deproject_callback
        )

        self.get_logger().info(
            f'[MockCameraServiceNode] Ready — {len(png_paths)} image(s) in {images_dir}, '
            f'mock depth={self._mock_depth_m} m'
        )

    # ── /camera/get_image ─────────────────────────────────────────────────────

    def _get_image_callback(self, request, response):
        path = next(self._image_cycle)
        bgr = cv2.imread(str(path))
        if bgr is None:
            response.success = False
            response.error_message = f'Failed to read image: {path}'
            return response

        ros_img = self._bridge.cv2_to_imgmsg(bgr, encoding='bgr8')
        ros_img.header = Header(frame_id='mock_camera_optical_frame')
        ros_img.header.stamp = self.get_clock().now().to_msg()

        response.success = True
        response.image = ros_img
        response.frame_id = 'mock_camera_optical_frame'

        self.get_logger().info(f'[MockCameraServiceNode] Served image: {path.name}')
        return response

    # ── /camera/deproject ─────────────────────────────────────────────────────

    def _deproject_callback(self, request, response):
        """
        Map pixel coordinates to workspace 3D coords linearly.

        u (column, 0..image_width_px)  → x in [-workspace_w_m/2, +workspace_w_m/2]
        v (row,    0..image_height_px) → y in [-workspace_h_m/2, +workspace_h_m/2]
        z fixed at mock_depth_m
        """
        if len(request.u) != len(request.v):
            response.success = False
            response.error_message = 'u and v must have the same length'
            return response

        points = []
        for u, v in zip(request.u, request.v):
            x = (u / self._img_w - 0.5) * self._workspace_w_m
            y = (v / self._img_h - 0.5) * self._workspace_h_m
            z = self._mock_depth_m
            points.append(Point(x=float(x), y=float(y), z=float(z)))

        response.success = True
        response.points = points
        response.frame_id = 'base_link'
        response.charuco_pose_available = False
        response.charuco_points = []
        response.charuco_frame_id = ''
        return response


def main(args=None):
    rclpy.init(args=args)
    node = MockCameraServiceNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
