"""Tests for the ROS interface name config (interfaces.yaml + interfaces.py)."""
import pytest
import yaml

from robo_reason_bringup.interfaces import DEFAULT_FILE, load_interfaces


def test_real_profile_resolves_to_the_historical_absolute_names():
    """Relative names in the root namespace must equal the names used before the config file."""
    real = load_interfaces('real')
    assert real.namespace == ''
    assert real.trajectory_action == 'scaled_joint_trajectory_controller/follow_joint_trajectory'
    assert real.gripper_io == 'io_and_status_controller/set_io'
    assert real.plan_task == 'plan_task'
    assert real.execute_skill_status == 'execute_skill/_action/status'


def test_sim_profile_runs_in_its_own_namespace_with_the_plain_trajectory_controller():
    sim = load_interfaces('sim')
    assert sim.namespace == 'sim'
    assert sim.trajectory_action == 'joint_trajectory_controller/follow_joint_trajectory'


def test_every_name_is_relative_so_namespaces_apply():
    for profile in ('real', 'sim'):
        loaded = load_interfaces(profile)
        for field, value in vars(loaded).items():
            if field not in ('profile', 'namespace'):
                assert not value.startswith('/'), f'{profile}.{field} is absolute: {value}'


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match='available: real, sim'):
        load_interfaces('lab')


def test_missing_name_is_reported(tmp_path):
    data = yaml.safe_load(DEFAULT_FILE.read_text())
    del data['profiles']['sim']['robot']['gripper_io']
    broken = tmp_path / 'interfaces.yaml'
    broken.write_text(yaml.safe_dump(data))
    with pytest.raises(KeyError, match='gripper_io'):
        load_interfaces('sim', path=broken)
