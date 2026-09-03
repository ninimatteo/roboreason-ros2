# Object/Target Geometry Pipeline — LLM vs VLM vs VLM_LLM

How each planning mode decides an object's/target's **height**, **position**,
and **grasp width**, and how that flows into the final pick/release z the
robot actually executes. This is the "who computes what, and from where"
reference for the three modes; see `docs/ROBOREASON_GUIDE.md` for
launch/operator instructions.

---

## TL;DR

| Quantity | `LLM` | `VLM` | `VLM_LLM` |
|---|---|---|---|
| Object height | hand-authored in `scene_mock.json` | **depth-derived** (`table_surface_z - top_z`) | **depth-derived**, computed once when the generated scene is built |
| Pick contact z | `object.position.z` (as authored) | depth top_z, pulled down (mid-body, clamp-capped) | same clamp math as `VLM`, baked into the generated scene's `position.z` |
| Target height (stacking) | hand-authored `size[2]` | n/a — deprojection gives the real top z directly | **depth-derived**, `position.z`/`size[2]` split so the downstream LLM formula reconstructs the true top z |
| Grasp width | hand-authored `size[0]` | VLM's **visual guess** (not depth-verifiable) | VLM's **visual guess**, carried through the generated scene |
| Held-object lift on release | `object_height` set by LLM from `size[2]`, applied by executor | `object_height` overwritten with depth-derived height, applied by executor | same as `LLM` (it *is* the `LLM` pipeline, fed a generated scene) |
| TCP offset (flange→contact) | interpolated from `grasp_width` at execution time — same in all three modes (`ur5_skill_executor_node.py`) | | |

The one quantity that is **never** depth-verified in any mode is grasp
**width** — a single top-down depth pixel only tells you height (distance to
the visible top surface), not how wide the object is left-to-right. This is
the main motivation for the homographic-transform experiment described in
`docs/SESSION_CONTEXT.md` / the `feature/vlm-homography` branch.

---

## Shared building blocks

- **`table_surface_z`** — read from `workspace.table.surface_z` in whatever
  scene JSON is in play (the static `scene_mock.json` for `LLM`, or the
  request's `scene_json` for `VLM`/`VLM_LLM`, which is really the same file
  passed through unchanged). This is the one "ground truth" height reference
  every depth-derived computation is anchored to.
- **`PICK_GRASP_DEPTH_FRACTION`** (default `0.5`) — how far below an object's
  top surface to descend for a *mid-body* grasp, as a fraction of its real
  height.
- **`MIN_OBJECT_HEIGHT_M`** (default `0.02`) — floor for depth-derived
  *object* height, so a noisy/flat depth reading doesn't produce a
  near-zero or negative descent. **Not** applied to target height (see
  below) — a flat target zone marked on the table should read ~0 height,
  not be artificially lifted.
- **`TCP_CLAMP_CLEARANCE_M`** (default `0.15`) — the RG2 gripper's rigid
  clamp/mount body (between the flange and the pivoting fingers) crashes
  into an object if the flange descends more than
  `TCP_OFFSET_Z - TCP_CLAMP_CLEARANCE_M` below its top surface. Pick descent
  is capped at this value, so very tall objects get gripped nearer their top
  instead of mid-body.
- **`TCP_OFFSET_Z_CALIBRATION`** — a 3-point (object width → flange-to-contact
  offset) calibration table for the RG2 fingers, which pivot/arc so the
  offset shrinks as they open wider:

  | Object width | TCP offset Z |
  |---|---|
  | 0.00 m (fully closed) | 0.213 m |
  | 0.05 m (mid-open) | 0.207 m |
  | 0.10 m (fully open) | 0.175 m |

  `tcp_offset_z_for_width()` (`robo_reason_bringup/config.py`) does
  piecewise-linear interpolation between these points (clamped at the
  endpoints), falling back to the mid-open default when width is unknown
  (`grasp_width <= 0`).

---

## Mode 1 — `LLM` (`llm_planner_node.py`)

No camera involved. The planner reads the static, hand-authored
`scene_mock.json` and passes it verbatim as `environment_map` to the LLM.
Every number is only as accurate as whoever last edited that file.

- **Object height/position**: `object.position` and `object.size` come
  straight from the JSON. The prompt
  ([fhp_ffhp_prompts.py](../src/robo_reason_reasoning/robo_reason_reasoning/EmbodiedAgentsPrompts/fhp_ffhp_prompts.py))
  instructs: `target_position.z = object.position.z` — i.e. `position.z` is
  treated as the grasp **contact point**, not a base/bottom reference.
- **Grasp width**: the LLM is told to set `grasp_width = object.size[0]`.
- **Release height**:
  - Flat surface: `release_position.z = surface_z`.
  - Stacking on a target: `release_position.z = target.position.z +
    target.size[2]`. Here `target.position.z` is treated as a **base**
    reference and `size[2]` as the height needed to reach the top — this is
    the convention `scene_mock.json` was authored with, and the reason the
    `VLM_LLM` fix below exists (see "Known pitfall").
- **Held-object lift**: the LLM also sets `object_height = size[2]` of the
  *held* object on the `release` action; the executor
  (`ur5_skill_executor_node.py`) adds this to `release_position.z` at
  execution time so the object's bottom lands on the surface, not the TCP.

