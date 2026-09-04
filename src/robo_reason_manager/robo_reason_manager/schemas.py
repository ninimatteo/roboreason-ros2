"""Shared constants, helpers, and data structures for robo_reason_real."""

from robo_reason_bringup.config import settings

ALLOWED_SKILLS = {"approach", "pick", "release", "move_home", "wait"}

# How close two releases' (x, y) need to be to count as "the same spot" —
# for LLM this is a near-exact echo of a target's position, but VLM/VLM_LLM
# releases are independent depth deprojections of the same physical point
# and can differ by a bit of measurement noise even when aimed at the same
# object, so this is a physical radius, not a float-equality tolerance.
_RELEASE_COLLISION_RADIUS_M = 0.03
# How far apart two same-(x, y) releases' z needs to be to count as an
# intentional stack (a higher release on top of the first) rather than two
# objects meant to land side by side. Comfortably below MIN_OBJECT_HEIGHT_M
# (0.02 m) — smaller than any real stack's z jump — so it doesn't
# mistake a genuine stack for a same-level collision.
_STACK_Z_GAP_M = 0.01

SKILL_REQUIRED_ARGS = {
    "approach": ["target_position"],
    "pick": ["target_position"],
    "release": ["release_position"],
    "move_home": [],
    "wait": ["time"],
}


def extract_skill_args(step: dict) -> dict:
    """Extract skill arguments from a plan step dict (removes action_name and step index)."""
    return {k: v for k, v in step.items() if k not in ("action_name", "step", "score")}


def normalize_plan(plan: list) -> list:
    """Fix common LLM parameter name aliases before validation."""
    for step in plan:
        skill = step.get('action_name', '').lower()
        # LLMs often use target_position instead of release_position for release
        if skill == 'release' and step.get('release_position') is None:
            fallback = step.get('target_position')
            if fallback:
                step['release_position'] = fallback
    return plan


def _zone_slot_offset(spacing: float, index: int) -> tuple:
    """Return an (dx, dy) offset for the `index`-th additional occupant of a
    release cluster (index starts at 1 — index 0, the first occupant, keeps
    its own unmodified position and never calls this).

    Fills a line along +y first (ZONE_PLACEMENT_ITEMS_PER_ROW items at
    `spacing` apart), then wraps into additional rows along +x once a row
    is full — i.e. a grid. Grows monotonically away from the untouched
    anchor (rather than a layout centered on it) so no slot can ever land
    back on offset (0, 0) and re-collide with the anchor — a centered,
    odd-width row would put its middle slot exactly there. Unbounded (no
    zone size is known — see distribute_zone_releases): a clamped layout
    would eventually pile later rows on top of each other again once the
    clamp is hit, and unbounded growth can't ever collide.
    """
    items_per_row = max(1, settings.ZONE_PLACEMENT_ITEMS_PER_ROW)
    row, col = divmod(index - 1, items_per_row)
    dy = spacing * (col + 1)
    dx = row * spacing
    return dx, dy


def _find_zone_bounds(x: float, y: float, targets: dict) -> tuple:
    """Return the (x_min, x_max, y_min, y_max) bounds of the smallest
    targets.* zone (ROBOAI-19 shape: bounds.x/y as [min, max] pairs) whose
    footprint contains (x, y), or None if none matches — including when
    `targets` is empty/None, which is the common case for VLM/VLM_LLM
    plans, whose releases are independent depth deprojections and
    essentially never land exactly on a hand-authored zone's own centre.

    Smallest-match-wins for the same reason `_fix_release_height` in
    llm_planner_node.py picks the smallest footprint among candidates: a
    broad catch-all zone (e.g. "table") can legitimately contain a more
    specific one (e.g. "tray"), and the specific one is what the release
    actually means.
    """
    best, best_area = None, None
    for entry in (targets or {}).values():
        bounds = (entry or {}).get('bounds') or {}
        bx, by = bounds.get('x'), bounds.get('y')
        if not bx or not by:
            continue
        x_min, x_max = min(bx), max(bx)
        y_min, y_max = min(by), max(by)
        if not (x_min <= x <= x_max and y_min <= y <= y_max):
            continue
        area = (x_max - x_min) * (y_max - y_min)
        if best_area is None or area < best_area:
            best, best_area = (x_min, x_max, y_min, y_max), area
    return best


