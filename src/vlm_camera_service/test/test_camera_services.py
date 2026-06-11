import pytest

from geometry_msgs.msg import Point
from vlm_camera_service.charuco_utils import CharucoPose, camera_point_to_charuco
from vlm_camera_service.camera_services_node import (
    deproject_pixel_to_3d,
    median_depth_at_pixel,
)


def test_deproject_center_pixel_is_on_z_axis() -> None:
    point = deproject_pixel_to_3d(
        u=320,
        v=240,
        z=1.0,
        fx=600.0,
        fy=600.0,
        cx=320.0,
        cy=240.0,
    )

    assert isinstance(point, Point)
    assert point.x == pytest.approx(0.0)
    assert point.y == pytest.approx(0.0)
    assert point.z == pytest.approx(1.0)


def test_deproject_right_and_down_are_positive_in_optical_convention() -> None:
    point = deproject_pixel_to_3d(
        u=420,
        v=340,
        z=2.0,
        fx=500.0,
        fy=500.0,
        cx=320.0,
        cy=240.0,
    )

    assert point.x == pytest.approx(0.4)
    assert point.y == pytest.approx(0.4)
    assert point.z == pytest.approx(2.0)


def test_median_depth_uses_real_16uc1_millimeters() -> None:
    import numpy as np

    depth = np.array(
        [
            [0, 600, 610],
            [590, 600, 10000],
            [610, 620, 0],
        ],
        dtype=np.uint16,
    )

    depth_m = median_depth_at_pixel(
        depth_image=depth,
        u=1,
        v=1,
        window_size=3,
        encoding="16UC1",
        min_depth_m=0.15,
        max_depth_m=3.0,
    )

    assert depth_m == pytest.approx(0.605)


def test_camera_point_to_charuco_identity_pose() -> None:
    import numpy as np

    point = Point(x=0.1, y=0.2, z=0.3)
    pose = CharucoPose(
        rvec=np.zeros((3, 1), dtype=np.float64),
        tvec=np.zeros((3, 1), dtype=np.float64),
        camera_matrix=np.eye(3, dtype=np.float64),
        dist_coeffs=np.zeros((5,), dtype=np.float64),
        marker_corners=(),
        marker_ids=None,
        charuco_corners=None,
        charuco_ids=None,
    )

    converted = camera_point_to_charuco(point, pose)

    assert converted.x == pytest.approx(point.x)
    assert converted.y == pytest.approx(point.y)
    assert converted.z == pytest.approx(point.z)
