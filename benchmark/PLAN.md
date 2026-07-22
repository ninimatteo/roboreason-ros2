# Benchmark Plan — LLM vs VLM Reasoning-Method Evaluation

Adapted from the taxonomy and metrics in *"LLM-Based Reasoning for Robotic
Planning: Robustness to Task and Environmental Complexity"* (Favali,
Sabattini, Villani — RO-MAN 2025 submission), scoped down to what's
runnable on this physical UR5cb setup in ~3 days with one LLM model, one
VLM model, no dynamic-event injection, and no simulator ground truth.

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
itself (see `bridge_node.py::_record_execution_outcome`, added for this).
The only two numbers you type by hand, immediately after each trial, are:
**"was it safe?"** (y/n) and **"how many sub-tasks actually ended up
correct?"** (an integer) — via `benchmark/benchmark_annotate.py`, which pulls
everything else automatically and computes TS/TSR/AETS itself. That's the
"automatic from the program" part; the two numbers are the honest,
unavoidable manual part on real hardware.

---

## 1. Task matrix

Same three complexity axes as the paper, same "all-easy vs all-hard"
extremes per task (not a full 2³ factorial — matches what you specified).

Real scene objects: **four cubes, same shape, distinguished only by
color** — blue, red, white, orange (you're updating `scene_mock.json`
yourself to match). This makes the specificity axis the natural way
round: **specific** = refer by color ("the red cube", unambiguous),
**lifted/generic** = refer by the shared class ("the cubes", ambiguous
among all 4). Targets: the tray and the table.

Task 3 ("arithmetic with cubes") assigns each color an integer value,
stated directly in the prompt, and asks the model to use addition/
subtraction on those values to decide what to do — genuinely tests
numeric reasoning grounded in the scene instead of just spatial pick-place.
Same fixed mapping throughout, for both difficulty levels and all reps:
**blue = 1, red = 2, white = 3, orange = 4.**

| # | Condition | task_id | Prompt | Length | Specificity | Affordance | Sub-tasks (SC_T) |
|---|---|---|---|---|---|---|---|
| 1a | Pick&Place easy | `pp_easy` | "Pick the red cube and place it on the brown tray." | short (4 steps) | specific | complete | 1 — red cube ends up on brown tray |
| 1b | Pick&Place hard | `pp_hard` | "Put all the cubes on the brown tray." | long (16 steps) | lifted | PAP | 4 — each cube ends up on tray |
| 2a | Sort/Stack easy | `sort_easy` | "Stack the red cube on top of the blue cube." | short (4 steps) | specific | complete | 1 — red cube ends up stacked on blue cube |
| 2b | Sort/Stack hard | `sort_hard` | "Sort all the cubes: put the red cube onto the brown tray, and arrange the rest in a straight line on the table." | long (16 steps) | lifted | PAP | 4 — red@tray + 3 others@table, spread apart |
| 3a | Arithmetic easy | `arith_easy` | "Each cube has a value: blue = 1, red = 2, white = 3, orange = 4. Pick up the cube whose value equals 3 minus 1, and place it on the brown tray." | short (4 steps) | specific | complete | 1 — red cube (value 2) ends up on tray |
| 3b | Arithmetic hard | `arith_hard` | "Each cube has a value: blue = 1, red = 2, white = 3, orange = 4. Move every cube whose value is greater than the sum of the blue and red cubes' values onto the brown tray, and arrange the rest in a line on the table." | long (16 steps) | lifted | PAP | 4 — orange (4 > 3) @tray, blue/red/white @table, spread apart |

3a requires one subtraction (`3 − 1 = 2`) to resolve which single cube is
meant — still "specific" in the sense that matters here (it resolves to
exactly one instance, no enumeration), just reached via a computed
reference instead of a literal color name. 3b requires one addition to
get the threshold (`blue + red = 1 + 2 = 3`), then a comparison against
every cube's value — genuinely "lifted" (no cube is named) and PAP (the
model has to work out the two-group split itself).

Sub-task rules for 1a/1b/2a/3a are cheap to check ("is it visually on/at
the target"). 2b/3b require a bit more care (relative spacing, or getting
the arithmetic right in the first place) — eyeball/measure, don't
overthink precision; this is a planning-logic benchmark, not a metrology
one.

**6 conditions × 10 reps × 2 models = 120 trials.** At ~1-2 min/trial
(planning + execution + a 10-second annotation) that's roughly 3-5 hours
of pure trial time — very doable in 3 days even with setup/debugging
overhead, but the schedule below front-loads coverage in case something
eats more time than expected.

