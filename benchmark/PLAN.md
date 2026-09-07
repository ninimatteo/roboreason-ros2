# Benchmark Plan — LLM vs VLM Reasoning-Method Evaluation

Adapted from the taxonomy and metrics in *"LLM-Based Reasoning for Robotic
Planning: Robustness to Task and Environmental Complexity"* (Favali,
Sabattini, Villani — RO-MAN 2025 submission), scoped down to what's
runnable on this physical UR5cb setup with no dynamic-event injection and
no simulator ground truth.

**Rewritten 2026-09-04 (ROBOAI-25).** Supersedes the two-arm version of
this plan. Two changes from that version:

1. **Three arms instead of two** — `mode=LLM`, `mode=VLM`, and
   `mode=VLM_LLM` all run fresh, not "VLM_LLM added on top of the July
   LLM/VLM data." July's VLM-arm model (`qwen3-2.5-70b`) is gone from
   Nebius's catalog (see ROBOAI-12); rather than mix models across arms,
   this whole study is a new baseline on one unified model.
2. **The statistical criterion is declared here, before any trial runs**,
   not chosen after looking at the numbers — see §4.

**Addendum 2026-09-07.** Collection for this cycle runs on
`feature/ROBOAI-19-bbox-scene-schema`, not `main` — ROBOAI-19 (the
`targets.*` bounds schema migration) is verified working on real hardware
but still formally In Review, not yet merged. Running the campaign on
this branch doubles as an extended real-hardware review of that change:
if it holds up across the full run, that's stronger evidence than a
manual look at the diff. Record the branch alongside the commit hash in
the DoD's "commit hash used for collection" step (§5).

This does **not** relax the code-freeze rule, it just moves which branch
it applies to: if a bug in ROBOAI-19's code surfaces mid-collection, stop
the campaign immediately, fix it on this same branch (not on `main`), and
restart collection from rep 1 rather than resuming — a fix partway
through means the arms are no longer running the same code version, which
is exactly what the freeze exists to prevent. Only merge to `main` once
the full run completes clean.

**Addendum 2026-09-07 (second).** `VLM_LLM` is dropped from this cycle —
two arms, `LLM` and `VLM`, not three. Not a software defect: a real
object in the camera's field of view (a loose cardboard flap propped at
an angle on a tray-like container, off to the side of the workspace) has
no usable depth at that viewing angle, and the VLM's scene-grounding step
correctly, repeatedly detects it as a candidate target — `_deproject_points`
then fails outright on it ("No valid depth at u=..., v=..."), taking down
the whole `VLM_LLM` plan. Physical setup issue, not something a code fix
resolves; out of scope to chase further this cycle. Three `VLM_LLM` trials
already completed cleanly before this started recurring (`pp_easy`,
`pp_hard`, `sort_easy` in `benchmark/results_2026-09_3arm.csv`) — left in
place as partial data, not deleted.

`benchmark/results_2026-09_3arm.csv` keeps its `_3arm` name despite the
scope drop, for continuity with the rows already in it — not worth a
rename mid-collection (every reader/writer of that path would need
updating in lockstep). Read `_3arm` as "this cycle's results file", not
as a live claim about arm count.

---

## 0. What's different from the paper (read this first)

The paper validates in a symbolic simulator, where the ground-truth world
state is always exactly known, so **TS** (Task Safety) and **TSR** (Task
Success Rate) are 100% automatic. On a real robot:

- **TS** (did anything unsafe happen — collision, near-miss) has no sensor
  in this codebase that detects it (no force/torque fault threshold
  anywhere in `ur5_skill_executor_node.py`). It needs a human observer —
  you, standing at the robot, same as any real hardware trial.
- **TSR** (did each sub-task's final-state rule hold — e.g. "the red cube
  is on the tray") has no vision-based verification step. It needs a
  quick visual check after each trial.

So this plan is **semi-automatic**: everything the program can know
(command, model, config, generated plan, how many steps actually executed,
service-level errors) is logged automatically to
`debug/summary.csv` + `debug/benchmark_summary.csv` by the GUI backend
itself (see `bridge_node.py::_record_execution_outcome`). The only two
numbers you type by hand, immediately after each trial, are: **"was it
safe?"** (y/n) and **"how many sub-tasks actually ended up correct?"** (an
integer) — via `benchmark/benchmark_annotate.py`, which pulls everything
else automatically and computes TS/TSR/AETS itself.

---

## 1. Task matrix (unchanged from July)

Same three complexity axes as the paper, same "all-easy vs all-hard"
extremes per task. Real scene objects: **four cubes, same shape,
distinguished only by color** — blue, red, white, orange. Targets: the
tray and the table.

Task 3 ("arithmetic with cubes") assigns each color an integer value,
stated directly in the prompt: **blue = 1, red = 2, white = 3, orange =
4**, fixed throughout.

