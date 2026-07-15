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
