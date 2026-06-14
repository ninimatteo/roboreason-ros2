from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from geometry_msgs.msg import Point
from sensor_msgs.msg import CameraInfo


@dataclass(frozen=True)
class CharucoConfig:
    dictionary_name: str
    squares_x: int
    squares_y: int
    square_length_m: float
    marker_length_m: float
    axis_length_m: float
    min_corners: int
    z_sign: float = -1.0


@dataclass(frozen=True)
class CharucoPose:
    rvec: np.ndarray
    tvec: np.ndarray
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    marker_corners: tuple
    marker_ids: Optional[np.ndarray]
    charuco_corners: Optional[np.ndarray]
    charuco_ids: Optional[np.ndarray]


def camera_info_to_matrices(camera_info: CameraInfo) -> tuple[np.ndarray, np.ndarray]:
    camera_matrix = np.array(camera_info.k, dtype=np.float64).reshape((3, 3))
    dist_coeffs = np.array(camera_info.d, dtype=np.float64)
    if dist_coeffs.size == 0:
        dist_coeffs = np.zeros((5,), dtype=np.float64)
    return camera_matrix, dist_coeffs


def create_charuco_board(config: CharucoConfig):
    if not hasattr(cv2, "aruco"):
        raise ValueError("OpenCV aruco module not available; install opencv-contrib-python")

    dictionary_id = getattr(cv2.aruco, config.dictionary_name, None)
    if dictionary_id is None:
        raise ValueError(f"Unknown ArUco dictionary '{config.dictionary_name}'")

    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    try:
        board = cv2.aruco.CharucoBoard(
            (config.squares_x, config.squares_y),
            config.square_length_m,
            config.marker_length_m,
            dictionary,
        )
    except Exception:
        board = cv2.aruco.CharucoBoard_create(
            config.squares_x,
            config.squares_y,
            config.square_length_m,
            config.marker_length_m,
            dictionary,
        )
    return board, dictionary


def create_detector_parameters():
    if hasattr(cv2.aruco, "DetectorParameters"):
        params = cv2.aruco.DetectorParameters()
    elif hasattr(cv2.aruco, "DetectorParameters_create"):
        params = cv2.aruco.DetectorParameters_create()
    else:
        raise ValueError("OpenCV aruco module has no DetectorParameters API")
    # Disable sub-pixel corner refinement – produces a more stable pose estimate
    if hasattr(cv2.aruco, "CORNER_REFINE_NONE"):
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_NONE
    return params


def get_charuco_object_points(board) -> np.ndarray:
    """Return the 3-D object points of all ChArUco inner corners."""
    if hasattr(board, "getChessboardCorners"):
        return np.asarray(board.getChessboardCorners(), dtype=np.float32)
    if hasattr(board, "chessboardCorners"):
        return np.asarray(board.chessboardCorners, dtype=np.float32)
    raise RuntimeError("Cannot retrieve chessboard corners from ChArUco board")


def _detect_markers(gray: np.ndarray, dictionary, params):
    """Detect ArUco markers using new API (OpenCV 4.7+) with fallback to old API."""
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(dictionary, params)
        return detector.detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, dictionary, parameters=params)


