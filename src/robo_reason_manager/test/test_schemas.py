"""Tests for distribute_zone_releases (schemas.py) — spacing out multiple
releases that land on (about) the same spot instead of letting them
collide. Collision detection compares release positions to each other
within the plan, not to a scene's static `targets` registry, so the same
logic covers LLM, VLM, and VLM_LLM plans alike.
"""
from robo_reason_manager.schemas import distribute_zone_releases, _zone_slot_offset


def _approach(position):
    return {'action_name': 'approach', 'target_position': list(position)}


def _release(position, grasp_width=0.05):
    return {
        'action_name': 'release',
        'release_position': list(position),
        'grasp_width': grasp_width,
    }


def test_single_release_is_left_untouched():
    plan = [_approach([0.55, -0.20, 0.06]), _release([0.55, -0.20, 0.06])]
    result = distribute_zone_releases(plan)
    assert result[1]['release_position'] == [0.55, -0.20, 0.06]
    assert result[0]['target_position'] == [0.55, -0.20, 0.06]


def test_releases_far_apart_are_left_untouched():
    plan = [_release([0.1, 0.1, 0.02]), _release([0.5, 0.5, 0.02])]
    result = distribute_zone_releases(plan)
    assert result[0]['release_position'] == [0.1, 0.1, 0.02]
    assert result[1]['release_position'] == [0.5, 0.5, 0.02]


def test_second_release_at_same_spot_is_offset():
    plan = [
        _approach([0.55, -0.20, 0.06]), _release([0.55, -0.20, 0.06]),
        _approach([0.55, -0.20, 0.06]), _release([0.55, -0.20, 0.06]),
    ]
    result = distribute_zone_releases(plan)

    first, second = result[1]['release_position'], result[3]['release_position']
    assert first == [0.55, -0.20, 0.06]
    assert second[:2] != first[:2]
    assert second[2] == first[2]  # z untouched, only x/y nudged


def test_preceding_approach_is_nudged_by_the_same_amount_as_its_release():
    plan = [
        _approach([0.55, -0.20, 0.06]), _release([0.55, -0.20, 0.06]),
        _approach([0.55, -0.20, 0.06]), _release([0.55, -0.20, 0.06]),
    ]
    result = distribute_zone_releases(plan)

    second_release = result[3]['release_position']
    second_approach = result[2]['target_position']
    assert second_approach[0] == second_release[0]
    assert second_approach[1] == second_release[1]
    # First approach/release pair is untouched.
    assert result[0]['target_position'] == [0.55, -0.20, 0.06]


def test_stack_same_xy_different_z_is_not_treated_as_a_collision():
    plan = [
        _release([0.55, -0.20, 0.05]),
        _release([0.55, -0.20, 0.10]),  # same spot, released higher -> a stack
    ]
    result = distribute_zone_releases(plan)
    assert result[0]['release_position'] == [0.55, -0.20, 0.05]
    assert result[1]['release_position'] == [0.55, -0.20, 0.10]


def test_third_release_does_not_collide_with_second():
    plan = [_release([0.55, -0.20, 0.06]) for _ in range(3)]
    result = distribute_zone_releases(plan)
    positions = [tuple(step['release_position'][:2]) for step in result]
    assert len(set(positions)) == 3


def test_unrelated_actions_are_ignored():
    plan = [
        {'action_name': 'pick', 'target_position': [0.55, -0.20, 0.06]},
        {'action_name': 'wait', 'time': 1.0},
    ]
    result = distribute_zone_releases(plan)
    assert result == plan


def test_zone_slot_offset_wraps_into_a_new_row_when_line_is_full():
    # ZONE_PLACEMENT_ITEMS_PER_ROW defaults to 3, so extras 1-3 (2nd-4th
    # occupant overall) fit in row 0 along y, and none of them can land on
    # (0, 0) (the untouched anchor).
    for index in (1, 2, 3):
        dx, dy = _zone_slot_offset(0.06, index)
        assert dx == 0.0
        assert dy != 0.0

    dx, dy = _zone_slot_offset(0.06, 4)
    assert dx != 0.0  # 5th occupant wraps into row 1 (along x)


# --- targets-aware (ROBOAI-19 bounds) spacing ---

