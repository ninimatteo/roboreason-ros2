"""Redo /camera/deproject offline on a frame saved by a debug run (ROBOAI-59).

A debug run saves, next to each RGB frame <stem>.png:
  <stem>_depth.png    the aligned depth frame, 16 bit, millimetres (0 = no depth)
  <stem>_camera.json  intrinsics, camera pose in base_link, deprojection settings

deproject() follows the same steps as CameraServicesNode._handle_deproject, with
the same functions, so a saved run gives the points the live service gave.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from vlm_camera_service.camera_services_node import (
    CameraIntrinsics,
    deproject_pixel_to_3d,
    local_plane_centroid,
    median_depth_at_pixel,
)


def load(frame_png: str | Path) -> dict:
    """The depth array (uint16, mm) and the camera json saved with a frame."""
    import cv2

    stem = str(frame_png)[:-len('.png')] if str(frame_png).endswith('.png') else str(frame_png)
    depth = cv2.imread(stem + '_depth.png', cv2.IMREAD_UNCHANGED)
    if depth is None:
        raise FileNotFoundError(f'{stem}_depth.png')
    camera = json.loads(Path(stem + '_camera.json').read_text())
    return {'depth': depth, **camera}


def deproject(snapshot: dict, us, vs) -> list:
    """base_link [x, y, z] per pixel, None where there is no valid depth."""
    if not snapshot['calibrated']:
        raise ValueError('the camera was not calibrated when this frame was saved')
    depth, p = snapshot['depth'], snapshot['deproject_params']
    k = snapshot['k']
    intr = CameraIntrinsics(fx=k[0], fy=k[4], cx=k[2], cy=k[5])
    T = np.array(snapshot['base_from_camera'], dtype=np.float64).reshape(4, 4)
    out = []
    for u, v in zip(us, vs):
        u, v = int(u), int(v)
        z = median_depth_at_pixel(depth, u, v, p['window_size'], '16UC1',
                                  p['min_depth_m'], p['max_depth_m'])
        if z is None:
            out.append(None)
            continue
        c = deproject_pixel_to_3d(u, v, z, intr.fx, intr.fy, intr.cx, intr.cy)
        x, y, zb = (T @ np.array([c.x, c.y, c.z, 1.0]))[:3]
        if p['local_plane_centroid_enabled']:
            centroid = local_plane_centroid(
                depth, u, v, p['local_plane_centroid_radius_px'], p['local_plane_centroid_z_tol_m'],
                p['local_plane_centroid_min_points'], '16UC1', intr, T,
                p['min_depth_m'], p['max_depth_m'])
            if centroid is not None:
                x, y, zb = centroid[:3]
        out.append([float(x), float(y), float(zb) + p['z_offset_m']])
    return out