def _estimate_pose_solvepnp(
    board,
    charuco_corners: np.ndarray,
    charuco_ids: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> tuple[bool, Optional[np.ndarray], Optional[np.ndarray]]:
    """Estimate board pose via solvePnP – more stable than estimatePoseCharucoBoard."""
    if charuco_corners is None or charuco_ids is None or len(charuco_ids) < 4:
        return False, None, None

    all_object_points = get_charuco_object_points(board)
    ids = charuco_ids.flatten().astype(np.int32)
    object_points = all_object_points[ids].astype(np.float32)
    image_points = charuco_corners.reshape(-1, 2).astype(np.float32)

    if len(object_points) < 4:
        return False, None, None

    valid, rvec, tvec = cv2.solvePnP(
        object_points,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    return bool(valid), rvec, tvec


def detect_charuco_pose(
    bgr_image: np.ndarray,
    camera_info: CameraInfo,
    config: CharucoConfig,
) -> Optional[CharucoPose]:
    board, dictionary = create_charuco_board(config)
    camera_matrix, dist_coeffs = camera_info_to_matrices(camera_info)

    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    params = create_detector_parameters()
    marker_corners, marker_ids, _rejected = _detect_markers(gray, dictionary, params)

    if marker_ids is None or len(marker_ids) == 0:
        return None

    retval, charuco_corners, charuco_ids = cv2.aruco.interpolateCornersCharuco(
        marker_corners,
        marker_ids,
        gray,
        board,
        cameraMatrix=camera_matrix,
        distCoeffs=dist_coeffs,
    )
    if not retval or charuco_corners is None or charuco_ids is None:
        return None
    if len(charuco_ids) < config.min_corners:
        return None

    pose_ok, rvec, tvec = _estimate_pose_solvepnp(
        board, charuco_corners, charuco_ids, camera_matrix, dist_coeffs
    )
    if not pose_ok:
        return None

    return CharucoPose(
        rvec=np.asarray(rvec, dtype=np.float64),
        tvec=np.asarray(tvec, dtype=np.float64),
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        marker_corners=marker_corners,
        marker_ids=marker_ids,
        charuco_corners=charuco_corners,
        charuco_ids=charuco_ids,
    )


def camera_point_to_charuco(point: Point, pose: CharucoPose) -> Point:
    rotation, _jacobian = cv2.Rodrigues(pose.rvec)
    point_camera = np.array([[point.x], [point.y], [point.z]], dtype=np.float64)
    point_charuco = rotation.T @ (point_camera - pose.tvec.reshape((3, 1)))

    out = Point()
    out.x = float(point_charuco[0, 0])
    out.y = float(point_charuco[1, 0])
    out.z = float(point_charuco[2, 0])
    return out


# ── Camera ↔ base_link calibration via ArUco ──────────────────────────────────

def rpy_to_rotation_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Intrinsic XYZ roll-pitch-yaw (radians) → 3×3 rotation matrix."""
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr,  cr]], dtype=np.float64)
    Ry = np.array([[cp, 0, sp], [0,  1,   0], [-sp, 0, cp]], dtype=np.float64)
    Rz = np.array([[cy, -sy, 0], [sy, cy,  0], [0,   0,  1]], dtype=np.float64)
    return Rz @ Ry @ Rx


def board_pose_to_matrix(
    x: float, y: float, z: float,
    roll: float, pitch: float, yaw: float,
) -> np.ndarray:
    """Build 4×4 homogeneous T_base_board from position (m) and RPY (rad)."""
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = rpy_to_rotation_matrix(roll, pitch, yaw)
    T[:3, 3] = [x, y, z]
    return T


def charuco_pose_to_matrix(pose: CharucoPose) -> np.ndarray:
    """Convert a CharucoPose (rvec, tvec) to a 4×4 T_cam_board matrix.

    T_cam_board transforms a point expressed in board frame to camera frame:
        P_cam = T_cam_board @ [P_board; 1]
    """
    R, _ = cv2.Rodrigues(pose.rvec)
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = pose.tvec.flatten()
    return T


def compute_T_base_camera(
    pose: CharucoPose,
    T_base_board: np.ndarray,
) -> np.ndarray:
    """Compute the 4×4 T_base_camera transform (camera frame → robot base_link).

    The ArUco board sits at a fixed, known position relative to the robot base.
    ArUco detection gives us the board's pose in camera frame (T_cam_board).
    Together they yield the camera's pose in base frame:

        T_base_cam = T_base_board @ inv(T_cam_board)

    Returns a 4×4 matrix M such that:
        P_base = M @ [P_cam_x, P_cam_y, P_cam_z, 1]
    """
    T_cam_board = charuco_pose_to_matrix(pose)
    T_board_cam = np.linalg.inv(T_cam_board)
    return T_base_board @ T_board_cam


def transform_point_to_base(point: Point, T_base_camera: np.ndarray) -> Point:
    """Apply T_base_camera (4×4) to a camera-frame Point → base-frame Point."""
    p = np.array([point.x, point.y, point.z, 1.0], dtype=np.float64)
    p_base = T_base_camera @ p
    out = Point()
    out.x = float(p_base[0])
    out.y = float(p_base[1])
    out.z = float(p_base[2])
    return out


def rotation_matrix_to_quaternion(R: np.ndarray):
    """3×3 rotation matrix → (x, y, z, w) quaternion (Shepperd method)."""
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return float(x), float(y), float(z), float(w)


def T_to_transform_stamped(
    T: np.ndarray,
    parent_frame: str,
    child_frame: str,
    stamp,
):
    """4×4 homogeneous matrix → geometry_msgs/TransformStamped."""
    from geometry_msgs.msg import TransformStamped
    x, y, z, w = rotation_matrix_to_quaternion(T[:3, :3])
    msg = TransformStamped()
    msg.header.stamp = stamp
    msg.header.frame_id = parent_frame
    msg.child_frame_id = child_frame
    msg.transform.translation.x = float(T[0, 3])
    msg.transform.translation.y = float(T[1, 3])
    msg.transform.translation.z = float(T[2, 3])
    msg.transform.rotation.x = x
    msg.transform.rotation.y = y
    msg.transform.rotation.z = z
    msg.transform.rotation.w = w
    return msg


# ── Overlay drawing helpers ────────────────────────────────────────────────────

def _draw_board_outline(
    frame: np.ndarray,
    pose: CharucoPose,
    config: CharucoConfig,
) -> None:
    """Draw the rectangular outer edge of the ChArUco board (cyan)."""
    board_width = config.squares_x * config.square_length_m
    board_height = config.squares_y * config.square_length_m

    board_corners_3d = np.float32([
        [0.0, 0.0, 0.0],
        [board_width, 0.0, 0.0],
        [board_width, board_height, 0.0],
        [0.0, board_height, 0.0],
    ])

    projected, _ = cv2.projectPoints(
        board_corners_3d,
        pose.rvec,
        pose.tvec,
        pose.camera_matrix,
        pose.dist_coeffs,
    )
    # .astype(np.int32).tolist() produces pure Python int objects, which is the
    # only form accepted by cv2.line in OpenCV 4.5.x Python bindings.
    pts = np.round(projected.reshape(-1, 2)).astype(np.int32).tolist()

    for i in range(4):
        p1 = (pts[i][0], pts[i][1])
        p2 = (pts[(i + 1) % 4][0], pts[(i + 1) % 4][1])
        cv2.line(frame, p1, p2, (255, 255, 0), 2)


def _draw_manual_axes(
    frame: np.ndarray,
    pose: CharucoPose,
    axis_length_m: float,
    z_sign: float = -1.0,
) -> None:
    """Draw XYZ axes using projectPoints (more stable than drawFrameAxes).

    X = red, Y = green, Z = blue.
    z_sign=-1 points Z toward the camera (out of the board surface toward viewer).
    """
    axis_3d = np.float32([
        [0.0, 0.0, 0.0],
        [axis_length_m, 0.0, 0.0],
        [0.0, axis_length_m, 0.0],
        [0.0, 0.0, z_sign * axis_length_m],
    ])

    projected, _ = cv2.projectPoints(
        axis_3d,
        pose.rvec,
        pose.tvec,
        pose.camera_matrix,
        pose.dist_coeffs,
    )
    # .astype(np.int32).tolist() produces pure Python int objects, which is the
    # only form accepted by cv2.line in OpenCV 4.5.x Python bindings.
    pts = np.round(projected.reshape(-1, 2)).astype(np.int32).tolist()

    origin = (pts[0][0], pts[0][1])
    x_pt   = (pts[1][0], pts[1][1])
    y_pt   = (pts[2][0], pts[2][1])
    z_pt   = (pts[3][0], pts[3][1])

    # Origin marker
    cv2.circle(frame, origin, 6, (255, 255, 255), -1)
    cv2.circle(frame, origin, 8, (0, 0, 0), 2)

    # X axis – red
    cv2.line(frame, origin, x_pt, (0, 0, 255), 4)
    cv2.putText(frame, "X", x_pt, cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 3)

    # Y axis – green
    cv2.line(frame, origin, y_pt, (0, 255, 0), 4)
    cv2.putText(frame, "Y", y_pt, cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 3)

    # Z axis – blue
    cv2.line(frame, origin, z_pt, (255, 0, 0), 4)
    cv2.putText(frame, "Z", z_pt, cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 0, 0), 3)


def draw_charuco_overlay(
    frame: np.ndarray,
    pose: CharucoPose,
    config: CharucoConfig,
) -> None:
    """Draw detected markers, ChArUco corners, board outline, and XYZ axes onto frame."""
    cv2.aruco.drawDetectedMarkers(frame, pose.marker_corners, pose.marker_ids)
    if pose.charuco_corners is not None and pose.charuco_ids is not None:
        cv2.aruco.drawDetectedCornersCharuco(
            frame, pose.charuco_corners, pose.charuco_ids
        )
    _draw_board_outline(frame, pose, config)
    _draw_manual_axes(frame, pose, config.axis_length_m, z_sign=config.z_sign)