def _centered_zone_offset(spacing: float, index: int, half_x: float, half_y: float) -> tuple:
    """Return the (dx, dy) offset of the `index`-th additional occupant
    (index starts at 1) of a release cluster known to sit at the centre
    of a target zone half-width `half_x` and half-depth `half_y` — or
    None if `index` doesn't fit in a grid that size, or the zone is too
    small to offer even one slot.

    Unlike `_zone_slot_offset`, this grid is centred on the anchor rather
    than growing away from it in one direction, because here (and only
    here) the anchor is known to already be the zone's own centre — the
    exact midpoint of `bounds` (see fhp_ffhp_prompts.py's "release into a
    target zone" instruction) — so both directions on each axis have
    equal room. Slots are visited nearest-to-the-anchor first (by
    Chebyshev ring) so occupants stay close together instead of spreading
    to the zone's far corner first, and every slot offered is guaranteed
    to be within (half_x, half_y) of the anchor, i.e. inside the zone.
    """
    if spacing <= 0:
        return None
    cols = max(0, int(half_x // spacing))
    rows = max(0, int(half_y // spacing))
    candidates = sorted(
        (
            (c, r)
            for r in range(-rows, rows + 1)
            for c in range(-cols, cols + 1)
            if not (r == 0 and c == 0)
        ),
        key=lambda cr: (max(abs(cr[0]), abs(cr[1])), abs(cr[1]), abs(cr[0]), cr[1], cr[0]),
    )
    if index - 1 >= len(candidates):
        return None
    c, r = candidates[index - 1]
    return c * spacing, r * spacing


def distribute_zone_releases(plan: list, targets: dict = None) -> list:
    """Space out multiple releases that land on (about) the same spot
    instead of letting them collide — works for LLM, VLM, and VLM_LLM
    plans alike, since it only looks at the release positions the plan
    itself contains, not a scene's static `targets` registry (VLM/VLM_LLM
    release positions are independent depth deprojections and essentially
    never match a hand-authored target position exactly, so a
    targets-lookup approach only ever fires for LLM mode).

    `targets` is the scene's target-zone registry (scene_json's
    `targets`, optional) — used only to keep the spacing grid inside a
    matched zone's own known footprint (`_centered_zone_offset`) instead
    of growing unbounded past it (`_zone_slot_offset`). Omit it (or pass
    a scene predating ROBOAI-19, with no bounds-shaped targets) to get
    the old unbounded-growth behaviour verbatim.

    The first release at a given spot is left untouched (so the common
    single-object case is unchanged). A later release within
    ZONE_PLACEMENT_COLLISION_RADIUS_M of an earlier one is either:
      - left alone, if its z differs from the earlier one by more than
        _STACK_Z_GAP_M — that's an intentional stack (the release-on-top-
        of-target formula), not "spread these out"; or
      - nudged sideways, spaced by the released object's grasp_width
        (falling back to ZONE_PLACEMENT_DEFAULT_SPACING_M) plus
        ZONE_PLACEMENT_MARGIN_M — within the matched zone's bounds via
        `_centered_zone_offset` if the cluster's anchor fell inside one,
        via the unbounded `_zone_slot_offset` otherwise (no zone matched,
        or the zone is too small to offer another in-bounds slot).

    Whenever a release is nudged, the `approach` step immediately before it
    (if its target_position matches the release's pre-nudge x, y) is nudged
    by the same amount — otherwise the arm still flies/descends to the
    original, occupied spot and only sidesteps at the very last moment
    (the release), which is too late to avoid clipping whatever already
    landed there.

    Mutates and returns `plan`. Must run after normalize_plan (so
    release_position is populated) and before PlanValidator, so a nudge
    that ends up outside workspace limits still gets caught.
    """
    clusters = []  # each: {'x', 'y', 'z', 'count', 'zone_half'}

    for i, step in enumerate(plan):
        if step.get('action_name', '').lower() != 'release':
            continue
        pos = step.get('release_position')
        if not pos or len(pos) < 3:
            continue

        cluster = next(
            (c for c in clusters
             if abs(c['x'] - pos[0]) < settings.ZONE_PLACEMENT_COLLISION_RADIUS_M
             and abs(c['y'] - pos[1]) < settings.ZONE_PLACEMENT_COLLISION_RADIUS_M),
            None,
        )
        if cluster is None:
            zone_bounds = _find_zone_bounds(pos[0], pos[1], targets)
            zone_half = (
                ((zone_bounds[1] - zone_bounds[0]) / 2, (zone_bounds[3] - zone_bounds[2]) / 2)
                if zone_bounds else None
            )
            clusters.append({'x': pos[0], 'y': pos[1], 'z': pos[2], 'count': 1, 'zone_half': zone_half})
            continue

        if abs(cluster['z'] - pos[2]) >= _STACK_Z_GAP_M:
            continue  # intentional stack at this spot — leave it alone

        orig_x, orig_y = pos[0], pos[1]
        spacing = (step.get('grasp_width') or settings.ZONE_PLACEMENT_DEFAULT_SPACING_M)
        spacing += settings.ZONE_PLACEMENT_MARGIN_M

        offset = None
        if cluster['zone_half'] is not None:
            offset = _centered_zone_offset(spacing, cluster['count'], *cluster['zone_half'])
        if offset is None:
            offset = _zone_slot_offset(spacing, cluster['count'])
        dx, dy = offset

        pos[0] += dx
        pos[1] += dy
        cluster['count'] += 1

        if i > 0 and plan[i - 1].get('action_name', '').lower() == 'approach':
            approach_pos = plan[i - 1].get('target_position')
            if (approach_pos and len(approach_pos) >= 2
                    and abs(approach_pos[0] - orig_x) < settings.ZONE_PLACEMENT_COLLISION_RADIUS_M
                    and abs(approach_pos[1] - orig_y) < settings.ZONE_PLACEMENT_COLLISION_RADIUS_M):
                approach_pos[0] += dx
                approach_pos[1] += dy

    return plan
