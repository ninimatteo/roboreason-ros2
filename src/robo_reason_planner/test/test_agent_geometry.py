from robo_reason_planner.agent import geometry

GEO = geometry.GraspGeometry(pick_fraction=0.5, min_object_height=0.02, max_descent=0.057)


def test_object_measurement_grasps_mid_body():
    m = geometry.object_measurement(
        [0.1, -0.4, 0.015], {'left': [0.08, -0.4, 0.015], 'right': [0.12, -0.4, 0.015]},
        table_z=-0.03, geo=GEO,
    )
    assert m['height_m'] == 0.045
    assert m['grasp_point'] == [0.1, -0.4, round(0.015 - 0.0225, 4)]
    assert m['held_height_below_grasp_m'] == 0.0225
    assert m['width_m'] == 0.04
    assert not m['looks_empty']


def test_object_measurement_flags_an_empty_spot():
    m = geometry.object_measurement([0.1, -0.4, -0.02], {}, table_z=-0.03, geo=GEO)   # 1 cm offset
    assert m['looks_empty']
    assert 'width_m' not in m


def _grid(x0, x1, y0, y1, n, z_of):
    pts = []
    for j in range(n):
        for i in range(n):
            x = x0 + (x1 - x0) * (i + 0.5) / n
            y = y0 + (y1 - y0) * (j + 0.5) / n
            pts.append([x, y, z_of(x, y)])
    return pts


def test_free_spot_avoids_an_object_already_there():
    # tray 0.20 x 0.30 at z=0.05, a cube occupying its centre
    cube = lambda x, y: abs(x - 0.35) < 0.025 and abs(y + 0.27) < 0.025
    pts = _grid(0.25, 0.45, -0.42, -0.12, 14, lambda x, y: 0.095 if cube(x, y) else 0.05)
    spot = geometry.free_spot(pts, footprint_m=0.045, margin_m=0.01)
    assert spot['ok']
    assert spot['surface_z'] == 0.05
    x, y, z = spot['position']
    assert not (abs(x - 0.35) < 0.05 and abs(y + 0.27) < 0.05)
    assert spot['occupied_samples'] > 0


def test_free_spot_treats_the_table_around_a_tray_as_off_limits():
    # loose bbox: tray in the middle, lower table around it
    on_tray = lambda x, y: 0.28 < x < 0.42 and -0.38 < y < -0.16
    pts = _grid(0.22, 0.48, -0.44, -0.10, 14, lambda x, y: 0.05 if on_tray(x, y) else -0.03)
    spot = geometry.free_spot(pts, footprint_m=0.045, margin_m=0.01)
    x, y, _ = spot['position']
    assert spot['ok']
    assert spot['surface_z'] == 0.05
    assert 0.28 < x < 0.42 and -0.38 < y < -0.16


def test_free_spot_reports_a_full_area():
    pts = _grid(0.0, 0.06, 0.0, 0.06, 6, lambda x, y: 0.0)
    spot = geometry.free_spot(pts, footprint_m=0.08, margin_m=0.01)
    assert not spot['ok']


def test_check_target_rejects_without_correcting():
    limits = {'x': (-0.6, 0.6), 'y': (-0.8, 0.0), 'z': (-0.03, 0.55)}
    assert geometry.check_target([0.1, -0.4, 0.0], -0.03, limits) is None
    assert 'below the table' in geometry.check_target([0.1, -0.4, -0.05], -0.04, {})
    assert 'outside the workspace' in geometry.check_target([0.9, -0.4, 0.0], -0.03, limits)
    assert 'must be [x, y, z]' in geometry.check_target([0.1, 0.2], -0.03, limits)


def test_free_spot_several_positions_keep_apart():
    pts = _grid(0.25, 0.45, -0.42, -0.12, 14, lambda x, y: 0.05)
    spot = geometry.free_spot(pts, footprint_m=0.045, margin_m=0.01, count=3)
    assert spot['ok'] and len(spot['positions']) == 3
    ps = spot['positions']
    assert min(geometry._dist_xy(a, b) for i, a in enumerate(ps) for b in ps[i + 1:]) >= 0.045 + 0.01


