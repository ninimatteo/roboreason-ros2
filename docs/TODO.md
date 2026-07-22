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

## 4. `scene_mock.json`: bounding box instead of position + size, for targets

Replace `targets.*.position` (center) + `size` (dimensions) with an
explicit bounding box (e.g. `min`/`max` corners, or a `min` corner + size
if it stays closer to today's shape) — discussed and agreed to hold until
after the current benchmark push. Rationale (from that discussion):

- Removes an *assumed* convention (`position` = center, half-extent =
  `size/2`) that isn't written down anywhere and that this session's own
  bugs got tripped up by more than once (the release-height base-vs-
  contact-point confusion, the footprint-vs-point-distance matching gap in
  `_fix_release_height`). An explicit min/max needs no assumed convention.
- Matches how you'd actually measure a physical zone by hand (two
  corners) better than "find the center, then the half-width."
- Simplifies the footprint-containment code that already exists
  (`llm_planner_node.py::_fix_release_height`'s `contains()`/
  `nudge_outside()`) — direct min/max comparison instead of computing
  half-extents every time — and simplifies the stacking-height prompt
  instruction (`release_position.z = target.position.z + target.size[2]`
  → just `target.z_max`).
- Would fit VLM `bbox` grounding mode more naturally too — a detected
  pixel bbox's corners can be deprojected straight into a real-world
  min/max box, more accurate than estimating a center + width/depth from
  a single click/estimate (`vlm_llm_planner_node.py::_build_generated_scene`
  currently emits `position` + `size` for generated targets the same way).

**Not** proposed for `objects.*` — picking needs a contact *point*
(`position` already serves that), not a box; only targets are genuinely
area-shaped.

Touches (breaking change, needs updating together):
- `llm_planner_node.py::_fix_object_height` / `_fix_release_height`
  (this session's new code).
- The stacking-height instruction duplicated across all 6 LLM prompt
  files (`fhp_ffhp_prompts.py`, `react_prompts.py`, `cot_sc_prompts.py`,
  `tot_prompts.py`, `self_refine_prompts.py`, `always_act_prompts.py`).
- `vlm_llm_planner_node.py::_build_generated_scene`.
- `docs/GRASP_GEOMETRY_PIPELINE.md` and any other docs describing the
  position/size convention.
- `scene_mock.json` itself (yours to edit).

## 5. Rewrite the skill/primitive descriptions for clarity

Revisit the skill descriptions the LLM/VLM actually see — the
`UR5Action` field docstrings (`extraction_classes.py`) and whatever
skill-list text gets embedded in each reasoning method's prompt
(`skills.py` / the per-method `EmbodiedAgentsPrompts/*.py` files) — to
better explain what each primitive (`approach`, `pick`, `release`,
`move_home`, `wait`, and whatever comes out of #2's push/nudge skill)
actually does, what each parameter means, and what convention it follows
(e.g. is `object_height` still worth exposing to the model at all, now
that `_fix_object_height`/`_fix_release_height` compute it deterministically
and ignore whatever the model puts there — see #7, which is closely
related). No concrete plan yet — needs a pass through the current prompt
text to find what's unclear/stale first.

## 6. A "chat" for multiple LLM/VLM calls per task

Currently every reasoning method is a single request/response cycle (or,
for `cot_sc`, several *independent* samples voted on at once) — there's no
notion of an ongoing conversation where follow-up calls can react to
what an earlier call in the *same* task decided. Want to explore adding a
real multi-turn interaction — closer to `ReAct`'s reason/act loop but
generalized — so the agent (or the operator) can issue several LLM/VLM
calls within one task and have later calls see earlier ones' output/state,
rather than every call starting fresh from just the scene + user request.
Directly enables #7 (a model that can call a "fix my plan" skill and see
the result before finalizing).

## 7. Expose the deterministic geometry fixes as skills the model can call

This session added several deterministic, code-side corrections that
silently override whatever the LLM/VLM computed —
`llm_planner_node.py::_fix_object_height`, `_fix_release_height` (surface
height lookup, footprint-overlap nudging), and
`schemas.py::distribute_zone_releases` (collision spacing). All of these
are currently one-shot, invisible post-processing: the model never sees
that its numbers got corrected, and can't ask for a *different* correction
if the automatic one isn't what was actually wanted.

Idea: instead of (or in addition to) applying these automatically, expose
them as callable skills/tools the LLM/VLM can invoke directly — "recompute
this release's height", "space these releases apart", "nudge this point
out of zone X" — as part of a multi-call interaction (see #6), so the
model is actively deciding to use the correction rather than having it
applied behind its back. Would also make failures more diagnosable (the
model's own trace would show *why* a position changed, instead of it
just changing). Biggest open question: how much of the current
hardcoded-in-Python geometry logic to keep as an automatic safety net
regardless (given this session's whole pattern was "don't trust the
model's own arithmetic for real-world geometry") vs. how much to hand over
as an optional tool call.
