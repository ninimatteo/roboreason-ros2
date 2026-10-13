"""A GUI supervisor must never reap processes of another ROS domain or Gazebo partition."""
import os
import subprocess
import sys

import pytest

from robo_reason_bringup.gazebo_partition import gazebo_partition
from robo_reason_gui.process_scope import same_environment


@pytest.fixture
def child():
    procs = []

    def spawn(**overrides):
        env = dict(os.environ, **overrides)
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; print("ready", flush=True); time.sleep(30)'],
            env=env, stdout=subprocess.PIPE, text=True,
        )
        procs.append(proc)
        # Until the child has exec'd, /proc/<pid>/environ can read empty.
        proc.stdout.readline()
        return proc.pid

    yield spawn
    for proc in procs:
        proc.kill()
        proc.wait()


def test_same_domain_and_partition_is_ours(child):
    assert same_environment(child())


def test_other_ros_domain_is_not_ours(child):
    other = str(int(os.environ.get('ROS_DOMAIN_ID', '0')) + 1)
    assert not same_environment(child(ROS_DOMAIN_ID=other))


def test_other_gazebo_partition_is_not_ours(child):
    assert not same_environment(child(IGN_PARTITION='someone_else'))


def test_simulation_we_launched_is_ours(child, monkeypatch):
    # sim.launch.py sets the partition derived from the domain on its
    # processes; the GUI that started it has none set, and must still reap them.
    monkeypatch.delenv('IGN_PARTITION', raising=False)
    monkeypatch.delenv('GZ_PARTITION', raising=False)
    assert same_environment(child(IGN_PARTITION=gazebo_partition()))


def test_partition_follows_the_ros_domain():
    assert gazebo_partition({'ROS_DOMAIN_ID': '42'}) == 'roboreason_domain42'
    assert gazebo_partition({}) == gazebo_partition({'ROS_DOMAIN_ID': '0'})
    assert gazebo_partition({'ROS_DOMAIN_ID': '42', 'IGN_PARTITION': 'mine'}) == 'mine'


def test_unreadable_process_is_left_alone():
    assert not same_environment(999_999_999)
