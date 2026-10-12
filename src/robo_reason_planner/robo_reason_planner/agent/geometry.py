"""Deterministic geometry the agent can call as tools.

These are the same computations the one-shot planners apply silently after
the model has answered (grasp depth compensation and grasp width in
vlm_planner_node, release spacing in schemas.distribute_zone_releases, the
surface lookup in llm_planner_node._fix_release_height). Here they are pure
functions over deprojected camera points, so the agent decides when to use
them and sees what they return.

All positions are in the robot base frame, metres.
"""

import math
from dataclasses import dataclass


@dataclass
class GraspGeometry:
    pick_fraction: float        # how far down the object to grasp (0 = top, 1 = bottom)
    min_object_height: float    # clamp for a measured height
    max_descent: float          # deepest grasp below the top before the rigid clamp hits


def bbox_sample_pixels(bbox, inset_frac: float) -> dict:
    """Centre and edge pixels of a bbox, edges inset so they land on the object.

    Depth is least reliable exactly on an object's silhouette, so the width
    samples are pulled inside the box by inset_frac of its size.
    """
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    dx, dy = inset_frac * (x1 - x0), inset_frac * (y1 - y0)
    return {
        'center': (cx, cy),
        'left': (x0 + dx, cy),
        'right': (x1 - dx, cy),
        'top': (cx, y0 + dy),
        'bottom': (cx, y1 - dy),
    }


def object_measurement(center, edges: dict, table_z: float, geo: GraspGeometry) -> dict:
    """Height, footprint and suggested grasp point of an object seen from above.

    center: deprojected [x, y, z] of the object's top surface centre.
    edges:  deprojected points for any of 'left', 'right', 'top', 'bottom'
            (missing ones are simply skipped).
    """
    top_z = center[2]
    height = max(top_z - table_z, geo.min_object_height)
    descent = min(geo.pick_fraction * height, geo.max_descent)
    out = {
        'top_center': _r(center),
        'height_m': round(height, 4),
        'grasp_point': _r([center[0], center[1], top_z - descent]),
        # What the executor must lift the TCP by when placing this object
        # after grasping it at grasp_point: only the part below the fingers.
        'held_height_below_grasp_m': round(height - descent, 4),
        'looks_empty': (top_z - table_z) < 0.01,
    }
    if 'left' in edges and 'right' in edges:
        out['width_m'] = round(_dist_xy(edges['left'], edges['right']), 4)
    if 'top' in edges and 'bottom' in edges:
        out['depth_m'] = round(_dist_xy(edges['top'], edges['bottom']), 4)
    return out


def surface_measurement(center, corners: list) -> dict:
    """Surface height and xy extent of a placement area seen from above."""
    out = {'center': _r(center), 'surface_z': round(center[2], 4)}
    if corners:
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        out['x_range'] = [round(min(xs), 4), round(max(xs), 4)]
        out['y_range'] = [round(min(ys), 4), round(max(ys), 4)]
    return out


def free_spot(points: list, footprint_m: float, margin_m: float,
              step_tolerance_m: float = 0.015) -> dict:
    """Best place for a new object inside a sampled area.

    points: deprojected [x, y, z] samples on a grid covering the area (None
            where depth was missing). The area's surface is the dominant
            height in the central half of the samples (the bbox is drawn
            around the area, so its centre is on it even when the box is
            loose); samples clearly above it are occupied by something,
            samples clearly below it are off the area (e.g. the table around
            a tray). A candidate must be on the surface, and is scored by its
            distance to the nearest occupied or off area sample.

    Returns the best candidate, its clearance, and whether that clearance
    fits footprint_m / 2 + margin_m.
    """
    valid = [p for p in points if p is not None]
    if not valid:
        return {'ok': False, 'reason': 'no depth samples in the area'}

    xs = [p[0] for p in valid]
    ys = [p[1] for p in valid]
    bounds = (min(xs), max(xs), min(ys), max(ys))
    qx, qy = (bounds[1] - bounds[0]) / 4.0, (bounds[3] - bounds[2]) / 4.0
    central = [p for p in valid
               if bounds[0] + qx <= p[0] <= bounds[1] - qx and bounds[2] + qy <= p[1] <= bounds[3] - qy]
    surface_z = _dominant_height(central or valid, step_tolerance_m)
    on_surface, blocked = [], []
    for p in valid:
        (on_surface if abs(p[2] - surface_z) <= step_tolerance_m else blocked).append(p)
    if not on_surface:
        return {'ok': False, 'reason': 'no flat surface found in the area'}

    # The edge of what was sampled counts as blocked too, so a spot is never
    # chosen right at the border of the bbox.
    best, best_clear = None, -1.0
    for p in on_surface:
        clear = min(
            p[0] - bounds[0], bounds[1] - p[0], p[1] - bounds[2], bounds[3] - p[1],
        )
        for b in blocked:
            clear = min(clear, _dist_xy(p, b))
        if clear > best_clear:
            best, best_clear = p, clear

    needed = footprint_m / 2.0 + margin_m
    return {
        'ok': best_clear >= needed,
        'position': _r([best[0], best[1], surface_z]),
        'surface_z': round(surface_z, 4),
        'clearance_m': round(best_clear, 4),
        'needed_clearance_m': round(needed, 4),
        'occupied_samples': sum(1 for b in blocked if b[2] > surface_z),
    }


def check_target(position, table_z: float, limits: dict, below_table_tolerance: float = 0.005):
    """Reject a target the robot must not be sent to. Returns an error or None.

    Never corrects the position: the agent gets the reason and decides.
    """
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        return f'position must be [x, y, z] in metres, got {position!r}'
    try:
        x, y, z = (float(v) for v in position)
    except (TypeError, ValueError):
        return f'position must be numbers, got {position!r}'
    if any(math.isnan(v) for v in (x, y, z)):
        return 'position contains NaN'
    if z < table_z - below_table_tolerance:
        return f'z={z:.3f} is below the table surface ({table_z:.3f})'
    for axis, value in (('x', x), ('y', y), ('z', z)):
        lo, hi = limits.get(axis, (-math.inf, math.inf))
        if not lo <= value <= hi:
            return f'{axis}={value:.3f} is outside the workspace [{lo}, {hi}]'
    return None


def _dominant_height(points, tolerance):
    """The height shared by the most samples (mode within a tolerance)."""
    zs = sorted(p[2] for p in points)
    best_z, best_n = zs[0], 0
    j = 0
    for i, z in enumerate(zs):
        while zs[j] < z - tolerance:
            j += 1
        # window [z - tolerance, z]; centre it on the window's median
        n = i - j + 1
        if n > best_n:
            best_n, best_z = n, zs[(i + j) // 2]
    return best_z


def _dist_xy(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _r(p):
    return [round(float(v), 4) for v in p]