| # | Condition | task_id | Prompt | Length | Specificity | Affordance | Sub-tasks (SC_T) |
|---|---|---|---|---|---|---|---|
| 1a | Pick&Place easy | `pp_easy` | "Pick the red cube and place it on the brown tray." | short (4 steps) | specific | complete | 1 — red cube ends up on brown tray |
| 1b | Pick&Place hard | `pp_hard` | "Put all the cubes on the brown tray." | long (16 steps) | lifted | PAP | 4 — each cube ends up on tray |
| 2a | Sort/Stack easy | `sort_easy` | "Stack the red cube on top of the blue cube." | short (4 steps) | specific | complete | 1 — red cube ends up stacked on blue cube |
| 2b | Sort/Stack hard | `sort_hard` | "Sort all the cubes: put the red cube onto the brown tray, and arrange the rest in a straight line on the table." | long (16 steps) | lifted | PAP | 4 — red@tray + 3 others@table, spread apart |
| 3a | Arithmetic easy | `arith_easy` | "Each cube has a value: blue = 1, red = 2, white = 3, orange = 4. Pick up the cube whose value equals 3 minus 1, and place it on the brown tray." | short (4 steps) | specific | complete | 1 — red cube (value 2) ends up on tray |
| 3b | Arithmetic hard | `arith_hard` | "Each cube has a value: blue = 1, red = 2, white = 3, orange = 4. Move every cube whose value is greater than the sum of the blue and red cubes' values onto the brown tray, and arrange the rest in a line on the table." | long (16 steps) | lifted | PAP | 4 — orange (4 > 3) @tray, blue/red/white @table, spread apart |

Sub-task rules for 1a/1b/2a/3a are cheap to check ("is it visually on/at
the target"). 2b/3b require a bit more care (relative spacing, or getting
the arithmetic right in the first place) — eyeball/measure, don't
overthink precision; this is a planning-logic benchmark, not a metrology
one.

**6 conditions × 10 reps × 2 modes = 120 trials** (see the 2026-09-07
addendum above — `VLM_LLM` dropped from this cycle).

---

## 2. Model (single, unified across both arms)

`nebius/kimi-k2.6` for **every** call in both modes — the LLM planning
call and the VLM direct-grounding call. Concretely: `Settings.MODEL_NAME`
and `Settings.VLM_MODEL_NAME` both set to `nebius/kimi-k2.6`.

Why this model: verified clean on real hardware in both text-only and
image modes during ROBOAI-12 (`sort_hard` and `arith_hard`, no errors).
**Not a verified equivalent to July's models** (`nebius/nvidia-nemotron-120b`
for LLM, `groq/qwen3.6-27b` for VLM) — this study is a fresh baseline, not
an extension of July's results.

