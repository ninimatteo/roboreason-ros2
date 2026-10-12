"""Which cube, if any, the simulated gripper ends up holding when it closes.

The simulated grasp has no finger geometry, so the physical constraints a real
top-down grasp obeys have to be stated here:

  - only a cube within the tolerance of the TCP can be grasped;
  - a cube with another cube resting on it can't: coming down from above, the
    fingers meet the upper cube first. Without this rule, picking the lower
    cube of a stack lifted both, which can't happen on the real robot.
"""

import numpy as np

# Vertical gap below which one cube counts as resting on another.
_CONTACT_GAP_M = 0.01


def covered_by(cube: str, positions: dict, sizes: dict):
    """Name of a cube resting on top of ``cube``, or None."""
    x, y, z = positions[cube]
    sx, sy, sz = sizes[cube]
    for other, (ox, oy, oz) in positions.items():
        if other == cube:
            continue
        osx, osy, osz = sizes[other]
        overlaps = abs(ox - x) < (sx + osx) / 2 and abs(oy - y) < (sy + osy) / 2
        above = oz > z and abs((oz - osz / 2) - (z + sz / 2)) < _CONTACT_GAP_M
        if overlaps and above:
            return other
    return None


def choose_grasp(tcp, positions: dict, sizes: dict, tolerance: float):
    """Return (cube, explanation): the cube the gripper holds after closing, or None."""
    by_distance = sorted(
        (float(np.linalg.norm(np.asarray(p) - np.asarray(tcp))), c) for c, p in positions.items()
    )
    reachable = [(d, c) for d, c in by_distance if d <= tolerance]
    if not reachable:
        d, c = by_distance[0]
        return None, f'closed on nothing: nearest is {c} at {d:.3f} m > tolerance {tolerance:.3f} m'
    blocked = []
    for d, c in reachable:
        top = covered_by(c, positions, sizes)
        if top is None:
            return c, f'grasped {c} (TCP distance {d:.3f} m)'
        blocked.append(f'{top} sits on {c}')
    return None, 'closed on nothing: blocked, ' + '; '.join(blocked)