def _tray_targets(x_min=0.24, x_max=0.46, y_min=-0.43, y_max=-0.12):
    return {'tray': {'bounds': {'x': [x_min, x_max], 'y': [y_min, y_max], 'z': [0.05, 0.05]}}}


def test_four_releases_at_a_zone_centre_all_stay_inside_its_bounds():
    # Same shape as the real bug report: 4 cubes, LLM releases all of them
    # at the tray's exact centre (0.35, -0.275), tray is 0.22 x 0.31 m.
    center = [0.35, -0.275, 0.05]
    plan = [_release(list(center)) for _ in range(4)]
    result = distribute_zone_releases(plan, _tray_targets())

    positions = [tuple(step['release_position'][:2]) for step in result]
    assert len(set(positions)) == 4, "all four releases must end up at distinct spots"
    for x, y in positions:
        assert 0.24 <= x <= 0.46, f"x={x} escaped the tray"
        assert -0.43 <= y <= -0.12, f"y={y} escaped the tray"


def test_zone_unaware_call_keeps_old_unbounded_behaviour():
    # Same 4-release scenario, but without a targets registry (e.g. a
    # VLM/VLM_LLM plan) — must match the pre-ROBOAI-19 behaviour exactly.
    center = [0.35, -0.275, 0.05]
    plan = [_release(list(center)) for _ in range(4)]
    result = distribute_zone_releases(plan)

    positions = [tuple(step['release_position'][:2]) for step in result]
    assert len(set(positions)) == 4
    # unbounded growth is free to leave the tray's footprint — that's the
    # exact behaviour this test locks in as "unchanged when targets is omitted"
    assert any(not (0.24 <= x <= 0.46 and -0.43 <= y <= -0.12) for x, y in positions)


def test_release_outside_any_zone_is_unaffected_by_targets_arg():
    # A collision far from the tray must behave identically whether or not
    # a targets registry is passed, since _find_zone_bounds won't match it.
    plan = [_release([0.55, -0.20, 0.06]) for _ in range(2)]
    with_targets = distribute_zone_releases([dict(s) for s in plan], _tray_targets())
    without_targets = distribute_zone_releases([dict(s) for s in plan])
    assert with_targets[1]['release_position'] == without_targets[1]['release_position']


def test_zone_too_small_for_extra_slots_falls_back_to_unbounded_growth():
    # A zone barely bigger than one item can't offer a second in-bounds
    # slot — must not raise, and must still separate the releases (by
    # escaping the zone) rather than leaving them collided.
    tiny = {'cup': {'bounds': {'x': [0.10, 0.13], 'y': [-0.10, -0.07], 'z': [0.02, 0.02]}}}
    center = [0.115, -0.085, 0.02]
    plan = [_release(list(center)) for _ in range(2)]
    result = distribute_zone_releases(plan, tiny)

    positions = [tuple(step['release_position'][:2]) for step in result]
    assert len(set(positions)) == 2


# --- fitted layout inside a known zone (ROBOAI-21, R3) ---

TRAY = dict(x_min=0.24, x_max=0.46, y_min=-0.43, y_max=-0.12)   # 0.22 x 0.31 m, longer along y
TRAY_CENTRE = [(TRAY['x_min'] + TRAY['x_max']) / 2, (TRAY['y_min'] + TRAY['y_max']) / 2, 0.05]


def _xy(result):
    return [tuple(s['release_position'][:2]) for s in result if s['action_name'] == 'release']


def _assert_footprints_inside(positions, half, bounds):
    for x, y in positions:
        assert bounds['x_min'] <= x - half and x + half <= bounds['x_max'], f'x={x} overhangs the zone'
        assert bounds['y_min'] <= y - half and y + half <= bounds['y_max'], f'y={y} overhangs the zone'


def test_two_releases_are_centred_on_the_zone_along_its_long_axis():
    plan = [_release(list(TRAY_CENTRE)) for _ in range(2)]
    positions = _xy(distribute_zone_releases(plan, _tray_targets()))
    (x1, y1), (x2, y2) = positions
    assert abs(x1 - TRAY_CENTRE[0]) < 1e-9 and abs(x2 - TRAY_CENTRE[0]) < 1e-9
    assert abs((y1 + y2) / 2 - TRAY_CENTRE[1]) < 1e-9, 'the pair is centred on the tray'
    assert abs(y1 - y2) > 0.10, 'the pair uses the tray, not the minimum spacing'


