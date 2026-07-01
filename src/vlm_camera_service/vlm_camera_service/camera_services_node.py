from __future__ import annotations

from typing import Optional

import numpy as np
import rclpy
import tf2_ros
from geometry_msgs.msg import Point
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Bool
from robo_reason_interfaces.msg import PixelArray
from robo_reason_interfaces.srv import Deproject, GetImage

from std_srvs.srv import Trigger

from robo_reason_bringup.config import settings
from vlm_camera_service.charuco_utils import (
    CharucoConfig,
    T_to_transform_stamped,
    board_pose_to_matrix,
    camera_point_to_charuco,
    compute_T_base_camera,
    detect_charuco_pose,
    transform_point_to_base,
)


_CALIB_REQUIRED_HITS = 5       # consecutive consistent detections to lock
_CALIB_CONSISTENCY_M = 0.005   # max translation deviation between hits (5 mm)


class CameraServicesNode(Node):
    """Camera service bridge with real RGB/depth topics and pixel callers."""

    def __init__(self) -> None:
        super().__init__("camera_services_node")

        self.declare_parameter("color_topic", settings.COLOR_TOPIC)
        self.declare_parameter("depth_topic", settings.DEPTH_TOPIC)
        self.declare_parameter("camera_info_topic", settings.CAMERA_INFO_TOPIC)
        self.declare_parameter("window_size", settings.WINDOW_SIZE)
        self.declare_parameter("min_depth_m", settings.MIN_DEPTH_M)
        self.declare_parameter("max_depth_m", settings.MAX_DEPTH_M)
        self.declare_parameter("get_image_service", settings.GET_IMAGE_SERVICE)
        self.declare_parameter("deproject_service", settings.DEPROJECT_SERVICE)
        self.declare_parameter("pixel_debug_topic", settings.PIXEL_DEBUG_TOPIC)
        self.declare_parameter("charuco_axis_topic", settings.CHARUCO_AXIS_TOPIC)
        self.declare_parameter("calibration_status_topic", settings.CALIBRATION_STATUS_TOPIC)
        self.declare_parameter("charuco_enabled", settings.CHARUCO_ENABLED)
        self.declare_parameter("charuco_dictionary", settings.CHARUCO_DICTIONARY)
        self.declare_parameter("charuco_squares_x", settings.CHARUCO_SQUARES_X)
        self.declare_parameter("charuco_squares_y", settings.CHARUCO_SQUARES_Y)
        self.declare_parameter("charuco_square_length_m", settings.CHARUCO_SQUARE_LENGTH_M)
        self.declare_parameter("charuco_marker_length_m", settings.CHARUCO_MARKER_LENGTH_M)
        self.declare_parameter("charuco_axis_length_m", settings.CHARUCO_AXIS_LENGTH_M)
        self.declare_parameter("charuco_min_corners", settings.CHARUCO_MIN_CORNERS)
        self.declare_parameter("charuco_z_sign", settings.CHARUCO_Z_SIGN)
        self.declare_parameter("charuco_frame_id", settings.CHARUCO_FRAME_ID)
        # Known pose of the ChArUco board in robot base_link frame (measured once).
        # Position in metres, rotation as intrinsic RPY in radians.
        # When these are set correctly the Deproject service returns base_link coords.
        self.declare_parameter("board_in_base_x",     settings.BOARD_IN_BASE_X)
        self.declare_parameter("board_in_base_y",     settings.BOARD_IN_BASE_Y)
        self.declare_parameter("board_in_base_z",     settings.BOARD_IN_BASE_Z)
        self.declare_parameter("board_in_base_roll",  settings.BOARD_IN_BASE_ROLL)
        self.declare_parameter("board_in_base_pitch", settings.BOARD_IN_BASE_PITCH)
        self.declare_parameter("board_in_base_yaw",   settings.BOARD_IN_BASE_YAW)
        # Extra Z offset added to base_link points before returning to the planner.
        self.declare_parameter("z_offset_m", settings.Z_OFFSET_M)

        color_topic = self.get_parameter("color_topic").value
        depth_topic = self.get_parameter("depth_topic").value
        camera_info_topic = self.get_parameter("camera_info_topic").value
        self._window_size = int(self.get_parameter("window_size").value)
        self._min_depth_m = float(self.get_parameter("min_depth_m").value)
        self._max_depth_m = float(self.get_parameter("max_depth_m").value)
        get_image_service = self.get_parameter("get_image_service").value
        deproject_service = self.get_parameter("deproject_service").value
        pixel_debug_topic = self.get_parameter("pixel_debug_topic").value
        charuco_axis_topic = self.get_parameter("charuco_axis_topic").value
        calibration_status_topic = self.get_parameter("calibration_status_topic").value
        self._charuco_enabled = bool(self.get_parameter("charuco_enabled").value)
        self._charuco_frame_id = self.get_parameter("charuco_frame_id").value
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
            z_sign=float(self.get_parameter("charuco_z_sign").value),
        )

        self._z_offset_m = float(self.get_parameter("z_offset_m").value)

        # Build the known board→base transform from parameters.
        self._T_base_board = board_pose_to_matrix(
            x=float(self.get_parameter("board_in_base_x").value),
            y=float(self.get_parameter("board_in_base_y").value),
            z=float(self.get_parameter("board_in_base_z").value),
            roll=float(self.get_parameter("board_in_base_roll").value),
            pitch=float(self.get_parameter("board_in_base_pitch").value),
            yaw=float(self.get_parameter("board_in_base_yaw").value),
        )
        # Cached camera→base_link transform.
        # Set once on the first successful ChArUco detection, then locked forever.
        self._T_base_camera: Optional[np.ndarray] = None
        self._charuco_calibrated: bool = False
        self._calib_hits: list = []   # accumulate consistent detections before locking
        # TF2 broadcaster — publishes camera optical frame → base_link.
        self._tf_broadcaster = tf2_ros.StaticTransformBroadcaster(self)

        self._latest_color: Optional[Image] = None
        self._latest_depth: Optional[Image] = None
        self._latest_camera_info: Optional[CameraInfo] = None

        self._color_sub = self.create_subscription(Image, color_topic, self._on_color, qos_profile_sensor_data)
        self._depth_sub = self.create_subscription(Image, depth_topic, self._on_depth, qos_profile_sensor_data)
        self._camera_info_sub = self.create_subscription(
            CameraInfo,
            camera_info_topic,
            self._on_camera_info,
            qos_profile_sensor_data,
        )
        self._pixel_debug_pub = self.create_publisher(PixelArray, pixel_debug_topic, 10)
        self._charuco_axis_pub = self.create_publisher(PixelArray, charuco_axis_topic, 10)
        self._calibration_status_pub = self.create_publisher(Bool, calibration_status_topic, 10)
        self.create_service(GetImage, get_image_service, self._handle_get_image)
        self.create_service(Deproject, deproject_service, self._handle_deproject)
        self.create_service(Trigger, '/camera/recalibrate', self._handle_recalibrate)

        self.get_logger().info("Camera service bridge started")
        self.get_logger().info(f"color_topic={color_topic}")
        self.get_logger().info(f"depth_topic={depth_topic}")
        self.get_logger().info(f"camera_info_topic={camera_info_topic}")
        self.get_logger().info(f"{get_image_service}: returns latest real RGB image")
        self.get_logger().info(
            f"{deproject_service}: returns real depth deprojection in camera frame"
        )
        self.get_logger().info(f"pixel_debug_topic={pixel_debug_topic}")
        self.get_logger().info(f"calibration_status_topic={calibration_status_topic}")
        self.get_logger().info(
            "charuco="
            f"{self._charuco_enabled}, dictionary={self._charuco_config.dictionary_name}, "
            f"squares={self._charuco_config.squares_x}x{self._charuco_config.squares_y}, "
            f"square={self._charuco_config.square_length_m:.4f} m, "
            f"marker={self._charuco_config.marker_length_m:.4f} m"
        )
        self.get_logger().info(
            f"window_size={self._window_size}, "
            f"valid depth range=[{self._min_depth_m:.3f}, {self._max_depth_m:.3f}] m"
        )
        self._status_timer = self.create_timer(2.0, self._log_input_status)
        # One-shot calibration: try every 0.5 s until the board is detected once,
        # then lock the transform and cancel this timer.
        self._calib_timer = self.create_timer(0.5, self._try_calibrate)
        # Publish projected ChArUco axis keypoints at ~2 Hz for the GUI overlay.
        self.create_timer(0.5, self._publish_charuco_axis)
        self.get_logger().info(f"charuco_axis_topic={charuco_axis_topic}")
        # Publish calibration-lock status at ~2 Hz so the GUI LED reflects the
        # current state (and flips back to "not calibrated" immediately after
        # a recalibrate request, rather than showing a stale locked state).
        self.create_timer(0.5, self._publish_calibration_status)

    def _on_color(self, msg: Image) -> None:
        if self._latest_color is None:
            self.get_logger().info(
                f"received first RGB image: {msg.width}x{msg.height}, "
                f"encoding={msg.encoding}, frame={msg.header.frame_id}"
            )
        self._latest_color = msg

    def _on_depth(self, msg: Image) -> None:
        if self._latest_depth is None:
            self.get_logger().info(
                f"received first depth image: {msg.width}x{msg.height}, "
                f"encoding={msg.encoding}, frame={msg.header.frame_id}"
            )
        self._latest_depth = msg

    def _on_camera_info(self, msg: CameraInfo) -> None:
        if self._latest_camera_info is None:
            self.get_logger().info(
                f"received first CameraInfo: {msg.width}x{msg.height}, "
                f"frame={msg.header.frame_id}"
            )
        self._latest_camera_info = msg

    def _log_input_status(self) -> None:
        missing = []
        if self._latest_color is None:
            missing.append("RGB")
        if self._latest_depth is None:
            missing.append("depth")
        if self._latest_camera_info is None:
            missing.append("CameraInfo")
        if missing:
            self.get_logger().warn(
                "waiting for camera inputs: " + ", ".join(missing)
            )

    def _handle_get_image(
        self, _request: GetImage.Request, response: GetImage.Response
    ) -> GetImage.Response:
        if self._latest_color is None:
            response.success = False
            response.frame_id = ""
            response.error_message = "RGB image not received yet"
            return response

        image = self._latest_color
        response.success = True
        response.image = image
        response.frame_id = image.header.frame_id
        response.error_message = ""
        self.get_logger().info(
            f"GetImage -> {image.width}x{image.height}, "
            f"encoding={image.encoding}, frame={image.header.frame_id}"
        )
        return response

    def _handle_deproject(
        self, request: Deproject.Request, response: Deproject.Response
    ) -> Deproject.Response:
        response.frame_id = self._camera_frame_id()
        response.charuco_pose_available = False
        response.charuco_points = []
        response.charuco_frame_id = self._charuco_frame_id

        if len(request.u) != len(request.v):
            response.success = False
            response.points = []
            response.error_message = "u and v arrays must have the same length"
            return response

        self._publish_debug_pixels(request.u, request.v)

        if self._latest_depth is None:
            response.success = False
            response.points = []
            response.error_message = "Depth image not received yet"
            return response

        if self._latest_camera_info is None:
            response.success = False
            response.points = []
            response.error_message = "CameraInfo not received yet"
            return response

        try:
            depth_image = image_msg_to_depth_array(self._latest_depth)
            intrinsics = camera_info_to_intrinsics(self._latest_camera_info)
            points = []
            for u_raw, v_raw in zip(request.u, request.v):
                u = int(u_raw)
                v = int(v_raw)
                depth_m = median_depth_at_pixel(
                    depth_image=depth_image,
                    u=u,
                    v=v,
                    window_size=self._window_size,
                    encoding=self._latest_depth.encoding,
                    min_depth_m=self._min_depth_m,
                    max_depth_m=self._max_depth_m,
                )
                if depth_m is None:
                    response.success = False
                    response.points = []
                    response.error_message = f"No valid depth at u={u}, v={v}"
                    return response
                points.append(
                    deproject_pixel_to_3d(
                        u=u,
                        v=v,
                        z=depth_m,
                        fx=intrinsics.fx,
                        fy=intrinsics.fy,
                        cx=intrinsics.cx,
                        cy=intrinsics.cy,
                    )
                )
        except ValueError as exc:
            response.success = False
            response.points = []
            response.error_message = str(exc)
            return response

        # Try to update T_base_camera from ArUco before deciding the output frame.
        self._fill_charuco_points(points, response)

        if self._T_base_camera is not None:
            base_points = [transform_point_to_base(p, self._T_base_camera) for p in points]
            if self._z_offset_m != 0.0:
                for p in base_points:
                    p.z += self._z_offset_m
            response.success = True
            response.points = base_points
            response.frame_id = "base_link"
        else:
            self.get_logger().warn(
                "No camera→base_link transform yet — returning camera-frame points. "
                "Make sure the ArUco board is visible and board_in_base_* params are set."
            )
            response.success = True
            response.points = points
            response.frame_id = self._camera_frame_id()

        response.error_message = ""
        self.get_logger().info(
            f"Deproject -> {len(points)} points in {response.frame_id}, "
            f"charuco_pose_available={response.charuco_pose_available}"
        )
        return response

    def _try_calibrate(self) -> None:
        """Periodic callback: accumulate consistent ChArUco detections before locking.

        Requires _CALIB_REQUIRED_HITS consecutive detections whose translation
        vectors agree within _CALIB_CONSISTENCY_M (5 mm).  Any inconsistent
        detection resets the counter so a single noisy frame can't corrupt the
        locked transform.  Once enough consistent hits accumulate the translation
        is averaged, the last rotation is used, and the timer cancels itself.
        """
        if self._charuco_calibrated:
            self._calib_timer.cancel()
            return
        if not self._charuco_enabled:
            self._calib_timer.cancel()
            return
        if self._latest_color is None or self._latest_camera_info is None:
            return  # camera not ready yet — keep trying

        try:
            bgr = image_msg_to_bgr(self._latest_color)
            pose = detect_charuco_pose(
                bgr_image=bgr,
                camera_info=self._latest_camera_info,
                config=self._charuco_config,
            )
        except Exception as exc:
            self.get_logger().warn(f"[CameraServicesNode] ChArUco detection failed: {exc}")
            return

        if pose is None:
            if self._calib_hits:
                self.get_logger().warn(
                    f"[CameraServicesNode] Board lost during calibration — resetting "
                    f"({len(self._calib_hits)} hits discarded)"
                )
                self._calib_hits = []
            return

        T_candidate = compute_T_base_camera(pose, self._T_base_board)
        t_candidate = T_candidate[:3, 3]

        if not self._calib_hits:
            self._calib_hits = [T_candidate]
            self.get_logger().info(
                f"[CameraServicesNode] Calibration: first detection — "
                f"need {_CALIB_REQUIRED_HITS} consistent hits"
            )
        else:
            mean_t = np.mean([T[:3, 3] for T in self._calib_hits], axis=0)
            dist = float(np.linalg.norm(t_candidate - mean_t))
            if dist < _CALIB_CONSISTENCY_M:
                self._calib_hits.append(T_candidate)
                self.get_logger().info(
                    f"[CameraServicesNode] Calibration: {len(self._calib_hits)}/"
                    f"{_CALIB_REQUIRED_HITS} consistent hits "
                    f"(deviation={dist * 1000:.1f} mm)"
                )
            else:
                self._calib_hits = [T_candidate]
                self.get_logger().warn(
                    f"[CameraServicesNode] Calibration: inconsistent detection "
                    f"(deviation={dist * 1000:.1f} mm > {_CALIB_CONSISTENCY_M * 1000:.0f} mm) — "
                    "resetting hit counter"
                )

        if len(self._calib_hits) < _CALIB_REQUIRED_HITS:
            return

        # Enough consistent hits: average translations, keep last rotation.
        mean_t = np.mean([T[:3, 3] for T in self._calib_hits], axis=0)
        T_locked = self._calib_hits[-1].copy()
        T_locked[:3, 3] = mean_t
        self._T_base_camera = T_locked
        self._charuco_calibrated = True
        self._calib_hits = []
        self._calib_timer.cancel()

        tv = pose.tvec.flatten()
        board_base = self._T_base_board[:3, 3]
        cam_base = T_locked[:3, 3]
        self.get_logger().info(
            f"[CameraServicesNode] LOCKED after {_CALIB_REQUIRED_HITS} consistent hits — "
            f"tvec(cam_frame)=[{tv[0]:+.3f}, {tv[1]:+.3f}, {tv[2]:+.3f}] m  |  "
            f"board_origin(base_link)=[{board_base[0]:+.3f}, {board_base[1]:+.3f}, {board_base[2]:+.3f}] m  |  "
            f"camera(base_link)=[{cam_base[0]:+.3f}, {cam_base[1]:+.3f}, {cam_base[2]:+.3f}] m"
        )
        self._publish_camera_tf(T_locked)

    def _handle_recalibrate(self, _request: Trigger.Request, response: Trigger.Response):
        """Reset calibration state and restart the detection loop.

        Useful when the board was moved or the initial calibration locked onto a
        bad detection.  The old timer is cancelled before a new one is created —
        otherwise the previous timer keeps firing alongside the new one (it was
        never stopped), silently leaking timers and letting a stale in-flight
        callback race the fresh calibration attempt.
        """
        if self._calib_timer is not None:
            self._calib_timer.cancel()
        self._T_base_camera = None
        self._charuco_calibrated = False
        self._calib_hits = []
        self._calib_timer = self.create_timer(0.5, self._try_calibrate)
        self.get_logger().info(
            f'[CameraServicesNode] Calibration reset — '
            f'need {_CALIB_REQUIRED_HITS} consistent hits to lock'
        )
        response.success = True
        response.message = (
            f'Calibration reset. Need {_CALIB_REQUIRED_HITS} consistent '
            f'detections within {_CALIB_CONSISTENCY_M * 1000:.0f} mm to lock.'
        )
        return response

    def _fill_charuco_points(
        self, camera_points: list[Point], response: Deproject.Response
    ) -> None:
        """Populate charuco_points in the deproject response (best-effort, no pose update)."""
        if not self._charuco_enabled:
            return
        if not self._charuco_calibrated:
            # Calibration not yet done — skip; charuco_points will remain empty.
            return
        if self._latest_color is None or self._latest_camera_info is None:
            return

        try:
            bgr = image_msg_to_bgr(self._latest_color)
            pose = detect_charuco_pose(
                bgr_image=bgr,
                camera_info=self._latest_camera_info,
                config=self._charuco_config,
            )
        except Exception as exc:
            self.get_logger().warn(f"ChArUco pose unavailable: {exc}")
            return

        if pose is None:
            return

        response.charuco_pose_available = True
        response.charuco_points = [
            camera_point_to_charuco(point, pose) for point in camera_points
        ]
        response.charuco_frame_id = self._charuco_frame_id
        # NOTE: _T_base_camera is intentionally NOT updated here — it was locked
        # at startup by _try_calibrate and will not change during operation.

    def _publish_camera_tf(self, T_base_cam: np.ndarray) -> None:
        """Broadcast the camera optical frame → base_link static TF."""
        camera_frame = self._camera_frame_id() or "camera_optical_frame"
        tf_msg = T_to_transform_stamped(
            T_base_cam,
            parent_frame="base_link",
            child_frame=camera_frame,
            stamp=self.get_clock().now().to_msg(),
        )
        self._tf_broadcaster.sendTransform(tf_msg)
        t = T_base_cam[:3, 3]
        self.get_logger().info(
            f"[CameraServicesNode] camera→base_link TF published "
            f"(t=[{t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f}] m)"
        )

    def _publish_debug_pixels(self, u_values, v_values) -> None:
        msg = PixelArray()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._camera_frame_id()
        msg.u = [int(u) for u in u_values]
        msg.v = [int(v) for v in v_values]
        self._pixel_debug_pub.publish(msg)
        self.get_logger().info(f"Published {len(msg.u)} debug pixels")

    def _publish_calibration_status(self) -> None:
        """Publish whether the camera→base_link transform is currently locked."""
        msg = Bool()
        msg.data = bool(self._charuco_calibrated)
        self._calibration_status_pub.publish(msg)

    def _publish_charuco_axis(self) -> None:
        """Project the ChArUco board XYZ axis to 2-D pixel coords and publish.

        Publishes a PixelArray with exactly 4 points (or 0 if the board is not
        visible), encoding [origin, X-end, Y-end, Z-end] so the GUI can draw:
          origin(white) → X-end(red) → Y-end(green) → Z-end(blue)

        An empty message means "board not detected this frame."
        """
        import cv2

        msg = PixelArray()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._camera_frame_id()

        if not self._charuco_enabled or self._latest_color is None or self._latest_camera_info is None:
            self._charuco_axis_pub.publish(msg)
            return

        try:
            bgr = image_msg_to_bgr(self._latest_color)
            pose = detect_charuco_pose(
                bgr_image=bgr,
                camera_info=self._latest_camera_info,
                config=self._charuco_config,
            )
        except Exception:
            self._charuco_axis_pub.publish(msg)
            return

        if pose is None:
            self._charuco_axis_pub.publish(msg)
            return

        axis_len = self._charuco_config.axis_length_m
        z_sign = self._charuco_config.z_sign
        axis_3d = np.float32([
            [0.0, 0.0, 0.0],
            [axis_len, 0.0, 0.0],
            [0.0, axis_len, 0.0],
            [0.0, 0.0, z_sign * axis_len],
        ])
        try:
            projected, _ = cv2.projectPoints(
                axis_3d, pose.rvec, pose.tvec,
                pose.camera_matrix, pose.dist_coeffs,
            )
        except Exception:
            self._charuco_axis_pub.publish(msg)
            return

        pts = np.round(projected.reshape(-1, 2)).astype(np.int32).tolist()
        msg.u = [p[0] for p in pts]
        msg.v = [p[1] for p in pts]
        self._charuco_axis_pub.publish(msg)

    def _camera_frame_id(self) -> str:
        if self._latest_depth is not None and self._latest_depth.header.frame_id:
            return self._latest_depth.header.frame_id
        if (
            self._latest_camera_info is not None
            and self._latest_camera_info.header.frame_id
        ):
            return self._latest_camera_info.header.frame_id
        return ""