## Mode 2 — `VLM` (`vlm_planner_node.py`)

The VLM looks at the live camera frame directly and plans pixel-coordinate
actions in one shot. The scene JSON is only consulted for
`workspace.table.surface_z` — objects/targets are never read from it.

- **Object height**: computed from real depth *after* the VLM proposes a
  pick pixel and it's deprojected: `height = max(table_surface_z -
  deprojected_top_z, MIN_OBJECT_HEIGHT_M)`. This **overwrites** whatever
  `object_height` the VLM guessed in its own output.
- **Pick contact point**: the deprojected `target_position.z` is pulled down
  by `min(PICK_GRASP_DEPTH_FRACTION * height, TCP_OFFSET_Z -
  TCP_CLAMP_CLEARANCE_M)` — a mid-body grasp, capped for tall objects.
- **Grasp width**: the VLM visually estimates it directly in the prompt (a
  single top-down depth point can't disambiguate width the way it can
  height, so there's no depth cross-check here).
- **Release height**: the VLM points at the pixel center of the target
  surface/object to stack on, and **deprojection alone already gives the
  real top-surface z** — no size arithmetic needed. The prompt explicitly
  says "do NOT add any z offset manually" for exactly this reason.
- **Held-object lift**: same mechanism as `LLM` mode — the (now
  depth-corrected) `object_height` is copied onto the `release` step, and
  the executor adds it at execution time.

## Mode 3 — `VLM_LLM` hybrid (`vlm_llm_planner_node.py`)

One VLM call *grounds a scene* (objects + targets as pixel centers only — no
actions), deprojects everything to world `[x, y, z]`, assembles a
`scene_mock.json`-shaped JSON file (the real `scene_mock.json` is never
touched), then hands that off to **the same LLM planning pipeline as Mode
1**. From the LLM's point of view this is indistinguishable from `LLM`
mode — all the interesting logic is in how the generated scene is built
(`_build_generated_scene`):

- **Objects**: `top_z` comes from real deprojection. If `table_surface_z` is
  known, `height = max(table_surface_z - top_z, MIN_OBJECT_HEIGHT_M)`
  **replaces** the VLM's blind `size[2]` guess, and `position.z = top_z -
  min(PICK_GRASP_DEPTH_FRACTION * height, TCP_OFFSET_Z -
  TCP_CLAMP_CLEARANCE_M)` — the same mid-body/clamp-capped contact point as
  `VLM` mode, just precomputed into the scene instead of during per-step
  deprojection.
- **Targets**: same idea, but **no height floor** — a flat zone marked
  directly on the table should read ~0 height, not get lifted like a
  graspable object would:
  `height = max(top_z - table_surface_z, 0.0)`, `size[2] = height`,
  `position.z = top_z - height`. This makes `position.z` a **base**
  reference again, so when the downstream LLM applies its normal stacking
  formula (`position.z + size[2]`), it reconstructs the real,
  depth-measured top surface — see "Known pitfall" below.
- **Grasp width**: still a blind VLM visual estimate, carried through the
  generated scene's `size[0]` and consumed by the LLM exactly as in `LLM`
  mode.
- **Release height / held-object lift**: identical mechanics to `LLM` mode
  from this point on, since it *is* that pipeline — the fix below just
  ensures the input fed into it is dimensionally consistent with what it
  assumes.

### Known pitfall (fixed) — double-counted release height

`scene_mock.json`'s convention is `position.z` = base reference,
`size[2]` = height, so `position.z + size[2]` = top surface. Early hybrid
code set `target.position.z` to the raw (already-correct) deprojected top
surface **and** left the VLM's un-verified `size[2]` guess untouched — so
the LLM's normal stacking formula added a guessed height on top of an
already-correct value, inflating every stack-on-target release by however
wrong that guess was. Fixed by deriving the target's real height from depth
and writing `position.z` back as a base reference (see "Targets" above), so
the existing LLM formula reconstructs the true top surface instead of
double-counting it.

---

## Executor-side TCP offset switching (`ur5_skill_executor_node.py`)

Independent of which mode produced the plan, every `pick` action carries a
`grasp_width`. Before the grasp move:

```python
tcp_offset_z = tcp_offset_z_for_width(grasp_width if grasp_width > 0.0 else None)
self._robot_model.tool = SE3(TCP_OFFSET_X, TCP_OFFSET_Y, tcp_offset_z)
```

This updates the `roboticstoolbox` tool transform used by IK/FK for that
motion. After the corresponding `release` opens the gripper, the tool offset
is reset to the default (`TCP_OFFSET_Z`, mid-open calibration) so subsequent
moves start from a known baseline until the next `pick` sets it again.

---

## Open limitation

Grasp **width** is the one quantity none of the three modes can currently
verify against depth — it's always a visual guess (hand-authored for `LLM`,
VLM-estimated for `VLM`/`VLM_LLM`). A perspective/homographic rectification
of the camera image before it reaches the VLM (using depth to build a better
top-down warp) is being explored on a separate branch specifically to make
object *centering* and *width* estimates more consistent — see the
`feature/vlm-homography` branch.
