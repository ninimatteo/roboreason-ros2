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
    m = geometry.object_measurement([0.1, -0.4, -0.028], {}, table_z=-0.03, geo=GEO)
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