---

## 2. Models

Use whatever's already the default in `config.py` — no reason to add a
new model choice on top of everything else:
- **LLM**: `Settings.MODEL_NAME` (`nebius/nvidia-nemotron-120b` as of this
  session).
- **VLM**: `Settings.VLM_MODEL_NAME` (`groq/qwen3.6-27b`).

Pick a **single `reasoning_method`** for both and hold it fixed across all
120 trials (a model×method interaction isn't part of this study). `cot_sc`
is the natural default — same one that's already been exercised heavily
this session, and per the paper it's the most safety-robust method, so
you're not stacking "reasoning method noise" on top of the LLM-vs-VLM
comparison you actually care about.

---

## 3. Step-by-step

### Day 1 — setup + coarse pass (get *something* in every cell first)

1. Confirm the physical scene matches the task matrix: 4 cubes (blue, red,
   white, orange) on the table, tray present, positions/colors matching
   your updated `scene_mock.json` (LLM mode reads it directly; VLM mode
   just needs the objects visible).
2. Smoke-test both modes once each with a trivial command (e.g. "pick the
   red cube and place it on the tray") to confirm the stack is healthy
   before spending trial budget on it.
3. **Coarse pass — 1 rep of all 6 conditions, both models (12 runs).**
   Goal: catch a broken prompt or a systematic crash *before* committing to
   10 reps of it. After each run, immediately run
   `benchmark/benchmark_annotate.py` (see §5) — don't batch this at the end
   of the day, you'll forget the visual state.
4. If a prompt is ambiguous/broken, fix the wording here (update this doc
   too) — cheap now, expensive after 40 more trials with the old wording.

At the end of Day 1 you already have n=1 across the *entire* comparison
matrix — if the remaining two days fall through, this is still a
(thin but complete) preliminary result set, which is the point of doing
the coarse pass first instead of finishing one cell at a time.

### Day 2 — bulk collection, cheapest task first

Backfill reps 2-10, in this order, so each *finished* task is a complete,
immediately-usable dataset if you have to stop:

1. `pp_easy` / `pp_hard` — both models, reps 2-10 (fastest task, gets a
   full 2×2×10 dataset banked earliest).
2. `sort_easy` / `sort_hard` — both models, reps 2-10.
3. `arith_easy` / `arith_hard` — both models, reps 2-10 (start this last;
   it's the newest/most likely to need prompt iteration, and the sub-task
   rules take longer to verify by eye).

### Day 3 — buffer + finish + analysis

1. Finish whatever's left from Day 2's ordering.
2. Run `benchmark/benchmark_annotate.py --summary` (see §5) to get the
   paper's TS%/TSR%/AETS table, split by model, computed directly from
   `benchmark/results.csv` — no manual spreadsheet work.

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
  `steps_executed` is read automatically from `execution_result.json`
  (see §0) — this is the one metric that's fully automatic given the two
  manual inputs above.

---

## 5. Logging — what's automatic vs. what you type

Automatic, per trial, no action needed:
- `debug/<run_id>/` — command, config (model/method/temperature),
  generated plan, planning success/error (already existed).
- `debug/<run_id>/execution_result.json` and a row in
  `debug/benchmark_summary.csv` — whether `/execute_plan` succeeded, how
  many steps actually ran, and the error if it didn't (new this session,
  written by `bridge_node.py::_record_execution_outcome`).

Manual, ~10 seconds per trial, right after execution finishes: when the
"Benchmark trial" checkbox is on, the GUI itself shows an inline form under
the execution report — pick the `task_id`, answer "was it safe?", enter how
many sub-tasks completed, hit Submit. It computes TS/TSR/AETS and appends
one row to `benchmark/results.csv` right there, no terminal needed.

If you're not at the GUI (or want to annotate/re-annotate a run
afterwards), the same thing is available as a standalone script:

```bash
python3 benchmark/benchmark_annotate.py
```

It reads the most recent *benchmark-flagged* run automatically (skips over
any casual, unflagged runs in between), shows you the command and the
inferred model (LLM/VLM), asks you to pick the `task_id` from a short menu,
then asks the same two questions the GUI form does. Repetition numbers are
tracked automatically either way (counts existing rows for that `task_id` +
model in `benchmark/results.csv`).

Run `python3 benchmark/benchmark_annotate.py --summary` any time to print a
live TS%/TSR%/AETS table grouped by model and by `task_id`, straight from
that CSV.