def test_free_spot_line_is_straight_evenly_spaced_and_avoids_objects():
    # table area 0.5 x 0.2, a cube sitting at its centre
    cube = lambda x, y: abs(x - 0.0) < 0.025 and abs(y + 0.6) < 0.025
    pts = _grid(-0.25, 0.25, -0.7, -0.5, 20, lambda x, y: 0.015 if cube(x, y) else -0.03)
    spot = geometry.free_spot(pts, footprint_m=0.045, margin_m=0.01, count=3, arrangement='line')
    assert spot['ok']
    (x0, y0, _), (x1, y1, _), (x2, y2, _) = spot['positions']
    assert y0 == y1 == y2                       # along the longer side (x)
    assert abs((x1 - x0) - 0.065) < 1e-6 and abs((x2 - x1) - 0.065) < 1e-6
    for x, y, _ in spot['positions']:
        assert not (abs(x) < 0.05 and abs(y + 0.6) < 0.05)


def test_free_spot_never_proposes_a_disallowed_spot():
    pts = _grid(-0.25, 0.25, -0.7, -0.5, 20, lambda x, y: -0.03)
    spot = geometry.free_spot(pts, 0.045, 0.01, count=3, arrangement='line',
                              allowed=lambda q: q[0] < 0.0)
    assert all(x < 0.0 for x, _, _ in spot['positions'])


def test_snap_ignores_a_table_offset_and_finds_the_nearest_object():
    # 10 x 10 grid, table read at -0.02 (1 cm above its nominal -0.03), one cube at cols 6..8
    samples = []
    for r in range(10):
        for c in range(10):
            z = 0.025 if 6 <= c <= 8 and 3 <= r <= 5 else -0.02
            samples.append((c * 4, r * 4, [0.0, 0.0, z]))
    box = geometry.snap_to_object(samples, 10, 10, (8, 16), table_z=-0.03)
    assert box == [24, 12, 32, 20]


# --- the real viewpoint (ROBOAI-46): ray cast the scene from the far end camera ---

import math
import numpy as np

_CAM = [-0.093, -0.934, 0.548, -2.4895, 0.0160, 0.0252]   # optical pose, as in sim.yaml
_TABLE_Z = -0.03


def _rot(roll, pitch, yaw):
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                              math.sin(pitch), math.cos(yaw), math.sin(yaw))
    return (np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]) @ np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
            @ np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]]))


def _cast(boxes, u, v, w=1280, h=720, f=670.0):
    """Base frame point the far end camera sees at pixel (u, v), or None off
    the table (each box: (x0, x1, y0, y1, z0, z1))."""
    origin = np.array(_CAM[:3])
    d = _rot(*_CAM[3:]) @ np.array([(u - w / 2) / f, (v - h / 2) / f, 1.0])
    best = (_TABLE_Z - origin[2]) / d[2] if d[2] < 0 else math.inf
    for x0, x1, y0, y1, z0, z1 in boxes:
        tmin, tmax = 0.0, math.inf
        for o, dd, lo, hi in zip(origin, d, (x0, y0, z0), (x1, y1, z1)):
            if abs(dd) < 1e-12:
                if not lo <= o <= hi:
                    tmin, tmax = 1, 0
                continue
            t1, t2 = sorted(((lo - o) / dd, (hi - o) / dd))
            tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin <= tmax and tmin < best:
            best = tmin
    p = origin + best * d
    return ([float(p[0]), float(p[1]), float(p[2])]
            if best < math.inf and -0.6 <= p[0] <= 0.6 and -0.8 <= p[1] <= 0.05 else None)


