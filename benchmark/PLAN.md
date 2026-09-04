# Benchmark Plan — LLM vs VLM vs VLM_LLM Reasoning-Method Evaluation

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

**6 conditions × 10 reps × 3 modes = 180 trials.**

---

## 2. Model (single, unified across all three arms)

`nebius/kimi-k2.6` for **every** call in all three modes — the LLM
planning call, the VLM direct-grounding call, and both halves of
`VLM_LLM` (the scene-grounding call and the subsequent LLM planning
call). Concretely: `Settings.MODEL_NAME` and `Settings.VLM_MODEL_NAME`
both set to `nebius/kimi-k2.6`, and the GUI's "Grounding provider/model"
fields (used by `mode=VLM_LLM`) set to the same.

Why this model: verified clean on real hardware in both text-only and
image modes during ROBOAI-12 (`sort_hard` and `arith_hard`, no errors).
**Not a verified equivalent to July's models** (`nebius/nvidia-nemotron-120b`
for LLM, `groq/qwen3.6-27b` for VLM) — this study is a fresh baseline, not
an extension of July's results.

**Reasoning method: `cot_sc`, fixed across all 180 trials** (matches
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
  comparing `VLM` against `VLM_LLM` — this is the actual hypothesis
  ROBOAI-25 is testing (does hybrid grounding recover arithmetic
  performance toward the LLM arm's level). A second planned test, same
  method, compares `LLM` against `VLM_LLM` on the same condition.
  Significance threshold α = 0.05, stated here in advance.
- **Everything else (the other 5 conditions, any other pairwise arm
  comparison) is reported descriptively with its Wilson CI, not
  hypothesis-tested.** With three arms × six conditions there are enough
  possible pairwise comparisons that testing all of them would need a
  multiple-comparison correction severe enough to be nearly powerless at
  n=10 — better to commit to the two comparisons that matter for the
  paper's actual claim and report the rest as description, not dress up
  exploratory numbers as confirmatory.
- Implementation: `benchmark/plot_results.py` (or a small addition to it)
  computes Wilson intervals and the two Fisher tests directly from
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

---

## 5. Step-by-step

### Day 0 — today, if there's time (setup + coarse pass across all 3 arms)

1. Confirm the physical scene matches the task matrix: 4 cubes (blue, red,
   white, orange) on the table, tray present.
2. Confirm `MODEL_NAME`, `VLM_MODEL_NAME`, and the GUI's grounding
   provider/model are all set to `nebius/kimi-k2.6`; `reasoning_method`
   is `cot_sc` in every panel that has one (planning and, for `VLM_LLM`,
   grounding).
3. **Coarse pass — 1 rep of all 6 conditions, all 3 modes (18 runs).**
   Goal: catch a broken prompt or a systematic crash before committing to
   10 reps of it. Annotate each run immediately with
   `benchmark/benchmark_annotate.py` (see §6) — don't batch it, you'll
   forget the visual state. At `kimi-k2.6`'s observed pace, budget this
   coarse pass at roughly 30-45 min for the VLM/VLM_LLM cells alone.
4. If a prompt is ambiguous/broken, fix the wording here (update this doc
   too) — cheap now, expensive after 50+ more trials with the old
   wording.

At the end of Day 0 you have n=1 across the entire 6×3 matrix — a thin
but complete preliminary result set if the remaining time falls through.

### Day 1 (Monday 7) — bulk collection, cheapest task first, all 3 modes

Backfill reps 2-10 per mode, in this order, so each *finished*
task×mode cell is a complete, immediately-usable dataset if you have to
stop:

1. `pp_easy` / `pp_hard` — all 3 modes, reps 2-10.
2. `sort_easy` / `sort_hard` — all 3 modes, reps 2-10.

### Day 2 (Tuesday 8) — finish collection + analysis

1. `arith_easy` / `arith_hard` — all 3 modes, reps 2-10 (kept last, same
   reasoning as before: most likely to need care in the sub-task check).
2. Run `benchmark/benchmark_annotate.py --summary` for the TS%/TSR%/AETS
   table with Wilson CIs, and the two planned Fisher tests from §3.
3. Regenerate plots on the 3-arm data; annotate the commit hash used for
   collection in `benchmark/results_2026-09_3arm.csv` per ROBOAI-25's DoD.

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