class CameraIntrinsics:
    def __init__(self, fx: float, fy: float, cx: float, cy: float) -> None:
        self.fx = fx
        self.fy = fy
        self.cx = cx
        self.cy = cy


def camera_info_to_intrinsics(camera_info: CameraInfo) -> CameraIntrinsics:
    k = camera_info.k
    if len(k) != 9:
        raise ValueError("camera_info.k must contain 9 values")
    fx = float(k[0])
    fy = float(k[4])
    cx = float(k[2])
    cy = float(k[5])
    if fx <= 0.0 or fy <= 0.0:
        raise ValueError("camera intrinsics fx and fy must be positive")
    return CameraIntrinsics(fx=fx, fy=fy, cx=cx, cy=cy)


def image_msg_to_depth_array(msg: Image) -> np.ndarray:
    if msg.encoding == "16UC1":
        dtype = np.dtype(np.uint16)
    elif msg.encoding == "32FC1":
        dtype = np.dtype(np.float32)
    else:
        raise ValueError(f"Unsupported depth encoding '{msg.encoding}'")

    if msg.is_bigendian:
        dtype = dtype.newbyteorder(">")

    expected_min_size = int(msg.step) * int(msg.height)
    if len(msg.data) < expected_min_size:
        raise ValueError("Depth image data is shorter than height * step")

    row_values = int(msg.step) // dtype.itemsize
    raw = np.frombuffer(bytes(msg.data), dtype=dtype, count=row_values * msg.height)
    raw = raw.reshape((msg.height, row_values))
    return raw[:, : msg.width]


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
        import cv2

        return cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)

    rgb_or_bgr = pixels.reshape((msg.height, msg.width, 3))
    if msg.encoding == "rgb8":
        import cv2

        return cv2.cvtColor(rgb_or_bgr, cv2.COLOR_RGB2BGR)
    return rgb_or_bgr.copy()


