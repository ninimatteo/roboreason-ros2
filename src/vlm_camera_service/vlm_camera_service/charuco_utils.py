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
    if hasattr(cv2.aruco, "CharucoBoard"):
        board = cv2.aruco.CharucoBoard(
            (config.squares_x, config.squares_y),
            config.square_length_m,
            config.marker_length_m,
            dictionary,
        )
    elif hasattr(cv2.aruco, "CharucoBoard_create"):
        board = cv2.aruco.CharucoBoard_create(
            config.squares_x,
            config.squares_y,
            config.square_length_m,
            config.marker_length_m,
            dictionary,
        )
    else:
        raise ValueError(
            "OpenCV aruco module has no ChArUco board constructor; "
            "install opencv-contrib-python or python3-opencv with aruco support"
        )
    return board, dictionary


def create_detector_parameters():
    if hasattr(cv2.aruco, "DetectorParameters"):
        return cv2.aruco.DetectorParameters()
    if hasattr(cv2.aruco, "DetectorParameters_create"):
        return cv2.aruco.DetectorParameters_create()
    raise ValueError("OpenCV aruco module has no DetectorParameters API")


def detect_charuco_pose(
    bgr_image: np.ndarray,
    camera_info: CameraInfo,
    config: CharucoConfig,
) -> Optional[CharucoPose]:
    board, dictionary = create_charuco_board(config)
    camera_matrix, dist_coeffs = camera_info_to_matrices(camera_info)

    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    params = create_detector_parameters()
    marker_corners, marker_ids, _rejected = cv2.aruco.detectMarkers(
        gray, dictionary, parameters=params
    )

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

    pose_ok, rvec, tvec = cv2.aruco.estimatePoseCharucoBoard(
        charuco_corners,
        charuco_ids,
        board,
        camera_matrix,
        dist_coeffs,
        None,
        None,
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


def draw_charuco_overlay(
    frame: np.ndarray,
    pose: CharucoPose,
    axis_length_m: float,
) -> None:
    cv2.aruco.drawDetectedMarkers(frame, pose.marker_corners, pose.marker_ids)
    if pose.charuco_corners is not None and pose.charuco_ids is not None:
        cv2.aruco.drawDetectedCornersCharuco(
            frame, pose.charuco_corners, pose.charuco_ids
        )
    cv2.drawFrameAxes(
        frame,
        pose.camera_matrix,
        pose.dist_coeffs,
        pose.rvec,
        pose.tvec,
        axis_length_m,
        3,
    )