**Reasoning method: `cot_sc`, fixed across all 120 trials** (matches
July — a model×method interaction isn't part of this study).

**Timing note:** `kimi-k2.6` is measurably slower than July's models —
observed ~1-2 min per VLM-mode `cot_sc` call during ROBOAI-12 testing
(vs. July's models finishing in seconds). Budget accordingly; see §5.

---

## 3. Statistical criterion (declared before collection, per ROBOAI-28)

n=10 per cell is small enough that point estimates alone invite reviewer
pushback. Decided **now**, before seeing any September data, so it can't
be picked after the fact to fit whatever the numbers turn out to be:

- **Every reported TS%/TSR% is a Wilson score interval at 95% confidence**,
  not a bare proportion. Wilson over normal-approximation because it
  doesn't produce out-of-[0,1] bounds at small n, and over Clopper-Pearson
  because it isn't needlessly conservative here.
- **One planned hypothesis test, not an exploratory sweep of all pairwise
  comparisons:** two-sided Fisher's exact test on `arith_hard` TSR,
  comparing `LLM` against `VLM` — this is the actual hypothesis ROBOAI-25
  is testing (does grounding modality — an exact text description of the
  scene versus reading it back out of an image — affect performance on
  the task that leans hardest on correct object-identity reasoning).
  Significance threshold α = 0.05, stated here in advance.
- **Everything else (the other 5 conditions) is reported descriptively
  with its Wilson CI, not hypothesis-tested.** With two arms there's
  only one possible pairwise comparison per condition; committing to a
  single one (the condition that matters most for the paper's actual
  claim) keeps the test properly confirmatory instead of an exploratory
  sweep across all six dressed up as one.
- Implementation: `benchmark/plot_results.py` (or a small addition to it)
  computes the Wilson intervals and the Fisher test directly from
  `benchmark/results_2026-09_3arm.csv` — no manual spreadsheet work, same principle as
  the existing automatic TS/TSR/AETS computation.

---

## 4. Metrics (ported directly from the paper, Eq. 14-16)

- **TS** (Task Safety): 1 if the trial was safe (no collision/damage), 0
  otherwise. You supply this (`safety_ok`); nothing in the codebase
  currently senses it.
- **TSR** (Task Success Rate): `sub_tasks_completed / sub_tasks_required`.
  `sub_tasks_required` is fixed per `task_id` (table in §1); you supply
  `sub_tasks_completed` after eyeballing the final scene.
- **AETS** (Action Efficiency on Task Success):
  `sub_tasks_completed / (sub_tasks_required × steps_executed)`.
  `steps_executed` is read automatically from `execution_result.json`.

**Timing (new 2026-09-04, not in the paper).** Fully automatic, no manual
input — both durations are measured at the actual service-call boundary,
not estimated:
- **`planning_duration_s`**: wall-clock time from the planner node
  starting to handle the command to the plan being fully formed. This is
  also "how long until the robot starts moving" in this architecture —
  there's no streaming/partial execution, the robot can't move before the
  whole plan comes back. Measured in `DebugRun.finish()`
  (`debug_recorder.py`) and written to `debug/summary.csv`.
- **`execution_duration_s`**: wall-clock time for the `/execute_plan` call
  itself — the robot (or fake executor) actually running the plan.
  Measured in `bridge_node.py::execute_command()` around the service call,
  captured whether it succeeds, errors, or raises. Written to
  `execution_result.json` and `debug/benchmark_summary.csv`.
- **`total_duration_s`**: `planning_duration_s + execution_duration_s`,
  computed at annotation time, not independently measured — "how long the
  whole request took start to finish."

All three land in `benchmark/results_2026-09_3arm.csv` alongside TS/TSR/AETS.
Expect `planning_duration_s` to be the more interesting one across arms —
`kimi-k2.6` was observed at ~1-2 min per VLM-mode `cot_sc` call during
ROBOAI-12 testing — worth a look even outside the planned statistical
test in §3.

---

## 5. Step-by-step

### Day 0 — today, if there's time (setup + coarse pass across both arms)

1. Confirm the physical scene matches the task matrix: 4 cubes (blue, red,
   white, orange) on the table, tray present.
2. Confirm `MODEL_NAME`, `VLM_MODEL_NAME`, and the GUI's grounding
   provider/model are all set to `nebius/kimi-k2.6`; `reasoning_method`
   is `cot_sc` in every panel that has one.
3. **Coarse pass — 1 rep of all 6 conditions, both modes (12 runs).**
   Goal: catch a broken prompt or a systematic crash before committing to
   10 reps of it. Annotate each run immediately with
   `benchmark/benchmark_annotate.py` (see §6) — don't batch it, you'll
   forget the visual state. At `kimi-k2.6`'s observed pace, budget this
   coarse pass at roughly 20-30 min for the VLM cells alone.
4. If a prompt is ambiguous/broken, fix the wording here (update this doc
   too) — cheap now, expensive after 50+ more trials with the old
   wording.

At the end of Day 0 you have n=1 across the entire 6×2 matrix — a thin
but complete preliminary result set if the remaining time falls through.

### Day 1 (Monday 7) — bulk collection, cheapest task first, both modes

Backfill reps 2-10 per mode, in this order, so each *finished*
task×mode cell is a complete, immediately-usable dataset if you have to
stop:

1. `pp_easy` / `pp_hard` — both modes, reps 2-10.
2. `sort_easy` / `sort_hard` — both modes, reps 2-10.

### Day 2 (Tuesday 8) — finish collection + analysis

1. `arith_easy` / `arith_hard` — both modes, reps 2-10 (kept last, same
   reasoning as before: most likely to need care in the sub-task check).
2. Run `benchmark/benchmark_annotate.py --summary` for the TS%/TSR%/AETS
   table with Wilson CIs, and the planned Fisher test from §3.
3. Regenerate plots on the collected data; annotate the branch and commit
   hash used for collection (`feature/ROBOAI-19-bbox-scene-schema`, see
   the 2026-09-07 addendum above) in `benchmark/results_2026-09_3arm.csv`
   per ROBOAI-25's DoD.

---

## 6. Logging — what's automatic vs. what you type

Automatic, per trial, no action needed:
- `debug/<run_id>/` — command, config (model/method/temperature),
  generated plan, planning success/error.
- `debug/<run_id>/execution_result.json` and a row in
  `debug/benchmark_summary.csv` — whether `/execute_plan` succeeded, how
  many steps actually ran, and the error if it didn't.

Manual, ~10 seconds per trial, right after execution finishes: when the
"Benchmark trial" checkbox is on, the GUI shows an inline form under the
execution report — pick the `task_id`, answer "was it safe?", enter how
many sub-tasks completed, hit Submit. It computes TS/TSR/AETS and appends
one row to `benchmark/results_2026-09_3arm.csv`.

Standalone alternative:

```bash
python3 benchmark/benchmark_annotate.py
```

It reads the most recent *benchmark-flagged* run automatically, shows the
command and the inferred mode/model, asks for the `task_id`, then asks
the same two questions the GUI form does. Repetition numbers are tracked
automatically (counts existing rows for that `task_id` + mode + model in
`benchmark/results_2026-09_3arm.csv`).

Run `python3 benchmark/benchmark_annotate.py --summary` any time for a
live TS%/TSR%/AETS table (with Wilson CIs, per §3) grouped by mode and by
`task_id`, straight from that CSV.
