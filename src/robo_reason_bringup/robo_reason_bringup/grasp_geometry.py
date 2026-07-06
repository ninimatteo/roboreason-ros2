"""TCP (flange-to-contact) offset geometry for the RG2 gripper.

Not a setting itself — this derives a Z offset from a per-pick `grasp_width`
via the calibration table below, so it lives here as code rather than in
config.py (which is meant to hold pure configuration values).
"""

from typing import List, Optional

from robo_reason_bringup.config import settings

# Calibrated (object_width_m, flange_to_contact_offset_z_m) pairs for the RG2
# gripper, sorted by width ascending. The fingers pivot/arc, so the true
# flange-to-contact-point distance shrinks as the fingers open wider to grip
# larger objects: fully closed (0.00 m wide) ~0.213 m, mid-open (0.05 m wide)
# ~0.207 m, fully open (0.10 m wide) ~0.175 m.
TCP_OFFSET_Z_CALIBRATION: List[List[float]] = [
    [0.00, 0.213],
    [0.05, 0.207],
    [0.10, 0.175],
]


def tcp_offset_z_for_width(width_m: Optional[float]) -> float:
    """Piecewise-linear interpolation of TCP_OFFSET_Z from grasp width (meters).

    Falls back to `settings.TCP_OFFSET_Z` (mid-open calibration) when width_m
    is None or non-positive, i.e. unknown. Clamps to the calibration table's
    endpoints outside its measured range.
    """
    if width_m is None or width_m <= 0.0:
        return settings.TCP_OFFSET_Z

    table = TCP_OFFSET_Z_CALIBRATION
    if width_m <= table[0][0]:
        return table[0][1]
    if width_m >= table[-1][0]:
        return table[-1][1]

    for (w0, z0), (w1, z1) in zip(table, table[1:]):
        if w0 <= width_m <= w1:
            t = (width_m - w0) / (w1 - w0)
            return z0 + t * (z1 - z0)

    return settings.TCP_OFFSET_Z
