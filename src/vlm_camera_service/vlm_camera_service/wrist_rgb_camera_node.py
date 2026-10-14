"""Generic V4L2 RGB camera driver for the wrist camera on the real robot
(ROBOAI-47): frames from OpenCV, published as an image and its CameraInfo.

The camera model is not fixed yet, so this needs nothing but OpenCV. Until
the camera is calibrated, the intrinsics come from its horizontal field of
view (no distortion); give the calibrated [fx, fy, cx, cy] once known.

Parameters
  device            V4L2 device, e.g. /dev/video2
  width, height     requested capture size
  fps               publish rate
  horizontal_fov    rad, used only without intrinsics
  intrinsics        [fx, fy, cx, cy] or [] (from the field of view)
  frame_id          optical frame of the camera
  color_topic, camera_info_topic
"""
import math

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image


def intrinsics_from_fov(width: int, height: int, horizontal_fov: float) -> list:
    """[fx, fy, cx, cy] of an ideal pinhole with square pixels."""
    f = width / 2.0 / math.tan(horizontal_fov / 2.0)
    return [f, f, width / 2.0, height / 2.0]


def camera_info(width: int, height: int, intrinsics: list, frame_id: str) -> CameraInfo:
    fx, fy, cx, cy = intrinsics
    info = CameraInfo()
    info.header.frame_id = frame_id
    info.width, info.height = width, height
    info.distortion_model = 'plumb_bob'
    info.d = [0.0] * 5
    info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
    info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
    return info


class WristRgbCameraNode(Node):
    def __init__(self):
        super().__init__('wrist_rgb_camera_node')
        import cv2
        from cv_bridge import CvBridge
        p = {name: self.declare_parameter(name, default).value for name, default in (
            ('device', '/dev/video0'), ('width', 640), ('height', 480), ('fps', 10.0),
            ('horizontal_fov', 1.2), ('intrinsics', [0.0]), ('frame_id', 'wrist_camera_optical_frame'),
            ('color_topic', 'wrist/camera/color/image_raw'),
            ('camera_info_topic', 'wrist/camera/color/camera_info'))}
        self._cap = cv2.VideoCapture(p['device'], cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, p['width'])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, p['height'])
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open {p['device']}")
        self._bridge = CvBridge()
        self._frame_id = p['frame_id']
        self._intrinsics = list(p['intrinsics']) if len(p['intrinsics']) == 4 else None
        self._hfov = p['horizontal_fov']
        self._image_pub = self.create_publisher(Image, p['color_topic'], 10)
        self._info_pub = self.create_publisher(CameraInfo, p['camera_info_topic'], 10)
        self.create_timer(1.0 / p['fps'], self._grab)
        self.get_logger().info(f"wrist RGB camera on {p['device']} -> {p['color_topic']}")

    def _grab(self):
        ok, frame = self._cap.read()
        if not ok:
            self.get_logger().warn('no frame from the wrist camera', throttle_duration_sec=5.0)
            return
        h, w = frame.shape[:2]
        stamp = self.get_clock().now().to_msg()
        image = self._bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        image.header.stamp, image.header.frame_id = stamp, self._frame_id
        info = camera_info(w, h, self._intrinsics or intrinsics_from_fov(w, h, self._hfov), self._frame_id)
        info.header.stamp = stamp
        self._image_pub.publish(image)
        self._info_pub.publish(info)


def main(args=None):
    rclpy.init(args=args)
    node = WristRgbCameraNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node._cap.release()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
