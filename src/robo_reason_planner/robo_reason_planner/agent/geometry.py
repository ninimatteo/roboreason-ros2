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


# Anything seen higher than this above the table is reported as probably the
# arm (or what it holds) rather than an object standing on the table.
ABOVE_TABLE_NOTE_M = 0.20
# A top surface lower than this above the table is the table itself (with
# room for a centimetre of calibration offset).
EMPTY_BELOW_M = 0.02


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
        'looks_empty': (top_z - table_z) < EMPTY_BELOW_M,
    }
    if top_z - table_z > ABOVE_TABLE_NOTE_M:
        out['note'] = (f'this surface is {top_z - table_z:.2f} m above the table: probably the arm, '
                       'or an object held in the gripper, not something standing on the table')
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


def free_spot(points: list, footprint_m: float, margin_m: float, count: int = 1,
              arrangement: str = 'any', step_tolerance_m: float = 0.015, allowed=None,
              surface_z: float = None) -> dict:
    """Best places for `count` new objects inside a sampled area.

    points: deprojected [x, y, z] samples on a grid covering the area (None
            where depth was missing). The area's surface is the dominant
            height in the central half of the samples (the bbox is drawn
            around the area, so its centre is on it even when the box is
            loose); samples clearly above it are occupied by something,
            samples clearly below it are off the area (e.g. the table around
            a tray). A candidate must be on the surface, and is scored by its
            clearance: the distance to the nearest occupied or off area
            sample, to the border of the sampled area, and to the other
            chosen spots.

    arrangement 'any' picks the spots one at a time, each the freest left;
    'line' puts them on a straight line along the longer side of the area,
    one footprint plus two margins apart.

    allowed: optional predicate on [x, y, z]; spots it rejects (e.g. out of
    the robot's reach) are never proposed.

    surface_z: the area's height when already measured (a detected tray, the
    table). Seen obliquely (ROBOAI-46), a bbox around a raised area also
    covers its front face and the table beyond it, and its centre need not be
    on the area.

    Returns the positions (on the surface), the worst clearance among them,
    and whether that fits footprint_m / 2 + margin_m.
    """
    valid = [p for p in points if p is not None]
    if not valid:
        return {'ok': False, 'reason': 'no depth samples in the area'}
    count = max(int(count), 1)

    if surface_z is None:
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
    # The area is where its surface is, not the whole sampled box.
    bounds = (min(p[0] for p in on_surface), max(p[0] for p in on_surface),
              min(p[1] for p in on_surface), max(p[1] for p in on_surface))
    candidates = [p for p in on_surface if allowed is None or allowed([p[0], p[1], surface_z])]
    if not candidates:
        return {'ok': False, 'reason': 'no reachable free surface in the area'}

    def clearance(q, chosen=()):
        c = min(q[0] - bounds[0], bounds[1] - q[0], q[1] - bounds[2], bounds[3] - q[1])
        for b in blocked:
            c = min(c, _dist_xy(q, b))
        for other in chosen:
            # the other object's footprint, not just its centre, is in the way
            c = min(c, _dist_xy(q, other) - footprint_m / 2.0)
        return c

    if arrangement == 'line':
        spots, worst = _best_line(on_surface, candidates, clearance, bounds, count,
                                  footprint_m + 2.0 * margin_m, allowed, surface_z)
    else:
        spots, worst = [], math.inf
        for _ in range(count):
            best = max(candidates, key=lambda q: clearance(q, spots))
            worst = min(worst, clearance(best, spots))
            spots.append(best)

    needed = footprint_m / 2.0 + margin_m
    out = {
        'ok': worst >= needed,
        'positions': [_r([p[0], p[1], surface_z]) for p in spots],
        'surface_z': round(surface_z, 4),
        'clearance_m': round(worst, 4),
        'needed_clearance_m': round(needed, 4),
        'occupied_samples': sum(1 for b in blocked if b[2] > surface_z),
    }
    if count == 1:
        out['position'] = out['positions'][0]
    return out


def _best_line(on_surface, candidates, clearance, bounds, count, spacing, allowed, surface_z):
    """count points `spacing` apart along the area's longer side, placed where
    the worst clearance among them is largest. Each point must land on the
    surface (within half a spacing of an on surface sample) and be allowed."""
    along_x = (bounds[1] - bounds[0]) >= (bounds[3] - bounds[2])
    step = (spacing, 0.0) if along_x else (0.0, spacing)
    best, best_worst = None, -math.inf
    for anchor in candidates:
        line = [(anchor[0] + k * step[0], anchor[1] + k * step[1]) for k in range(count)]
        # stay on the surface: every point needs a surface sample nearby
        if any(min(_dist_xy(q, s) for s in on_surface) > spacing / 2.0 for q in line):
            continue
        if allowed is not None and not all(allowed([q[0], q[1], surface_z]) for q in line):
            continue
        worst = min(clearance(q) for q in line)
        if worst > best_worst:
            best, best_worst = line, worst
    if best is None:
        return [candidates[0]], -1.0
    return best, best_worst