def median_depth_at_pixel(
    depth_image: np.ndarray,
    u: int,
    v: int,
    window_size: int,
    encoding: str,
    min_depth_m: float,
    max_depth_m: float,
) -> Optional[float]:
    valid_depths = extract_valid_depth_window(
        depth_image=depth_image,
        u=u,
        v=v,
        window_size=window_size,
        encoding=encoding,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
    )
    if valid_depths.size == 0:
        return None
    return float(np.median(valid_depths))


def extract_valid_depth_window(
    depth_image: np.ndarray,
    u: int,
    v: int,
    window_size: int,
    encoding: str,
    min_depth_m: float,
    max_depth_m: float,
) -> np.ndarray:
    if depth_image.ndim != 2:
        raise ValueError("depth_image must be a 2D single-channel array")
    if window_size <= 0 or window_size % 2 == 0:
        raise ValueError("window_size must be a positive odd integer")
    if encoding not in {"16UC1", "32FC1"}:
        raise ValueError(f"Unsupported depth encoding '{encoding}'")
    if max_depth_m <= min_depth_m:
        raise ValueError("max_depth_m must be greater than min_depth_m")

    height, width = depth_image.shape
    if u < 0 or v < 0 or u >= width or v >= height:
        return np.array([], dtype=np.float64)

    radius = window_size // 2
    x_min = max(0, u - radius)
    x_max = min(width, u + radius + 1)
    y_min = max(0, v - radius)
    y_max = min(height, v + radius + 1)

    window = depth_image[y_min:y_max, x_min:x_max]
    if encoding == "16UC1":
        depth_m = window.astype(np.float64) / 1000.0
    else:
        depth_m = window.astype(np.float64)

    valid = np.isfinite(depth_m)
    valid &= depth_m > 0.0
    valid &= depth_m >= min_depth_m
    valid &= depth_m <= max_depth_m
    return depth_m[valid].astype(np.float64)


def deproject_pixel_to_3d(
    u: int, v: int, z: float, fx: float, fy: float, cx: float, cy: float
) -> Point:
    if z <= 0.0:
        raise ValueError("z must be positive and expressed in meters")
    if fx <= 0.0 or fy <= 0.0:
        raise ValueError("fx and fy must be positive")
    point = Point()
    point.x = (float(u) - cx) * z / fx
    point.y = (float(v) - cy) * z / fy
    point.z = z
    return point


def main(args: Optional[list[str]] = None) -> None:
    rclpy.init(args=args)
    node = CameraServicesNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
