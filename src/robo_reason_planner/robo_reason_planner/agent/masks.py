"""Things on the table from SAM 2 masks filtered by depth (ROBOAI-61).

The depth segmenter (geometry.detect_blobs) joins touching things of the same
height by construction and needed a new rule for every new scene (ROBOAI-56).
Here SAM decides where one thing ends, the depth only decides whether a mask
is a thing at all and how big it is. The rules are general, none is about a
scene:

  - a mask is a thing when most of its depth samples stand above the table;
  - a mask in separate pieces is one thing per piece;
  - a mask mostly covered by smaller things already kept is their union
    (a pile, a group, the same thing found twice), not a thing of its own;
  - a mask lying inside a larger thing, not standing higher than its top, is
    a part of it (a cube's top face, a tray's wall);
  - a mask reaching into the robot's base, or touching one that does, is
    the robot.

What SAM can't separate from the table (a white tray on a white table) the
depth still sees: Toolbox._sam_blobs adds the depth blobs no mask covers.

The output has the shape of detect_blobs, so ids, stands_on, Set of Mark and
the measurements downstream are the same for both segmenters.
"""

import math

import numpy as np

from robo_reason_planner.agent import geometry


def split_pieces(mask, min_area: int) -> list:
    """The connected pieces of a mask with at least min_area pixels."""
    import cv2
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [labels == i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]


def sample_pixels(mask, max_samples: int, step: int = 4) -> list:
    """Pixels of a mask on a regular grid, the grid coarsened until there are
    at most max_samples."""
    while True:
        vs, us = np.nonzero(mask[step // 2::step, step // 2::step])
        if len(us) <= max_samples or step >= 64:
            return [(int(u) * step + step // 2, int(v) * step + step // 2) for u, v in zip(us, vs)]
        step *= 2


def blob(mask, pixels, pts, floor: float, raised_m: float = 0.01, min_frac: float = 0.6,
         max_height: float = geometry.ABOVE_TABLE_NOTE_M, top_m: float = 0.015,
         min_samples: int = 5):
    """A mask as a detect_blobs entry, or None when it is not a thing.

    pixels, pts: samples of the mask and their deprojection (None where
    there is no depth).
    The top is the dominant height among the raised samples: side faces seen
    obliquely spread over many heights, the top surface shares one.
    """
    valid = [p for p in pts if p is not None]
    if len(valid) < min_samples:
        return None
    raised = [p for p in valid if raised_m < p[2] - floor <= max_height]
    if len(raised) < min_frac * len(valid) or len(raised) < min_samples:
        return None
    surface = geometry._dominant_height(raised, 0.01)
    on_top = [abs(p[2] - surface) <= top_m and raised_m < p[2] - floor <= max_height
              if p is not None else False for p in pts]
    top = [p for p, t in zip(pts, on_top) if t]
    vs, us = np.nonzero(mask)
    xs, ys, tz = sorted(p[0] for p in top), sorted(p[1] for p in top), sorted(p[2] for p in top)
    return {
        'bbox_px': [int(us.min()), int(vs.min()), int(us.max()), int(vs.max())],
        'samples': len(valid),
        'height_m': round(tz[len(tz) // 2] - floor, 4),
        'footprint_m': round(max(xs[-1] - xs[0], ys[-1] - ys[0]), 4),
        'top_center': geometry._r([xs[len(xs) // 2], ys[len(ys) // 2], tz[len(tz) // 2]]),
        'xy_range': [[round(xs[0], 4), round(xs[-1], 4)], [round(ys[0], 4), round(ys[-1], 4)]],
        'narrow_m': round(geometry.narrowest_width(top), 4),
        'top_pixels': [px for px, t in zip(pixels, on_top) if t],
        # nearest the robot's base axis any sample of the mask comes
        'min_reach_m': round(min(math.hypot(p[0], p[1]) for p in valid), 4),
        'mask': mask,
    }


def drop_robot(blobs: list, base_radius_m: float, touch_px: int = 3) -> list:
    """Blobs that are not the robot: a mask with samples within base_radius_m
    of the base axis is the robot (nothing on the table overlaps its base),
    and so is a mask touching one of those (a link seen past the base)."""
    import cv2
    robot = [b for b in blobs if b['min_reach_m'] < base_radius_m]
    if not robot:
        return list(blobs)
    union = np.zeros_like(robot[0]['mask'], dtype=np.uint8)
    for b in robot:
        union |= b['mask'].astype(np.uint8)
    near = cv2.dilate(union, np.ones((2 * touch_px + 1, 2 * touch_px + 1), np.uint8)).astype(bool)
    return [b for b in blobs if b['min_reach_m'] >= base_radius_m and not (b['mask'] & near).any()]


def drop_parts(blobs: list, inside: float = 0.85, level_m: float = 0.01) -> list:
    """Drop a blob whose mask lies (for at least `inside`) within the mask of
    a larger blob kept, unless its top stands higher than that blob's top: a
    cube on a tray is a thing, the tray's wall or a cube's top face a part."""
    kept = []
    for b in sorted(blobs, key=lambda b: -int(b['mask'].sum())):
        m, z = b['mask'], b['top_center'][2]
        if any((m & k['mask']).sum() >= inside * m.sum() and z <= k['top_center'][2] + level_m
               for k in kept):
            continue
        kept.append(b)
    return kept


def drop_covered(blobs: list, max_cover: float = 0.85) -> list:
    """Keep blobs from the smallest mask up; one whose mask is covered for at
    least max_cover by the masks already kept is dropped."""
    kept, union = [], None
    for b in sorted(blobs, key=lambda b: int(b['mask'].sum())):
        m = b['mask']
        if union is not None and (m & union).sum() >= max_cover * m.sum():
            continue
        kept.append(b)
        union = m.copy() if union is None else union | m
    return kept
