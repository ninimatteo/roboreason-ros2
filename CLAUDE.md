# CLAUDE.md

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project map: where information lives

Each kind of information has exactly one owner. When you need something, go to
its owner rather than trusting a copy found elsewhere.

| Source | Owns | Access |
|---|---|---|
| **Jira project `ROBOAI`** | Work state: what to do, priority, due dates, what was decided | Atlassian connector, `cloudId` `1e620492-53d5-48f9-8ab1-2bdf6939d767` |
| **`docs/` in this repo** | Technical knowledge tied to the code | Read directly |
| **Overleaf** | The paper being written, and nothing else | Link in Jira `ROBOAI-6` |
| **SharePoint / Teams** | Binaries and things shared with people who don't use git: paper PDFs, decks, the SotA paper folder | Links in Jira `ROBOAI-3`, `ROBOAI-4`, `ROBOAI-8` |

**Jira is live — do not cache it here.** This file tells you how to reach it,
never what is currently in it. Any status written into a markdown file goes
stale and starts lying. To find the current state of the work, query Jira:

- What to work on next: open issues in `ROBOAI` ordered by due date.
- Why something is the way it is: the issue description, which cites the
  relevant `docs/` path and source commit.

`docs/` is organised by how a document is used, not by topic. `docs/README.md`
is the index; the short version:

- `docs/guide/` — how to install and run the system. `operator-guide.md` is the
  full operator reference.
- `docs/reference/` — how a subsystem works. `grasp-geometry-pipeline.md` covers
  grasp and release geometry across the three modes.
- `docs/history/` — dated, append-only records. `session-context.md` is the
  running technical log (long: grep it for a subsystem before reading it end to
  end). `audit-2026-07-08.md` is the read-only code audit whose safety findings
  are tracked as Jira bugs.
- `docs/paper/` — LaTeX sources for anything that becomes a PDF. `report/` is
  the technical report, `planning/` the three candidate paper outlines (outline
  B is the one being written; A and C are recorded but not pursued).
- `docs/archive/` — kept but unmaintained, excluded from git.
- `benchmark/PLAN.md` — the benchmark protocol, and `benchmark/results.csv` the
  data behind it.

`docs/TODO.md` is a stub. The R1 to R7 backlog it used to hold now lives in Jira
as `ROBOAI-18` through `ROBOAI-24`.

**Markdown or PDF.** Anything read by people working on the code stays `.md` and
is read on GitHub: `guide/`, `reference/`, `history/`. Anything destined for
someone outside the team is written in LaTeX under `docs/paper/` from the start
and shipped as a compiled PDF to SharePoint. Do not write a document in markdown
and hand-convert it later; that is where versions diverge. Overleaf holds only
the paper being co-written, and is not a place to read markdown.

## Working conventions

**Branch only when the code can break something.** Work that touches runtime
code gets a branch named with the Jira key, `feature/ROBOAI-19-bbox-scene-schema`.
Docs, chores and single-file edits go straight to `main` — a branch and a merge
for a documentation commit is pure overhead on a repo with one committer.

**Commits carry the Jira issue key**, on `main` too, so Jira links the commit to
the issue automatically:

```
ROBOAI-19 implement bbox target schema in scene_mock.json
```

Smart commits write to Jira without opening it:

```
ROBOAI-25 #comment raccolti i primi 20 trial #time 3h
```

**Comparing variants: flag, not branch.** If two versions need to run the same
afternoon and land in the same table, they are a `Settings` field in `config.py`
overridable by a `ROBOREASON_*` env var, the way `reasoning_mode` and
`VLM_GROUNDING_MODE` already work — the benchmark harness records the config of
every trial, so the variant is traceable in the data. A branch is only for
changes that cannot coexist in the same code. When two branches genuinely need
to be available at once, use `git worktree` rather than switching checkouts.

**Jira writing style.** Issue descriptions are written in Italian with
technical terms left in English (benchmark, calibration, trajectory, gripper,
plan, prompt, grounding, failure mode, trial). Issue titles stay in English.
No em dashes and no hyphenated words in prose anywhere; hyphens survive only
inside identifiers and labels (`phase-a`, `icra-2027`, `VLM_LLM`, `sort_hard`).
Match this when creating or editing issues.

**Code freeze during data collection.** While a benchmark arm is being
collected, do not change code that affects runtime behavior, even to fix a
known bug. The three arms of the study must run on the same version or the
comparison is invalid. This is why the safety bugs in the hardware epic are
deliberately scheduled after collection rather than before.

**Deterministic geometry over model arithmetic.** The project's consistent
finding is that LLM and VLM arithmetic on real-world geometry is the weak
link, not perception. Heights, offsets and release positions are computed in
Python and override whatever the model produced. Preserve this pattern unless
explicitly asked to revisit it.

