"""The Gazebo transport partition a RoboReason environment runs in.

ROS_DOMAIN_ID isolates ROS but not Gazebo, which has its own transport. Two
simulations in different domains with no partition set share the default one
and, since both worlds are named the same, mix: on 2026-10-13 an orphaned
server of an earlier session left a new Gazebo window empty (ROBOAI-41).
So unless a partition is set explicitly, it follows the ROS domain.
"""

import os


def gazebo_partition(env=None) -> str:
    env = os.environ if env is None else env
    explicit = env.get('IGN_PARTITION') or env.get('GZ_PARTITION')
    return explicit or f"roboreason_domain{env.get('ROS_DOMAIN_ID') or '0'}"