def _ray_cast(boxes, step=8, w=1280, h=720, f=670.0):
    """Depth grid samples over the whole image. Returns (samples, cols, rows)."""
    cols, rows = int((w - step) // step) + 1, int((h - step) // step) + 1
    samples = []
    for r in range(rows):
        for c in range(cols):
            u, v = step / 2 + c * step, step / 2 + r * step
            samples.append((u, v, _cast(boxes, u, v, w, h, f)))
    return samples, cols, rows


def _cube(x, y, z0=_TABLE_Z, s=0.045):
    return (x - s / 2, x + s / 2, y - s / 2, y + s / 2, z0, z0 + s)


TRAY = (0.24, 0.46, -0.43, -0.12, _TABLE_Z, 0.06)


def _heights(blobs):
    return sorted(round(b['height_m'], 3) for b in blobs)


def test_scene_mock_seen_from_the_real_camera_gives_four_cubes_and_the_tray():
    boxes = [_cube(-0.3, -0.6), _cube(-0.2, -0.5), _cube(-0.1, -0.7), _cube(0.0, -0.4), TRAY]
    blobs = geometry.detect_blobs(*_ray_cast(boxes))
    assert _heights(blobs) == [0.045, 0.045, 0.045, 0.045, 0.09]


def test_a_cube_on_the_tray_seen_from_the_real_camera_is_its_own_object():
    boxes = [TRAY, _cube(0.35, -0.29, z0=0.06), _cube(-0.2, -0.5)]
    blobs = geometry.detect_blobs(*_ray_cast(boxes))
    assert _heights(blobs) == [0.045, 0.09, 0.135]
    on_tray = next(b for b in blobs if round(b['height_m'], 3) == 0.135)
    assert math.dist(on_tray['top_center'][:2], [0.35, -0.29]) < 0.02


def test_free_spot_on_a_known_surface_ignores_the_table_behind_it():
    # An oblique bbox around a tray (top at 0.05, x 0.25..0.45) also covers
    # the table beyond it; the table is lower and must not be a candidate.
    tray = lambda x, y: 0.25 <= x <= 0.45
    pts = _grid(0.20, 0.60, -0.42, -0.12, 14, lambda x, y: 0.05 if tray(x, y) else -0.03)
    spot = geometry.free_spot(pts, footprint_m=0.045, margin_m=0.01, surface_z=0.05)
    assert spot['ok'] and spot['surface_z'] == 0.05
    assert 0.25 <= spot['position'][0] <= 0.45


def test_support_height_is_the_tray_under_an_object_on_it():
    tray = ([[0.24, 0.46], [-0.43, -0.12]], 0.06)
    assert geometry.support_height([0.35, -0.29], 0.105, [tray], -0.03) == 0.06
    assert geometry.support_height([0.35, -0.29], 0.06, [tray], -0.03) == 0.06    # the tray itself
    assert geometry.support_height([-0.2, -0.5], 0.015, [tray], -0.03) == -0.03   # beside it


# The final state of the failed pp_hard+move trial (ROBOAI-51): four cubes on the tray.
_ON_TRAY = [(0.306, -0.195), (0.388, -0.168), (0.343, -0.289), (0.391, -0.352)]


class _RayBackend:
    def __init__(self, boxes):
        self.boxes = boxes

    def deproject(self, pixels):
        return [_cast(self.boxes, u, v) for u, v in pixels]

    def metres_per_pixel(self, point):
        d = math.dist(point, _CAM[:3]) / 670.0
        return d, d


def test_cubes_on_the_tray_measure_as_cubes_standing_on_it():
    from robo_reason_planner.agent.tools import Toolbox, ToolboxConfig
    s = 0.045
    tb = Toolbox(backend=_RayBackend([TRAY] + [_cube(x, y, z0=0.06, s=s) for x, y in _ON_TRAY]),
                 config=ToolboxConfig(table_z=_TABLE_Z, limits={'x': (-0.6, 0.6), 'y': (-0.8, 0.0),
                                                                'z': (-0.03, 0.55)}, grasp=GEO),
                 log=lambda *_: None)
    tb.full_size = tb.image_size = (1280, 720)
    found = tb._detect()
    objects = [tb.refs[f['id']] for f in found if f['id'] != 'table' and tb.refs[f['id']]['pickable']]
    assert len(objects) == 4
    for m in objects:
        assert abs(m['height_m'] - s) < 0.01                        # from the tray, not the table
        assert m['grasp_point'][2] > 0.06 + 0.01                    # above the tray top
        assert abs(m['width_m'] - s) < 0.012                        # the cube, not the tray around it
    tray = next(f for f in found if f['id'] != 'table' and not tb.refs[f['id']]['pickable'])
    assert tray['stands_on'] == 'table'
    assert all(f['stands_on'] == tray['id'] for f in found if f['id'] not in ('table', tray['id']))


# --- unified things (ROBOAI-55): no objects and areas, each says what it stands on ---

def _ray_toolbox(boxes):
    from robo_reason_planner.agent.tools import Toolbox, ToolboxConfig
    tb = Toolbox(backend=_RayBackend(boxes),
                 config=ToolboxConfig(table_z=_TABLE_Z, limits={'x': (-0.6, 0.6), 'y': (-0.8, 0.0),
                                                                'z': (-0.03, 0.55)}, grasp=GEO,
                                      verify_actions=False, auto_look_after_action=False),
                 log=lambda *_: None)
    tb.full_size = tb.image_size = (1280, 720)
    tb.backend.run_skill = lambda name, args: (tb.skills.append((name, args)) or (True, ''))
    tb.skills = []
    return tb


def test_a_cube_on_a_block_is_measured_from_the_block():
    # a 4.5 cm cube on an 8 cm block: the block's top shows around the cube
    block = (-0.24, -0.16, -0.54, -0.46, _TABLE_Z, _TABLE_Z + 0.05)
    tb = _ray_toolbox([block, _cube(-0.2, -0.5, z0=_TABLE_Z + 0.05), _cube(0.0, -0.4)])
    found = {f['id']: f for f in tb._detect()}
    top = max((f for f in found.values() if f['id'] != 'table'),
              key=lambda f: tb.refs[f['id']]['top_center'][2])
    below = found[top['stands_on']]
    assert below['stands_on'] == 'table' and tb.refs[below['id']]['pickable']
    assert abs(top['height_m'] - 0.045) < 0.01                       # from the block, not the table
    assert tb.refs[top['id']]['grasp_point'][2] > _TABLE_Z + 0.05    # above the block's top
    # the block's box holds the cube too: the block is measured from its own top (ROBOAI-56)
    assert abs(below['height_m'] - 0.05) < 0.01


def test_a_support_colour_leaves_out_what_stands_on_it():
    from PIL import Image, ImageDraw
    from robo_reason_planner.agent.tools import _blob_colour
    im = Image.new('RGB', (100, 100), (240, 240, 240))
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 90, 90), fill=(10, 10, 10))        # a black block
    d.rectangle((15, 15, 85, 85), fill=(240, 220, 20))      # a yellow cube on it, most of the box
    blob = {'bbox_px': [10, 10, 90, 90]}
    assert _blob_colour(im, [], [], blob) == 'yellow'
    assert _blob_colour(im, [], [], blob, [[15, 15, 85, 85]]) == 'black'


