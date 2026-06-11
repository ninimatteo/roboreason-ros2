from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image
from vlm_camera_interfaces.msg import PixelArray

from vlm_camera_service.charuco_utils import (
    CharucoConfig,
    detect_charuco_pose,
    draw_charuco_overlay,
)


class PixelOverlayViewerNode(Node):
    """Show the live RGB image and persist the latest VLM/debug pixels."""

    def __init__(self) -> None:
        super().__init__("pixel_overlay_viewer_node")

        self.declare_parameter("color_topic", "/camera/color/image_raw")
        self.declare_parameter("camera_info_topic", "/camera/color/camera_info")
        self.declare_parameter("pixel_topic", "/camera/debug_pixels")
        self.declare_parameter("window_name", "VLM pixels on RGB")
        self.declare_parameter("marker_size", 14)
        self.declare_parameter("line_thickness", 2)
        self.declare_parameter("charuco_enabled", True)
        self.declare_parameter("charuco_dictionary", "DICT_6X6_250")
        self.declare_parameter("charuco_squares_x", 5)
        self.declare_parameter("charuco_squares_y", 7)
        self.declare_parameter("charuco_square_length_m", 0.03)
        self.declare_parameter("charuco_marker_length_m", 0.015)
        self.declare_parameter("charuco_axis_length_m", 0.08)
        self.declare_parameter("charuco_min_corners", 4)

        color_topic = self.get_parameter("color_topic").value
        camera_info_topic = self.get_parameter("camera_info_topic").value
        pixel_topic = self.get_parameter("pixel_topic").value
        self._window_name = self.get_parameter("window_name").value
        self._marker_size = int(self.get_parameter("marker_size").value)
        self._line_thickness = int(self.get_parameter("line_thickness").value)
        self._charuco_enabled = bool(self.get_parameter("charuco_enabled").value)
        self._charuco_config = CharucoConfig(
            dictionary_name=self.get_parameter("charuco_dictionary").value,
            squares_x=int(self.get_parameter("charuco_squares_x").value),
            squares_y=int(self.get_parameter("charuco_squares_y").value),
            square_length_m=float(
                self.get_parameter("charuco_square_length_m").value
            ),
            marker_length_m=float(
                self.get_parameter("charuco_marker_length_m").value
            ),
            axis_length_m=float(self.get_parameter("charuco_axis_length_m").value),
            min_corners=int(self.get_parameter("charuco_min_corners").value),
        )

        self._latest_pixels: list[tuple[int, int]] = []
        self._latest_pixel_stamp = None
        self._latest_camera_info: Optional[CameraInfo] = None
        self._window_created = False
        self._last_charuco_detected = False
        self._last_charuco_error = ""

        self._color_sub = self.create_subscription(Image, color_topic, self._on_image, 10)
        self._camera_info_sub = self.create_subscription(
            CameraInfo, camera_info_topic, self._on_camera_info, 10
        )
        self._pixel_sub = self.create_subscription(
            PixelArray, pixel_topic, self._on_pixels, 10
        )

        self.get_logger().info("Pixel overlay viewer started")
        self.get_logger().info(f"color_topic={color_topic}")
        self.get_logger().info(f"camera_info_topic={camera_info_topic}")
        self.get_logger().info(f"pixel_topic={pixel_topic}")
        self.get_logger().info(
            "charuco="
            f"{self._charuco_enabled}, dictionary={self._charuco_config.dictionary_name}, "
            f"squares={self._charuco_config.squares_x}x{self._charuco_config.squares_y}"
        )
        self.get_logger().info(
            "Window stays open and keeps the latest pixels until new pixels arrive"
        )

    def _on_camera_info(self, msg: CameraInfo) -> None:
        self._latest_camera_info = msg

    def _on_pixels(self, msg: PixelArray) -> None:
        if len(msg.u) != len(msg.v):
            self.get_logger().warn("Ignoring PixelArray: u and v lengths differ")
            return
        self._latest_pixels = [(int(u), int(v)) for u, v in zip(msg.u, msg.v)]
        self._latest_pixel_stamp = msg.header.stamp
        self.get_logger().info(f"Received {len(self._latest_pixels)} pixels")

    def _on_image(self, msg: Image) -> None:
        try:
            frame = image_msg_to_bgr(msg)
        except ValueError as exc:
            self.get_logger().error(str(exc))
            return

        if self._charuco_enabled and self._latest_camera_info is not None:
            try:
                pose = detect_charuco_pose(
                    bgr_image=frame,
                    camera_info=self._latest_camera_info,
                    config=self._charuco_config,
                )
            except Exception as exc:
                error = str(exc)
                if error != self._last_charuco_error:
                    self.get_logger().warn(f"ChArUco overlay disabled: {error}")
                    self._last_charuco_error = error
                pose = None

            if pose is not None:
                self._last_charuco_error = ""
                draw_charuco_overlay(
                    frame=frame,
                    pose=pose,
                    axis_length_m=self._charuco_config.axis_length_m,
                )
                if not self._last_charuco_detected:
                    self.get_logger().info("ChArUco board detected")
                self._last_charuco_detected = True
            else:
                self._last_charuco_detected = False

        draw_pixels(
            frame=frame,
            pixels=self._latest_pixels,
            marker_size=self._marker_size,
            line_thickness=self._line_thickness,
        )

        if not self._window_created:
            cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
            self._window_created = True

        cv2.imshow(self._window_name, frame)
        cv2.waitKey(1)

    def destroy_node(self) -> bool:
        if self._window_created:
            cv2.destroyWindow(self._window_name)
        return super().destroy_node()