def test_axis_follows_the_zone_shape():
    wide = _tray_targets(x_min=0.20, x_max=0.50, y_min=-0.35, y_max=-0.20)   # longer along x
    plan = [_release([0.35, -0.275, 0.05]) for _ in range(2)]
    (x1, y1), (x2, y2) = _xy(distribute_zone_releases(plan, wide))
    assert abs(y1 - y2) < 1e-9 and abs(x1 - x2) > 0.10


def test_four_releases_fill_a_centred_grid_with_footprints_inside():
    plan = [_release(list(TRAY_CENTRE)) for _ in range(4)]
    positions = _xy(distribute_zone_releases(plan, _tray_targets()))
    assert len(set(positions)) == 4
    centroid = (sum(p[0] for p in positions) / 4, sum(p[1] for p in positions) / 4)
    assert abs(centroid[0] - TRAY_CENTRE[0]) < 1e-9 and abs(centroid[1] - TRAY_CENTRE[1]) < 1e-9
    _assert_footprints_inside(positions, (0.05 + 0.01) / 2, TRAY)
    gaps = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for i, a in enumerate(positions) for b in positions[i + 1:]]
    assert min(gaps) >= 0.06


def test_the_first_release_moves_with_its_approach():
    plan = [_approach(TRAY_CENTRE), _release(list(TRAY_CENTRE)),
            _approach(TRAY_CENTRE), _release(list(TRAY_CENTRE))]
    result = distribute_zone_releases(plan, _tray_targets())
    for approach, release in ((result[0], result[1]), (result[2], result[3])):
        assert approach['target_position'][:2] == release['release_position'][:2]
    assert result[1]['release_position'][:2] != TRAY_CENTRE[:2]


def test_a_stack_in_the_cluster_keeps_the_ring_layout():
    # Something is released on top of the first object: moving that object
    # would leave the stacked one to fall onto the bare tray.
    on_top = [TRAY_CENTRE[0], TRAY_CENTRE[1], TRAY_CENTRE[2] + 0.05]
    plan = [_release(list(TRAY_CENTRE)), _release(on_top), _release(list(TRAY_CENTRE))]
    result = distribute_zone_releases(plan, _tray_targets())
    assert result[0]['release_position'] == TRAY_CENTRE
    assert result[1]['release_position'] == on_top


def test_another_release_in_the_zone_keeps_the_ring_layout():
    # A spread layout could land on an object the plan puts elsewhere in the tray.
    elsewhere = [0.27, -0.15, 0.05]
    plan = [_release(list(TRAY_CENTRE)), _release(list(TRAY_CENTRE)), _release(elsewhere)]
    result = distribute_zone_releases(plan, _tray_targets())
    assert result[0]['release_position'] == TRAY_CENTRE
    assert result[2]['release_position'] == elsewhere


def test_ring_flag_restores_the_r1_layout(monkeypatch):
    from robo_reason_bringup.config import settings
    monkeypatch.setattr(settings, 'ZONE_PLACEMENT_LAYOUT', 'ring')
    plan = [_release(list(TRAY_CENTRE)) for _ in range(2)]
    result = distribute_zone_releases(plan, _tray_targets())
    assert result[0]['release_position'] == TRAY_CENTRE


def test_objects_standing_in_the_zone_keep_the_ring_layout():
    # The table is a zone too, with cubes on it: spreading two releases over
    # the whole table would land on them, or beyond the arm's reach.
    table = {'table': {'bounds': {'x': [-0.4, 0.4], 'y': [-0.85, -0.2], 'z': [-0.03, -0.02]}}}
    objects = {'blue_cube': {'position': [0.05, -0.55, 0.0], 'size': [0.045, 0.045, 0.045]}}
    spot = [-0.2, -0.4, -0.02]
    plan = [_release(list(spot)) for _ in range(2)]
    result = distribute_zone_releases(plan, table, objects)
    assert result[0]['release_position'] == spot
