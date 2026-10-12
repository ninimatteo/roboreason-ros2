"""A GUI supervisor must never reap processes of another ROS domain or Gazebo partition."""
import os
import subprocess
import sys

import pytest

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


def test_unreadable_process_is_left_alone():
    assert not same_environment(999_999_999)