def image_msg_to_bgr(msg: Image) -> np.ndarray:
    if msg.encoding not in {"rgb8", "bgr8", "mono8"}:
        raise ValueError(f"Unsupported RGB image encoding '{msg.encoding}'")

    channels = 1 if msg.encoding == "mono8" else 3
    dtype = np.dtype(np.uint8)
    expected_min_size = int(msg.step) * int(msg.height)
    if len(msg.data) < expected_min_size:
        raise ValueError("Image data is shorter than height * step")

    row_values = int(msg.step) // dtype.itemsize
    raw = np.frombuffer(bytes(msg.data), dtype=dtype, count=row_values * msg.height)
    raw = raw.reshape((msg.height, row_values))
    pixels = raw[:, : msg.width * channels]

    if channels == 1:
        mono = pixels.reshape((msg.height, msg.width))
        return cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)

    rgb_or_bgr = pixels.reshape((msg.height, msg.width, 3))
    if msg.encoding == "rgb8":
        return cv2.cvtColor(rgb_or_bgr, cv2.COLOR_RGB2BGR)
    return rgb_or_bgr.copy()


def draw_pixels(
    frame: np.ndarray,
    pixels: list[tuple[int, int]],
    marker_size: int,
    line_thickness: int,
) -> None:
    if marker_size <= 0:
        marker_size = 14
    radius = marker_size // 2
    height, width = frame.shape[:2]

    for index, (u, v) in enumerate(pixels, start=1):
        if u < 0 or v < 0 or u >= width or v >= height:
            continue

        x0 = max(0, u - radius)
        y0 = max(0, v - radius)
        x1 = min(width - 1, u + radius)
        y1 = min(height - 1, v + radius)

        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 255, 255), line_thickness)
        cv2.drawMarker(
            frame,
            (u, v),
            (0, 0, 255),
            markerType=cv2.MARKER_CROSS,
            markerSize=max(marker_size, 10),
            thickness=line_thickness,
        )
        cv2.putText(
            frame,
            str(index),
            (min(width - 1, x1 + 4), max(12, y0 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )


def main(args: Optional[list[str]] = None) -> None:
    rclpy.init(args=args)
    node = PixelOverlayViewerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