def test_a_stack_of_equal_cubes_is_one_thing_from_the_fixed_camera():
    # Known limit: the lower cube's top is hidden under the upper one, so the
    # depth sees one thing as tall as the stack. Place by contact (ROBOAI-53)
    # or a second view is what tells them apart.
    tb = _ray_toolbox([_cube(-0.2, -0.5), _cube(-0.2, -0.5, z0=_TABLE_Z + 0.045)])
    found = tb._detect()
    assert len(found) == 2 and abs(found[1]['height_m'] - 0.09) < 0.01


def test_place_on_a_wide_thing_finds_a_free_place_on_it():
    tb = _ray_toolbox([TRAY, _cube(0.35, -0.275, z0=0.06), _cube(-0.2, -0.5)])
    found = tb._detect()
    tray = next(f['id'] for f in found if f['id'] != 'table' and not tb.refs[f['id']]['pickable'])
    loose = next(f['id'] for f in found if f['id'] != 'table' and f['stands_on'] == 'table'
                 and tb.refs[f['id']]['pickable'])
    assert tb.run('pick', {'object': tray}).text.startswith('REFUSED')
    assert tb.run('pick', {'object': loose}).text.startswith('pick completed')
    assert tb.run('place', {'target': tray}).text.startswith('place completed')
    x, y, z = tb.skills[-1][1]['release_position']
    assert abs(z - 0.06) < 0.01 and 0.24 <= x <= 0.46 and -0.43 <= y <= -0.12
    assert math.dist([x, y], [0.35, -0.275]) > 0.045                 # not on the cube already there


def test_a_long_bar_is_pickable_across_its_short_side():
    # 12 x 3 cm: wider than the gripper along x, 3 cm across (ROBOAI-56)
    tb = _ray_toolbox([(-0.26, -0.14, -0.515, -0.485, _TABLE_Z, _TABLE_Z + 0.03), _cube(0.0, -0.4)])
    found = [f for f in tb._detect() if f['id'] != 'table']
    assert len(found) == 2 and all(tb.refs[f['id']]['pickable'] for f in found)


def test_narrowest_width_of_a_bar_lying_askew():
    a = 0.5
    pts = [(u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a))
           for u in (-0.06, 0.0, 0.06) for v in (-0.015, 0.015)]
    assert max(p[0] for p in pts) - min(p[0] for p in pts) > 0.10       # wide on the image axes
    assert abs(geometry.narrowest_width(pts) - 0.03) < 0.003