**Check for overlapping work before touching already-collected experimental
data.** Nothing stops two sessions (or a session and the user) from working
the same numbers in parallel on different branches — this happened on
2026-09-09 (ROBOAI-6 vs ROBOAI-28/31, see `session-context.md`'s Session 6
§8): two independent statistical-treatment implementations of the same
PLAN.md criterion, disagreeing with each other, one already written into
the paper draft before the other was even started. Before starting work
that touches a results CSV, a report section, or an outline once real data
exists in it: read `session-context.md`'s most recent entries and check
Jira for other In Progress/In Review issues over the same data. It won't
catch everything (the other session here left no Jira trace until after
the fact), but it costs one read and would have caught this one.

## Work sessions

Two project skills wrap the start and end of a working session:

- `/sessione-inizio` — reads the current state from Jira, picks the next task by
  due date and dependencies, moves it to In Progress, and starts the Clockify
  timer.
- `/sessione-fine` — stops the timer, writes the time and a summary to Jira,
  updates `docs/history/session-context.md` when something durable happened,
  transitions the issue, and proposes the next task.

Jira transition ids on this project are global: `11` To Do, `21` In Progress,
`31` In Review, `41` Done.

Clockify is driven by `.claude/scripts/clockify.sh` (`start`, `stop`, `status`).
It reads its API key from `CLOCKIFY_API_KEY` or `~/.config/clockify/api_key`,
both outside the repo. Never ask the user to paste the key into the chat, and
never write it to a file in the repo. If the key is missing, say so in one line
and continue — time tracking must not block the work.

**Keep status live, not just at session boundaries.** Don't wait for
`/sessione-fine` to reflect reality in Jira. Transition an issue the moment
its actual state changes — In Review when a change is ready and self-tested
but not merged, Done when it's merged/delivered, back to In Progress if
review finds real problems. A status that's stale for hours because "the
session will update it later" defeats the point of Jira being the live
source of truth (see the project map above).

**Mid-session pivots — new issue found while working another.** If working a
task turns up a genuine separate issue (typically a bug, on real hardware or
in code) that isn't what the session started on: create the Jira issue right
away (`createJiraIssue`, project `ROBOAI`) rather than only mentioning it in
chat, and switch onto it the same way a session start would —
`transitionJiraIssue` to In Progress, `clockify.sh stop` the running entry,
`clockify.sh start "ROBOAI-xx ..."` under the new key. Link it to the issue
being worked when the connection matters (`createIssueLink`). When attention
returns to the original task, switch back the same way (stop, start under the
original key — Clockify has no "resume," a fresh entry under the same
description is normal). Whether to pivot immediately or just log the issue
and keep going is a judgment call (does it block the current task?) — when
genuinely unsure, ask rather than silently choosing either way.

**Weekly board grooming, at session start.** Jira issues default to sitting
in the backlog until someone acts on them — don't let "someone" always be the
user. As part of `/sessione-inizio` §1, alongside picking the next task, pull
what's due this week (`duedate` within the next 7 days) and surface it
explicitly in the session summary, unprompted. Caveat: the Jira tools
available here cover issue fields, status and comments, not the Agile
board/backlog API — there's no "move to board" or rank endpoint exposed, so
this can't be a literal backlog-to-board drag from here. Until that changes,
the reachable equivalent is calling the due-this-week issues out by name in
the summary (already required above) — good enough to make sure they're
seen, not a substitute for the real thing if a proper board-move ever becomes
available.

**Resuming across sessions and chats.** There is no local state file, and none
should be created: it would be a copy of Jira and would diverge. The context
lives in three places that are always current — Jira holds the In Progress task
and the session comments, `docs/history/session-context.md` holds the technical
record, and this file says where to look. Starting tomorrow in a different chat
means running `/sessione-inizio`.

## Build

```bash
# Standard build (must be outside any Python venv)
cd /root/ws
colcon build --symlink-install
source install/setup.bash

# If venv dependencies need to be respected:
source venv/bin/activate
python3 $(which colcon) build --symlink-install --cmake-args -DPython3_EXECUTABLE=$(which python3)
source install/setup.bash
```

## Run

**Recommended — Web GUI (manages the full stack as child processes):**
```bash
ros2 run robo_reason_gui gui_node
# open http://localhost:8080
```

**LLM dry-run (no robot, no camera):**
```bash
# Terminal 1:
ros2 launch robo_reason_bringup dry_run.launch.py use_mock_llm:=true
# Terminal 2:
ros2 run robo_reason_task_interface task_interface_node
```

**VLM dry-run (mock PNG camera):**
```bash
ros2 launch robo_reason_bringup vlm_dry_run.launch.py \
  images_dir:=/path/to/mock_frames model_name:=groq/qwen3.6-27b
```

**API keys** — set `{PROVIDER}_API_KEY` env vars (e.g. `GROQ_API_KEY`, `NEBIUS_API_KEY`, `ANTHROPIC_API_KEY`) or add them to a `.env` file in the working directory.