def snap_to_object(samples, cols: int, rows: int, center_uv, table_z: float,
                   min_height: float = 0.02, max_height: float = ABOVE_TABLE_NOTE_M,
                   level_z: float = None, level_tol: float = 0.01):
    """Find the object nearest to a rough bbox in a depth grid around it.

    samples: row major list of (u, v, [x, y, z] or None), cols x rows. Samples
    standing between min_height and max_height above the table are "raised"
    (the upper bound leaves out the arm); 4 connected raised samples form a
    blob. Returns the pixel bbox [u0, v0, u1, v1] of the blob whose centre
    is closest to center_uv, or None when nothing is raised nearby.

    Small VLMs place boxes 15 to 30 px off an object of that size; measuring
    the box as given then reads the table next to the object.

    level_z, when the object's top height is known, keeps only the samples
    within level_tol of it: the outline of the top surface. Raised above the
    floor is not enough for an object standing on a tray seen obliquely: the
    window then reaches the table, the tray is raised too, and the cube came
    out as one blob with the tray around it, twice its width (ROBOAI-51).
    """
    # Heights from the local floor (the dominant height around the box), not
    # from the configured table height: a calibration offset of a centimetre
    # (the simulation reads the table 1 cm high) made the table "raised".
    valid = [p for _, _, p in samples if p is not None]
    if not valid:
        return None
    floor = _dominant_height(valid, 0.01)
    if level_z is not None:
        raised = [p is not None and abs(p[2] - level_z) <= level_tol for _, _, p in samples]
    else:
        raised = [p is not None and min_height <= p[2] - floor <= max_height for _, _, p in samples]
    seen = [False] * len(samples)
    best, best_d = None, float('inf')
    for start in range(len(samples)):
        if not raised[start] or seen[start]:
            continue
        stack, blob = [start], []
        seen[start] = True
        while stack:
            i = stack.pop()
            blob.append(i)
            r, c = divmod(i, cols)
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                j = rr * cols + cc
                if 0 <= rr < rows and 0 <= cc < cols and raised[j] and not seen[j]:
                    seen[j] = True
                    stack.append(j)
        us = [samples[i][0] for i in blob]
        vs = [samples[i][1] for i in blob]
        cu, cv = sum(us) / len(us), sum(vs) / len(vs)
        d = math.hypot(cu - center_uv[0], cv - center_uv[1])
        if d < best_d:
            best, best_d = [min(us), min(vs), max(us), max(vs)], d
    return best


