"""The simulated grasp must respect what a real top-down grasp can reach."""
from robo_reason_sim.grasp_rules import choose_grasp

S = 0.045
SIZES = {'red': (S, S, S), 'blue': (S, S, S)}
TABLE_Z = -0.03 + S / 2


def test_single_cube_within_tolerance_is_grasped():
    cube, _ = choose_grasp((0.0, 0.0, TABLE_Z), {'red': (0.0, 0.0, TABLE_Z)}, {'red': SIZES['red']}, 0.035)
    assert cube == 'red'


def test_cube_out_of_tolerance_is_not():
    cube, why = choose_grasp((0.1, 0.0, TABLE_Z), {'red': (0.0, 0.0, TABLE_Z)}, {'red': SIZES['red']}, 0.035)
    assert cube is None and 'nearest is red' in why


def test_lower_cube_of_a_stack_cannot_be_grasped():
    stack = {'blue': (0.0, 0.0, TABLE_Z), 'red': (0.0, 0.0, TABLE_Z + S)}
    cube, why = choose_grasp((0.0, 0.0, TABLE_Z), stack, SIZES, 0.035)
    assert cube is None and 'red sits on blue' in why


def test_aiming_between_two_stacked_cubes_takes_only_the_top_one():
    stack = {'blue': (0.0, 0.0, TABLE_Z), 'red': (0.0, 0.0, TABLE_Z + S)}
    cube, _ = choose_grasp((0.0, 0.0, TABLE_Z + S / 2), stack, SIZES, 0.035)
    assert cube == 'red'


def test_cubes_side_by_side_do_not_block_each_other():
    side = {'blue': (0.0, 0.0, TABLE_Z), 'red': (S + 0.005, 0.0, TABLE_Z)}
    cube, _ = choose_grasp((0.0, 0.0, TABLE_Z), side, SIZES, 0.035)
    assert cube == 'blue'