## Architecture

### Node pipeline

```
task_interface_node  (or GUI)
        │  /plan_task (service)
        ▼
llm_planner_node | vlm_planner_node | vlm_llm_planner_node
        │  /execute_plan (service)
        ▼
plan_manager_node
        │  /execute_skill (action)
        ▼
fake_skill_executor_node | ur5_skill_executor_node
```

VLM and VLM_LLM planners also call `/camera/get_image` and `/camera/deproject` on `camera_services_node` (or `mock_camera_service_node`).

### Packages

| Package | Role |
|---|---|
| `robo_reason_interfaces` | All ROS2 msgs/srvs/actions (CMake): `PlanTask`, `ExecutePlan`, `ExecuteSkill`, `Deproject`, `GetImage`, `CancelExecution`, `PixelArray` |
| `robo_reason_bringup` | Centralized config (`config.py`) + launch files |
| `robo_reason_reasoning` | LLM/VLM clients + 7 reasoning methods; no ROS2 nodes |
| `robo_reason_planner` | Three planner nodes (LLM / VLM / VLM+LLM hybrid) |
| `robo_reason_manager` | Plan manager: validates plan, sequences `/execute_skill` calls, maintains `WorldState` |
| `robo_reason_executor` | Skill executor nodes (fake / real UR5cb) |
| `robo_reason_task_interface` | Terminal CLI entry point + `scene_mock.json` |
| `vlm_camera_service` | Camera bridge: RGB capture, pixel→3D deproject, ChArUco calibration |
| `robo_reason_gui` | FastAPI + rclpy bridge, supervises stack/UR-driver/camera as child processes |

### Configuration

All runtime settings live in `robo_reason_bringup/config.py` as a single `pydantic-settings` `Settings` class. Every field is overridable via a `ROBOREASON_` env var or `.env` file — no code changes needed for tuning (model, temperature, robot IP, timeouts, etc.).

### Reasoning layer (`robo_reason_reasoning`)

`EmbodiedAgent` is the entry point: it selects one of 7 reasoning method classes (`FHP`, `React`, `CoTSC`, `StepAction`, `SelfRefine`, `TreeOfThought`) based on `reasoning_mode`, and instantiates either `LLMClient` or `VLMClient`.

All clients use the `provider/model-name` format (e.g. `groq/qwen3.6-27b`, `nebius/nvidia-nemotron-120b`). The `ModelRegistry` in `base_client.py` maps short names to official API IDs; pass the full `provider/model-name` string directly if the model isn't registered there.

Supported providers: Groq, Nebius (OpenAI-compatible), OpenAI, Anthropic, Google Gemini — selected by the prefix.

### VLM pipeline detail

`vlm_planner_node` (and the hybrid `vlm_llm_planner_node`):
1. Captures an RGB frame via `/camera/get_image`.
2. Runs `EmbodiedAgent(client_type='vlm')` — VLM outputs pixel coordinates (either a `[x, y]` point or `[x_min, y_min, x_max, y_max]` bbox, controlled by `VLM_GROUNDING_MODE`).
3. Batch-deprojects all pixel fields to world `[x, y, z]` via `/camera/deproject`.
4. Applies depth-based grasp geometry compensation (mid-body pick using `PICK_GRASP_DEPTH_FRACTION`).

The hybrid mode (`VLM_LLM`) first runs a VLM scene-grounding call to build a `scene_mock.json`-shaped object list, then uses the standard LLM planner on that generated scene.

### GUI architecture

`server_node.py` is the entry point: it starts uvicorn + a `MultiThreadedExecutor` with `GuiBridgeNode`. It owns three supervisors:
- `StackSupervisor` — spawns/kills `ros2 launch gui_stack.launch.py` as a child process.
- `UrDriverSupervisor` — handles the flaky UR driver with auto-retry (up to `UR_DRIVER_MAX_ATTEMPTS`).
- `CameraServiceSupervisor` — manages the Orbbec camera script.

### ROS2 patterns

All service/action nodes use `ReentrantCallbackGroup` + `MultiThreadedExecutor` to avoid deadlock when service callbacks issue nested client calls.

### Debug artifacts

Every planning run writes a timestamped folder under `DEBUG_DIR` (`debug/` by default) with command, config, raw response, logs, and annotated debug images.

### Scene description (LLM mode)

`scene_mock.json` in `robo_reason_task_interface/config/` provides the workspace geometry and object positions in the robot base frame. Two entry kinds, deliberately shaped differently: `objects.*` (things to pick) carry `position` — the grasp contact point — plus `size: [w, d, h]`; `targets.*` (placement zones) carry an explicit axis-aligned box `bounds: {x, y, z}` of `[min, max]` pairs, where `bounds.z[1]` is the zone's top surface. See the file's own `schema_notes` field. The VLM/VLM_LLM modes only read `workspace.table.surface_z` from it (for grasp geometry); object positions come from the camera.