def detect_blobs(samples, cols: int, rows: int, min_height: float = 0.02,
                 max_height: float = ABOVE_TABLE_NOTE_M, step_m: float = 0.015,
                 flat_cos: float = 0.9) -> list:
    """Segment what stands on the table in a depth grid covering the image.

    samples: row major (u, v, [x, y, z] or None). A sample is raised when it
    is between min_height and max_height above the floor (the low fifth of
    all samples, so a calibration offset doesn't matter; the upper bound
    leaves out the arm). Neighbouring raised samples join a blob only if
    their heights differ by less than step_m, so a cube standing on a tray
    is its own blob, separate from the tray. The floor is the dominant height
    (the table fills most of the image; the room floor around it is far
    lower and must not be the reference).

    Only top surfaces are segmented. A camera looking at the table
    obliquely (ROBOAI-46) also sees vertical faces, and a face climbs from
    whatever the object stands on in steps small enough to chain: a cube on a
    tray came out as one blob with the tray, and the lower part of a face as
    a phantom object. A sample is kept only where the surface is level: the
    normal of the plane through it and its right and lower neighbours must be
    within acos(flat_cos) of vertical. Faces have a horizontal normal from any
    viewpoint, even seen almost edge on; samples on an edge get a mixed one
    and are dropped too.

    Returns one dict per blob: pixel bbox, sample count, median top height
    above the floor, xy footprint (metres) and the base frame centre of its
    top surface.
    """
    valid = [p for _, _, p in samples if p is not None]
    if not valid:
        return []
    floor = _dominant_height(valid, 0.01)

    def level(i):
        r, c = divmod(i, cols)
        p = samples[i][2]
        # a neighbour on each image axis, the other side at the grid border
        a = samples[r * cols + (c + 1 if c + 1 < cols else c - 1)][2] if cols > 1 else None
        b = samples[(r + 1 if r + 1 < rows else r - 1) * cols + c][2] if rows > 1 else None
        if a is None or b is None:
            return False
        u = (a[0] - p[0], a[1] - p[1], a[2] - p[2])
        v = (b[0] - p[0], b[1] - p[1], b[2] - p[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        norm = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        return norm > 0 and abs(n[2]) / norm >= flat_cos

    raised = [p is not None and min_height <= p[2] - floor <= max_height and level(i)
              for i, (_, _, p) in enumerate(samples)]
    seen = [False] * len(samples)
    groups, blobs = [], []
    for start in range(len(samples)):
        if not raised[start] or seen[start]:
            continue
        stack, idx = [start], []
        seen[start] = True
        while stack:
            i = stack.pop()
            idx.append(i)
            r, c = divmod(i, cols)
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                j = rr * cols + cc
                if (0 <= rr < rows and 0 <= cc < cols and raised[j] and not seen[j]
                        and abs(samples[j][2][2] - samples[i][2][2]) < step_m):
                    seen[j] = True
                    stack.append(j)
        groups.append(idx)
    for idx, top in _merge_side_faces(groups, samples):
        pts = [samples[i][2] for i in top]
        # the top surface only: face samples chained into the blob sit lower or higher
        surface = _dominant_height(pts, 0.01)
        pts = [p for p in pts if abs(p[2] - surface) <= step_m]
        us = [samples[i][0] for i in idx]
        vs = [samples[i][1] for i in idx]
        xs, ys, tz = sorted(p[0] for p in pts), sorted(p[1] for p in pts), sorted(p[2] for p in pts)
        blobs.append({
            'bbox_px': [min(us), min(vs), max(us), max(vs)],
            'samples': len(idx),
            'height_m': round(tz[len(tz) // 2] - floor, 4),
            'footprint_m': round(max(xs[-1] - xs[0], ys[-1] - ys[0]), 4),
            'top_center': _r([xs[len(xs) // 2], ys[len(ys) // 2], tz[len(tz) // 2]]),
            'xy_range': [[round(xs[0], 4), round(xs[-1], 4)], [round(ys[0], 4), round(ys[-1], 4)]],
        })
    return blobs


def _merge_side_faces(groups, samples, tolerance_m: float = 0.01) -> list:
    """Join each blob that is a side face of a taller one into it.

    A face seen almost edge on (the side of a tray, of a cube) is not steep
    between neighbouring samples, so it survives as a blob of its own. It
    lies under the top it hangs from: its xy footprint falls inside that
    top's footprint, and it is not higher. A cube on a tray is the other way round
    (the tray's footprint is not inside the cube's), so it stays its own
    blob. Returns (all sample indices, top surface indices) per blob.
    """
    def extent(idx):
        # height of the blob's dominant surface: a few samples of a face that
        # chained in must not make a tray look as tall as the cube on it
        pts = [samples[i][2] for i in idx]
        return (min(p[0] for p in pts), max(p[0] for p in pts),
                min(p[1] for p in pts), max(p[1] for p in pts), _dominant_height(pts, tolerance_m))

    out = []
    for idx in sorted(groups, key=lambda g: -extent(g)[4]):   # tallest first
        x0, x1, y0, y1, zt = extent(idx)
        host = next((h for h in out
                     if zt <= h[2][4] + tolerance_m
                     and h[2][0] - tolerance_m <= x0 and x1 <= h[2][1] + tolerance_m
                     and h[2][2] - tolerance_m <= y0 and y1 <= h[2][3] + tolerance_m), None)
        if host is None:
            out.append([list(idx), list(idx), extent(idx)])
        else:
            host[0].extend(idx)
    return [(all_idx, top) for all_idx, top, _ in out]


def support_height(xy, top_z: float, surfaces, table_z: float, margin_m: float = 0.01) -> float:
    """Height of the surface under a point seen from above: the highest known
    surface (a tray) whose xy range contains it and which is not above it;
    the table otherwise. On the tray itself that is the tray, so an empty
    spot there looks empty.

    surfaces: ([[x0, x1], [y0, y1]], surface_z) of the areas last detected.
    Measured from the table, a cube on a tray came out three times as tall
    and the grasp went below the tray top (ROBOAI-51).
    """
    best = table_z
    for (xr, yr), z in surfaces:
        if (xr[0] - margin_m <= xy[0] <= xr[1] + margin_m and yr[0] - margin_m <= xy[1] <= yr[1] + margin_m
                and best < z <= top_z + margin_m):
            best = z
    return best


def color_name(rgb) -> str:
    """A plain colour word for a median RGB (0..255)."""
    import colorsys
    h, l, sat = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    hue = h * 360
    if l > 0.85 or (sat < 0.15 and l > 0.6):
        return 'white'
    if l < 0.15:
        return 'black'
    if sat < 0.15:
        return 'gray'
    if hue < 15 or hue >= 340:
        return 'red'
    if hue < 40:
        return 'brown' if l < 0.45 or sat < 0.45 else 'orange'
    if hue < 70:
        return 'yellow'
    if hue < 165:
        return 'green'
    if hue < 200:
        return 'cyan'
    if hue < 255:
        return 'blue'
    if hue < 290:
        return 'purple'
    return 'pink'


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
