"""Which leftover processes a GUI supervisor may treat as its own.

Supervisors reap leftovers of a crashed session by command line. A command
line alone can't tell two environments apart: two GUIs on one machine, in
different ROS domains or Gazebo partitions (a test instance next to a lab
one), run identical commands. Before 2026-10-12 one GUI starting its
simulation SIGKILLed the other's.
"""

import os

from robo_reason_bringup.gazebo_partition import gazebo_partition


def same_environment(pid: int) -> bool:
    """True if ``pid`` runs in this process's ROS domain and Gazebo partition."""
    try:
        with open(f'/proc/{pid}/environ', 'rb') as f:
            pairs = (item.split(b'=', 1) for item in f.read().split(b'\0') if b'=' in item)
            theirs = {k.decode(errors='replace'): v.decode(errors='replace') for k, v in pairs}
    except OSError:
        return False  # can't tell: leave it alone
    # The partition is compared as resolved: sim.launch.py sets the one derived
    # from the domain on its processes, the GUI that started them has none set.
    return (theirs.get('ROS_DOMAIN_ID', '') == os.environ.get('ROS_DOMAIN_ID', '')
            and gazebo_partition(theirs) == gazebo_partition())
