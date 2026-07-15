# TODO

Things we've identified as worth doing but haven't implemented yet. Not
ordered by priority — pick off whichever fits the next session.

---

## 1. Zone-bounds-aware release placement

`distribute_zone_releases` (`src/robo_reason_manager/robo_reason_manager/schemas.py`)
currently spaces out colliding releases along a line/grid using only the
released object's `grasp_width` — it has no idea how big the tray/zone
actually is, so nothing stops the grid from growing past the tray's
physical edge (a 4th or 5th object could land just outside it).
`PlanValidator` only checks *global* workspace limits, not per-zone bounds,
so this wouldn't be caught anywhere.

Zone size is only reliably known in some modes:
- `LLM`: `scene_mock.json`'s hand-authored `targets.*.size`.
- `VLM_LLM`: the generated scene's target `size` (visually estimated by the
  grounding VLM call, see `_build_generated_scene`).
- `VLM` (plain, point-grounding mode): **no size at all** — a single
  deprojected click carries no footprint. `bbox` grounding mode gives an
  object's size, not the tray's, unless the VLM is also asked to bound the
  target itself.

Plan: add *opportunistic* bounds-checking — when a size happens to be
available (LLM, VLM_LLM), clamp/validate the grid against it (and reject or
re-pack rather than silently overflow); when it isn't (plain VLM point
mode), fall back to today's unbounded behavior. Needs re-plumbing some size
hint back into `distribute_zone_releases`, which currently intentionally
takes only `plan` (no `scene_state`) — see the VLM_LLM/VLM fix in
`docs/SESSION_CONTEXT.md` for why that dependency was dropped in the first
place; bringing size back needs to stay optional so it doesn't reintroduce
the LLM-only-matching problem that fix solved.

## 2. Non-grasping "push/nudge" skill (move an object via the EE, no grip)

A new skill that repositions an object by contacting and sliding it with
the end-effector, without ever closing the gripper on it — e.g. nudging an
object into place, clearing a path, or repositioning something the
gripper can't/shouldn't grasp.

Open questions to resolve before implementing:
- Naming/action shape: probably a new `ALLOWED_SKILLS` entry (alongside
  `approach | pick | release | move_home | wait` in `schemas.py`) — likely
  needs a start-contact point and an end point (could reuse
  `target_position`/`release_position` as start/end, or add a new field to
  `UR5Action`).
- Executor side (`ur5_skill_executor_node.py`): needs a contact-and-slide
  trajectory (descend to contact height without closing the gripper, move
  laterally, retract) — different geometry from pick's mid-body descent.
- Reasoning/prompt side: every reasoning method's prompt would need to
  learn this skill exists and when to prefer it over pick+release, across
  all 6 prompt files (LLM + VLM variants), the same way `grasp_width` and
  bbox grounding were threaded through everywhere in the earlier passes.

## 3. Real tray-dimension-aware "best positioning"

Distinct from #1's bounds-*checking*: this is about actively using the
tray's real physical size to compute the *best* placement (not just a
safe, non-colliding one) — e.g. centering the layout, choosing the axis
that best fits the tray's actual aspect ratio instead of always growing
along a fixed axis (currently hardcoded to +y then +x — see
`_zone_slot_offset`), or packing more optimally than a simple line/grid.

Raised alongside #1 as "maybe it can be a skill?" — worth deciding whether
this lives inside `distribute_zone_releases` (deterministic, code-side) as
today's line/grid does, or becomes an explicit skill the LLM can invoke
with a stated intent ("arrange neatly" vs "just don't collide"). Depends on
resolving #1 first, since both need real zone dimensions plumbed through.
