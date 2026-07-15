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


def distribute_zone_releases(plan: list) -> list:
    """Space out multiple releases that land on (about) the same spot
    instead of letting them collide — works for LLM, VLM, and VLM_LLM
    plans alike, since it only looks at the release positions the plan
    itself contains, not a scene's static `targets` registry (VLM/VLM_LLM
    release positions are independent depth deprojections and essentially
    never match a hand-authored target position exactly, so a
    targets-lookup approach only ever fires for LLM mode).

    The first release at a given spot is left untouched (so the common
    single-object case is unchanged). A later release within
    ZONE_PLACEMENT_COLLISION_RADIUS_M of an earlier one is either:
      - left alone, if its z differs from the earlier one by more than
        _STACK_Z_GAP_M — that's an intentional stack (the release-on-top-
        of-target formula), not "spread these out"; or
      - nudged sideways via `_zone_slot_offset`, spaced by the released
        object's grasp_width (falling back to ZONE_PLACEMENT_DEFAULT_SPACING_M)
        plus ZONE_PLACEMENT_MARGIN_M.

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
    clusters = []  # each: {'x', 'y', 'z', 'count'}

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
            clusters.append({'x': pos[0], 'y': pos[1], 'z': pos[2], 'count': 1})
            continue

        if abs(cluster['z'] - pos[2]) >= _STACK_Z_GAP_M:
            continue  # intentional stack at this spot — leave it alone

        orig_x, orig_y = pos[0], pos[1]
        spacing = (step.get('grasp_width') or settings.ZONE_PLACEMENT_DEFAULT_SPACING_M)
        spacing += settings.ZONE_PLACEMENT_MARGIN_M
        dx, dy = _zone_slot_offset(spacing, cluster['count'])
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
