# Session Context — RoboReason ROS2 VLM Pipeline

**Last updated:** 2026-07-22

---

## Project Overview

**RoboReason ROS2** is an LLM/VLM-based task planner for a UR5cb robot equipped with an OnRobot RG2 gripper. The system uses large language models and, optionally, vision-language models to interpret tasks, reason about the environment, and generate executable robot plans.

### ROS2 Package Structure

| Package | Role |
|---|---|
| `robo_reason_prompts` | Prompt templates for LLM/VLM reasoning |
| `robo_reason_reasoning` | FoundationClients (LLM/VLM wrappers), EmbodiedAgent |
| `robo_reason_planner` | High-level planner node, orchestrates LLM/VLM calls |
| `robo_reason_manager` | Plan manager, sequences skill execution |
| `robo_reason_executor` | Low-level skill executor (joint trajectories, gripper) |
| `robo_reason_task_interface` | Entry point for task requests from operators |
| `robo_reason_interfaces` | Custom ROS2 message and service definitions |
| `robo_reason_real` | Real-robot hardware interface (UR driver integration) |
| `robo_reason_bringup` | Launch files for bringing up the full stack |
| `vlm_camera_service` | Camera bridge: ChArUco calibration, image/depth services |
| `robo_reason_gui` | Web control panel — FastAPI + embedded `rclpy` bridge node (Session 3) |

### Service / Action Data Flow

```
task_interface
    → /plan_task (service)
        → /execute_plan (service)
            → /execute_skill (action, per skill)
                → joint trajectory commands (UR5cb)
                → gripper SetIO commands (OnRobot RG2)
```

VLM mode inserts a camera step inside `/plan_task`:
```
llm_planner_node
    → /camera/get_image  (GetImage service)
    → EmbodiedAgent(vlm) → pixel [w, h] coordinates
    → /camera/deproject  (Deproject service, batched)
    → 3D [x, y, z] in base_link
```

### Docker Container

- **Image tag:** `roboreason:ur`
- **Launch alias:** `roboreason-ur`
- Alias includes `--privileged -v /dev:/dev` for USB camera access.
- **Workspace mount:** `~/docker_ws/ros2_humble` on host → `/root/ws` inside container.
- **Setup guide for colleagues:** see `docs/docker-setup.md`.

---

## VLM Pipeline Architecture

### Mode Selection

**`mode='LLM'` (default)**
- Uses `EmbodiedAgent` with Groq LLM backend (or a mock).
- Scene information comes from `scene_mock.json` (static description).
- No camera hardware required.

**`mode='VLM'`**
- Camera node (Orbbec) publishes RGB and depth topics.
- The planner calls `/camera/get_image` to capture an RGB frame, saved to `/root/ws/src/vlm_frames/<task_id>/<index>_<stamp>.png`.
- The frame path is passed to `EmbodiedAgent(client_type='vlm')` which returns pixel coordinates `[w, h]` for objects of interest.
- The planner calls `/camera/deproject` in batch mode, converting pixel coordinates to 3D `[x, y, z]` in `base_link`.
- The resulting 3D plan is forwarded through the standard manager/executor pipeline.

In VLM mode `scene_mock.json` is **ignored for planning** (replaced by the camera image), but is still sent to `plan_manager_node` for workspace limit validation.

### Concurrency

`MultiThreadedExecutor` + `ReentrantCallbackGroup` are required in VLM mode to avoid deadlock when service calls are made from inside other service callbacks.

### VLM Image Storage

Images are saved to `/root/ws/src/vlm_frames/` (host: `~/docker_ws/ros2_humble/src/vlm_frames/`). Each task gets its own UUID subfolder. Browse directly from the host file explorer.

---

## ChArUco Calibration

### Board Parameters (current)

| Parameter | Value |
|---|---|
| Dictionary | `DICT_6X6_250` |
| Layout | 3×4 squares |
| `square_length` | 0.062 m |
| `marker_length` | 0.031 m |
| `axis_length` | 0.08 m |

These values are authoritative in `camera_services.launch.py`. `config.py` mirrors them so the pydantic-settings singleton is always consistent.

### Board Pose in base_link (current defaults)

| Parameter | Value |
|---|---|
| `board_in_base_x` | -0.224 m |
| `board_in_base_y` | -0.348 m |
| `board_in_base_z` | -0.030 m |
| `board_in_base_roll` | 3.14159 rad (180° around X) |
| `board_in_base_pitch` | 0.0 |
| `board_in_base_yaw` | 1.5708 rad (90° around Z) |

Intrinsic XYZ convention: roll first, then pitch, then yaw. Measured once per physical setup.

### Transform Chain

```
T_base_cam = T_base_board @ inv(T_cam_board)
```

- `T_cam_board`: live from ArUco pose estimation each frame.
- `T_base_board`: fixed, from `board_in_base_*` parameters.

When ChArUco is **visible**: Deproject returns coordinates in `base_link`.
When ChArUco is **not visible**: warning emitted, coordinates returned in camera frame.

### z_sign

`charuco_z_sign=1.0` (default) → right-handed triad, Z points toward camera.
`charuco_z_sign=-1.0` → left-handed triad (avoid).

### z_offset_m

A configurable Z offset (default `0.01` m = 1 cm) is added to all deprojected `base_link` points before returning them to the planner. This lifts object coordinates slightly above the table surface to avoid "inside table" positions.

---

## Key Bugs Fixed

### Session 1

#### 1. ChArUco detection improvements
- Replaced `estimatePoseCharucoBoard` with `solvePnP` (SOLVEPNP_ITERATIVE) — more stable.
- Switched to `cv2.aruco.ArucoDetector` new API with `CORNER_REFINE_NONE`.
- Manual axis drawing with `projectPoints` to support explicit `z_sign`.

#### 2. OpenCV 4.5.4 type compatibility
`cv2.line`, `cv2.circle`, `cv2.putText` reject `numpy.int32` scalars.
Fix: `np.round(...).astype(np.int32).tolist()` to get pure Python int coordinates.

#### 3. QoS mismatch
Camera driver publishes with `BEST_EFFORT` (sensor data profile). All three camera subscriptions in `camera_services_node` and `pixel_overlay_viewer_node` were changed from default `RELIABLE` to `qos_profile_sensor_data`.

#### 4. VLM pick/release state tracking
In VLM mode, object positions are from the camera (not hardcoded). `apply_skill_result('pick')` was only setting `robot.holding` when `find_object_near` found a match — which never happens in VLM mode. And the plan validator checked holding state before allowing release.

Fix: removed holding-state guardrails from `plan_validator` and `world_state` in VLM mode. Added `mode` parameter to `plan_manager_node`, threaded through to `PlanValidator.validate(mode=...)`. LLM mode retains full guardrails.

#### 5. Workspace limits not matching real table
Default `x: [0.15, 0.85]` excluded objects close to the robot (X ≈ 0). User must update `scene_mock.json` workspace limits to match the real table and camera coverage.

#### 6. Stale symlink build failures
```bash
rm -rf ~/ws/build/orbbec_camera_msgs/ament_cmake_python/orbbec_camera_msgs/orbbec_camera_msgs
rm -rf ~/ws/build/robo_reason_interfaces/ament_cmake_python/robo_reason_interfaces/robo_reason_interfaces
```
Always `deactivate` the Python venv before `colcon build`.

---

### Session 2 — Code Review Fixes

#### 1. `get_used_tokens` called a nonexistent method
`embodied_agent.get_used_tokens` called `get_total_used_tokens()` (does not exist on any client).
Fix: `get_total_usage().get('total_tokens', 0)` — uses the correct dict-returning API.

#### 2. `force_replan` kwarg silently ignored
`embodied_agent.step()` was passing `force_replanning=force_replanning` to all reasoning methods, but every method signature expects `force_replan=`. The misspelt kwarg was swallowed by `**kwargs` with no effect.
Fix: changed call site to `force_replan=force_replanning`.

#### 3. ReAct JSON key inconsistency
Both the prompt template and the parser used the spaced key `"end of simulation"`, creating silent failure risk on any future drift.
Fix: standardised to `"end_of_simulation"` in both `react_prompts.py` and `react.py`.

#### 4. ToT batch evaluation assert on LLM output
`TreeOfThought._evaluate_thoughts_batch` contained `assert len(evals) == len(plans)` on live LLM output — a guaranteed runtime crash on any model that returns a different count.
Fix: removed `_evaluate_thoughts_batch`, `use_iid_evaluation` flag, and the entire batch-eval branch. IID evaluation is now always used.

#### 5. Board geometry mismatch (`config.py` vs launch file)
`config.py` defaults were 5×7 / 0.03 m / 0.015 m; the authoritative launch file says 3×4 / 0.062 m / 0.031 m.
Fix: `config.py` corrected to match the launch file. Board-in-base pose defaults also synced (`Z=-0.030`, roll/yaw set). Trailing space in `board_in_base_roll` launch default removed.

---

## Deduplication & Refactors (Session 2)

### `_select_prompts()` base-class helper
Added to `ReasoningMethod` in `reasoning_method.py`:
```python
def _select_prompts(self, prompt_cls):
    """Return the VLM or LLM prompt tuple for the active client type."""
    getter = prompt_cls.get_vlm_prompts if self.use_vlm else prompt_cls.get_llm_prompts
    return getter()
```
Replaces the inline `get_vlm/get_llm` branch repeated across `react`, `tot`, `cot_sc`, `fhp_ffhp`, `always_act`, `self_refine` (11 call sites).

### `agent_runner.py` — shared planning loop
Created `src/robo_reason_planner/robo_reason_planner/agent_runner.py` with `run_plan_loop(agent, observation, max_steps=25)`.
Both `llm_planner_node` and `vlm_planner_node` now import and use it, removing ~30 lines of duplicated step-loop code.

### `_wait_for_future()` helper (`vlm_planner_node`)
Replaces two bare `while not future.done(): time.sleep(0.05)` busy-waits with a helper that enforces a 10-second timeout and raises a descriptive `RuntimeError` on expiry.

### Nebius → OpenAI routing
`_call_nebius` in `LLMClient` and `VLMClient` was byte-identical to `_call_openai`.
Fix: removed both `_call_nebius` methods; `__call__` now routes `provider in ("openai", "nebius")` through `_call_openai`. A comment explains that Nebius is OpenAI-API-compatible and only differs in `base_url` (set at init).

---

## Dead Code Removed (Session 2)

| Location | Removed |
|---|---|
| `extraction_classes.py` | `GoalReach`, `Predicate`, `Predicates`, `Effect`, `Effects`, `UR5Actions` — none imported anywhere |
| `reasoning_method.py` | `LLMReasoningMethod` (empty pass-through), `ReasoningMethodTester` (offline helper, unused in package) |
| `llm_client.py` | Broken `__main__` block (imported nonexistent `src.test.test_client`) |
| `mock_camera_service_node.py` | Unused imports: `glob as _glob`, `numpy as np`, `Time`, `Image` |

---

## Log Prefix Standardisation (Session 2)

All `self.get_logger()` calls now use `[{self.__class__.__name__}]` as the prefix, consistent with the ROS2 convention of prefixing with the node's class name. Files updated:

| File | Old prefix → New prefix |
|---|---|
| `ur5_skill_executor_node.py` | `[UR5Executor]` → `[UR5SkillExecutorNode]` (~23 sites) |
| `plan_manager_node.py` | `[PlanManager]` → `[PlanManagerNode]` |
| `fake_skill_executor_node.py` | `[FakeExecutor]` → `[FakeSkillExecutorNode]` |
| `task_interface_node.py` | `[TaskInterface]` → `[TaskInterfaceNode]` |
| `camera_services_node.py` | `[Calib]` / `[CalibUpdate]` → `[CameraServicesNode]` (4 sites) |

---

## File Changes Summary

### Session 1

| File | Change |
|---|---|
| `vlm_camera_service/charuco_utils.py` | solvePnP, ArucoDetector new API, CORNER_REFINE_NONE, manual axis drawing, z_sign, OpenCV 4.5.4 int fix, board outline |
| `vlm_camera_service/camera_services_node.py` | QoS fix, charuco_z_sign param, z_offset_m param, board origin + camera position logging |
| `vlm_camera_service/pixel_overlay_viewer_node.py` | QoS fix, charuco_z_sign param, tvec overlay on image |
| `vlm_camera_service/launch/camera_services.launch.py` | charuco_z_sign, z_offset_m, updated board defaults |
| `robo_reason_manager/plan_manager_node.py` | mode parameter, passed to PlanValidator |
| `robo_reason_manager/plan_validator.py` | holding checks gated on `mode != VLM` |
| `robo_reason_manager/world_state.py` | simplified pick/release to not require object matching |
| `robo_reason_bringup/launch/real_robot.launch.py` | mode param wired to plan_manager_node, tmp_dir changed to /root/ws/src/vlm_frames |
| `robo_reason_task_interface/config/scene_mock.json` | workspace limits to be updated by user |

### Session 2

| File | Change |
|---|---|
| `robo_reason_reasoning/embodied_agent.py` | Fix `force_replan=`, fix `get_total_usage()`, remove `use_iid_evaluation=True` |
| `robo_reason_reasoning/react.py` | `end_of_simulation` key fix, `_select_prompts` |
| `robo_reason_reasoning/EmbodiedAgentsPrompts/react_prompts.py` | `end_of_simulation` key standardised (2 sites) |
| `robo_reason_reasoning/tot.py` | Remove `_evaluate_thoughts_batch`, `use_iid_evaluation`, simplify eval block, `_select_prompts` (2 sites) |
| `robo_reason_reasoning/reasoning_method.py` | Add `_select_prompts` helper, remove `LLMReasoningMethod` + `ReasoningMethodTester` |
| `robo_reason_reasoning/fhp_ffhp.py` | `_select_prompts` |
| `robo_reason_reasoning/always_act.py` | `_select_prompts` |
| `robo_reason_reasoning/cot_sc.py` | `_select_prompts` |
| `robo_reason_reasoning/self_refine.py` | `_select_prompts` |
| `robo_reason_reasoning/extraction_classes.py` | Remove 5 dead Pydantic models + `UR5Actions` |
| `robo_reason_reasoning/FoundationClients/src/llm_client.py` | Remove `_call_nebius`, route nebius→openai, remove broken `__main__` |
| `robo_reason_reasoning/FoundationClients/src/vlm_client.py` | Remove `_call_nebius`, route nebius→openai |
| `robo_reason_planner/agent_runner.py` | **NEW** — shared `run_plan_loop()` |
| `robo_reason_planner/llm_planner_node.py` | Use `run_plan_loop`, fix double-traceback |
| `robo_reason_planner/vlm_planner_node.py` | Use `run_plan_loop`, fix double-traceback, add `_wait_for_future` |
| `robo_reason_bringup/config.py` | Sync ChArUco defaults to launch file (3×4, 0.062/0.031), set board-in-base pose |
| `vlm_camera_service/launch/camera_services.launch.py` | Remove trailing space from `board_in_base_roll` default |
| `vlm_camera_service/mock_camera_service_node.py` | Remove unused imports (glob, numpy, Time, Image) |
| `robo_reason_executor/ur5_skill_executor_node.py` | Log prefix standardisation (~23 sites) |
| `robo_reason_executor/fake_skill_executor_node.py` | Log prefix standardisation |
| `robo_reason_manager/plan_manager_node.py` | Log prefix standardisation |
| `robo_reason_task_interface/task_interface_node.py` | Log prefix standardisation |
| `vlm_camera_service/camera_services_node.py` | Log prefix standardisation (4 sites) |
| `docs/docker-setup.md` | **NEW** — decompress, load, and run guide for colleagues |

---

## Launch Reference

### Web GUI (recommended, Session 3)
```bash
ros2 run robo_reason_gui gui_node
# open http://localhost:8080
```

### Camera Driver (Orbbec)
```bash
./scripts/run_orbbec_registered.sh
```

### Camera Services + Overlay
```bash
ros2 launch vlm_camera_service camera_services.launch.py \
  show_overlay:=true \
  charuco_z_sign:=1.0 \
  board_in_base_x:=-0.224 \
  board_in_base_y:=-0.348 \
  board_in_base_roll:=3.14159 \
  board_in_base_yaw:=1.5708 \
  z_offset_m:=0.01
```

### LLM Mode (Real Robot)
```bash
export GROQ_API_KEY=gsk_...
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=LLM \
  reasoning_method:=fhp \
  model_name:=groq/qwen3-32b \
  temperature:=0.1
```

### VLM Mode (Real Robot)
```bash
export GROQ_API_KEY=gsk_...
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=VLM \
  model_name:=groq/qwen3.6-27b \
  temperature:=0.1
```

### Task Interface CLI
```bash
ros2 run robo_reason_task_interface task_interface_node
```

---

### Session 3 — Web GUI, Emergency Stop, Debug Recorder, Model Registry Refresh

Everything below postdates Session 2 and is on branch `feature/gui`.

#### 1. `robo_reason_gui` package (new)

A full web control panel, built across 6 phases (commits `f74b35b` scaffold →
`a70e616` Phase 6 polish) plus later hardening work:

- **`server_node.py`** — entry point. `rclpy.init()`, spins `GuiBridgeNode` on
  a `MultiThreadedExecutor` in a background thread, wires
  `StackSupervisor`/`UrDriverSupervisor`/`CameraServiceSupervisor` together,
  and runs `uvicorn` on `0.0.0.0:8080` (container uses `--network host`).
- **`bridge_node.py`** (`GuiBridgeNode`) — the ROS bridge: connectivity probes
  (trajectory action server, `/joint_states` freshness, gripper I/O), `/plan_task`
  + `/execute_plan` + `/cancel_execution` service clients, `/execution_log`
  fan-out to WebSocket subscribers via `call_soon_threadsafe`, live planner
  retuning through `SetParameters`, camera frame grab + JPEG encode with pixel/
  ChArUco-axis overlay drawing (Pillow, no OpenCV/numpy dependency in this
  path), and a `/api/preflight`-backing check for duplicate `/execute_skill`
  action servers (a leftover executor would double-run every skill).
- **`stack_supervisor.py`** (`StackSupervisor`) — launches
  `gui_stack.launch.py` as a child process group; reaps stale processes by
  name before every start, and refuses to launch while the graph still shows
  an `/execute_skill` server from a previous session (hard guard against
  double-executors, not just a name-based sweep).
- **`ur_driver_supervisor.py`** (`UrDriverSupervisor`) — the stock
  `ur_control.launch.py` driver fails intermittently on startup; this
  supervisor retries with backoff (up to `MAX_ATTEMPTS=10`) until the bridge's
  `robot_ready()` probe passes, and auto-reconnects on unexpected process
  exit. Tracks the teach-pendant reverse-interface connection separately from
  controller readiness so the header LED can stay amber even once controllers
  are up but the pendant hasn't connected.
- **`camera_service_supervisor.py`** (`CameraServiceSupervisor`) — same
  subprocess-supervision pattern as the UR driver, wrapping
  `scripts/run_orbbec_registered.sh`; readiness is the bridge's
  `camera_available()` probe (`/camera/get_image` reachable).
- **`options.py`** — builds the GUI's dropdown payload from
  `ModelRegistry` (see #4 below); `VLM_ONLY_MODELS` restricts the model
  dropdown to vision-capable models when `mode=VLM`.
- Frontend: plain `index.html` + `app.js` + `style.css`, no build step. Static
  files are served through a custom `/static/{path:path}` route rather than
  Starlette's `StaticFiles`, because `--symlink-install` produces symlinks
  that `StaticFiles` refuses to follow outside the served directory.

See [`src/robo_reason_gui/README.md`](../src/robo_reason_gui/README.md) and
[`docs/ROBOREASON_GUIDE.md`](ROBOREASON_GUIDE.md)'s new "Web GUI" section for
full usage and the HTTP API table.

#### 2. Emergency stop (`CancelExecution.srv`, new)

Request: "stop the robot, abort the plan, and return home, ready for the next
command." Implemented as a new `/cancel_execution` service on
`plan_manager_node`:

- `robo_reason_interfaces/srv/CancelExecution.srv` — empty request, `bool
  success` + `string message` response. Registered in `CMakeLists.txt` and
  rebuilt in the running container (`colcon build --packages-select
  robo_reason_interfaces`).
- `plan_manager_node.py` — `_execute_plan_callback`'s step loop now checks a
  `threading.Event` (`_stop_requested`) before every step and aborts cleanly if
  set. The active `/execute_skill` goal handle is tracked
  (`_active_goal_handle`, under a lock) so the new
  `_cancel_execution_callback` can call `.cancel_goal_async()` on it, then
  send a `move_home` goal synchronously before clearing the stop flag.
- `bridge_node.py` — new `_cancel_client` + `cancel_execution()`, deliberately
  **not** gated behind `_command_lock` so it's callable while
  `execute_command()` is blocked waiting on `/execute_plan`.
- `app.py` — `POST /api/execute/cancel`.
- Frontend — a red **Stop** button next to Send, enabled only while a plan is
  executing.

#### 3. Fence-stripping + `force_json` fixes (reasoning methods)

All 6 reasoning-method files (`always_act.py`, `cot_sc.py`, `fhp_ffhp.py`,
`react.py`, `self_refine.py`, `tot.py`) plus the `ReasoningMethod` base class
gained a shared `_strip_json_fence()` classmethod that strips
markdown-code-fence wrapping (` ```json ... ``` `) before `json.loads()`, and a
fix for a `force_json`/`force_json_response` kwarg-name mismatch that had been
silently disabling JSON enforcement on some call sites.

#### 4. Model registry refresh (`FoundationClients/src/base_client.py`)

- **Groq** — old lineup entirely removed: `llama4-scout-17b`,
  `llama4-maverick-17b`, `llama3.3-70b`, `llama3.1-8b`,
  `moonshotai-kimik2-32b`. New set: `openai-oss-20b`, `openai-oss-120b`,
  `qwen3-32b`, `qwen3.6-27b` (vision-enabled, replaces `llama4-scout-17b` as
  the GUI's default VLM-capable Groq model in `options.py` and
  `stack_supervisor.py`).
- **Nebius** — added `nvidia-cosmos3-33b`, `qwen3-embedding-8b`; renamed
  `kimi-k2` → `kimi-k2.6`.
- **Fixed:** several launch files hardcoded `model_name`'s default to
  `groq/llama4-scout-17b`, which no longer resolves in
  `ModelRegistry.GROQ_MODELS`. Updated defaults: `dry_run.launch.py` and
  `dry_run_services.launch.py`'s docstring example → `groq/qwen3-32b`;
  `vlm_dry_run.launch.py`, `real_robot.launch.py`, `gui_stack.launch.py` →
  `groq/qwen3.6-27b` (vision-enabled, works for both LLM and VLM mode so a
  bare `mode:=VLM` override without a `model_name:=` override still works).

#### 5. Debug recorder + `DEBUG_TIMEZONE`

`robo_reason_planner/debug_recorder.py`'s `DebugRun` captures per-`/plan_task`
artifacts (`command.txt`, `config.json`, `response.json`, `error.txt`,
`logs.txt`, camera frame + overlay in VLM mode) into `debug/<run_id>/`, plus a
root `debug/summary.csv` row per run. `response.json` is empty specifically
when `response=None` is passed on any exception — not necessarily an empty raw
LLM/VLM completion. New `Settings.DEBUG_TIMEZONE` (default `Europe/Berlin`)
makes run-folder timestamps match the operator's local time instead of the
container's UTC clock (`zoneinfo.ZoneInfo`, wired via a `_now()` helper).

#### 6. New failure modes surfaced by the debug recorder (2026-07-01 debug review)

Two distinct failure categories, both hypothesised to trace back to
prompt/schema complexity but manifesting differently per provider:

- **Groq empty completions on `fhp`/`ffhp`** — `json.loads()` raises on an
  empty string from `plan_task()`'s second (chained) LLM call. Groq's
  `qwen3.6-27b` likely exhausts its reasoning/thinking-token budget on the
  longer, chained predicates→plan prompt; `cot_sc`'s shorter single-shot
  prompt doesn't show this. Groq's `response_format` is only set when both
  `force_json` and `forced_json_schema` are passed, and `fhp_ffhp.py` never
  passes a schema — see `vlm_client.py`'s `_call_groq`.
- **Nebius bad-grounding-depth on `fhp`/`ffhp`/`always_act`** — the plan
  parses fine (not a JSON error), but the returned pixel coordinates don't
  land on the target's actual surface, so `/camera/deproject` gets no valid
  depth. Reproduced across different reasoning methods on Nebius, correlating
  with the same schema-complexity hypothesis but as a grounding-accuracy
  failure rather than a parse failure.
- A reduced-schema / split-grounding-from-planning reasoning method was
  discussed as a mitigation (ground objects in one simpler call, plan in a
  second) but **has not been implemented** — offered as an additive method
  (not replacing any of the existing 6) pending confirmation.

---

## Known Open Issues

- VLM may appear to use a stale image if the model hallucinates object positions. Verify by checking the saved image in `/root/ws/src/vlm_frames/<latest>/` — if the image is correct but coordinates are wrong, it is a model reasoning issue.
- Workspace limits in `scene_mock.json` must be manually tuned to match the real table. The validator uses these even in VLM mode.
- `charuco_z_sign` default was -1.0 (left-handed); corrected to 1.0.
- **Groq empty-completion failures** on `fhp`/`ffhp` (`qwen3.6-27b`) and
  **Nebius bad-grounding-depth failures** on `fhp`/`ffhp`/`always_act` — see
  Session 3 §6 above. A reduced-schema mitigation was discussed but not yet
  implemented.
- Working tree on `feature/gui` currently has 19 modified files + 1 new file
  (`CancelExecution.srv`) uncommitted, covering all of Session 3's changes —
  awaiting the operator's own commit.

---

### Session 4 — VLM→LLM Hybrid, Grasp-Width TCP Offset, Grounding Fixes, Bbox Grounding (in progress)

Commits `799dd2b` → `8389578` on `main`; current work is on branch
`feature/vlm-bbox-grounding` (uncommitted).

#### 1. VLM→LLM hybrid pipeline (`799dd2b`)

New `mode=VLM_LLM`: a VLM grounding call detects objects/targets as pixel
centers via a new scene-grounding prompt (`scene_description_prompts.py`),
deprojects them to world `[x, y, z]`, assembles a **generated** scene JSON
(`scene_mock.json` itself is never touched), then hands off to the standard
LLM planning pipeline. New `vlm_llm_planner_node.py` + `scene_grounder.py` +
`vlm_llm_dry_run.launch.py`. Grounding model/temperature and planning
model/temperature are wired independently through `config.py`,
`real_robot.launch.py`, and the GUI (`options.py`, `bridge_node.py`,
`stack_supervisor.py`, `app.js`/`index.html`).

Also fixed a project-wide **VLM pixel-coordinate convention bug**:
Qwen-family VLMs ignore the requested `[h, w]` (row, col) order and always
emit their native `[x, y]` (col, row) convention, causing a systematic
axis-swap between the detected scene and the real one. Standardized every
reasoning-method prompt (fhp/ffhp, tot, self_refine, react, always_act,
cot_sc), `skills.py`, `extraction_classes.py`, and both
`vlm_planner_node.py`/`vlm_llm_planner_node.py`'s deproject/debug-draw code
to consistently use `[x, y]`.

#### 2. Width-aware TCP offset + release-height fix (`6acc31e`)

- Added `grasp_width` to the `UR5Action` schema, `skills.py` docs, and every
  reasoning-method prompt so every planning mode reports/copies the object's
  estimated width into pick actions.
- Added a `TCP_OFFSET_Z_CALIBRATION` table + `tcp_offset_z_for_width()`
  piecewise-linear interpolation (moved to `grasp_geometry.py` in `49eada6`,
  see below). `ur5_skill_executor_node` now sets `self._robot_model.tool`
  per-pick from the interpolated finger-aperture offset and resets to the
  default after release — the RG2 fingers pivot, so flange-to-contact
  distance varies with object width.
- Capped pick descent depth at `(TCP_OFFSET_Z - TCP_CLAMP_CLEARANCE_M)` in
  `vlm_planner_node.py`/`vlm_llm_planner_node.py` so tall objects are gripped
  nearer their top instead of crashing the rigid TCP clamp into them.
- Fixed **double-counted release height** in the VLM_LLM hybrid pipeline:
  target `position.z` was already the real depth-measured top surface, but
  the LLM's stacking formula (`position.z + size[2]`) then added the VLM's
  blind, non-depth-grounded `size[2]` guess on top of it. Now derives target
  height from depth instead (`surface_z - top_z`) and resets `position.z`
  back to a base reference so `position.z + size[2]` reconstructs the true
  top surface instead of double-counting it. See
  [`docs/GRASP_GEOMETRY_PIPELINE.md`](GRASP_GEOMETRY_PIPELINE.md) for the
  full geometry writeup.

#### 3. Local-plane centroid correction, terminal-log capture, driver fast-fail (`49eada6`, merged via `59de4fe`)

- `camera_services_node.py`: added local-plane-centroid VLM-click correction
  to reduce grounding error near object edges.
- Fixed a `table_surface_z`/`top_z` sign inversion in object-height
  computation (`vlm_planner_node.py`, `vlm_llm_planner_node.py`).
- Moved `TCP_OFFSET_Z_CALIBRATION`/`tcp_offset_z_for_width()` out of
  `config.py` into a new `grasp_geometry.py` module.
- New `/gui/get_terminal_logs` service so each planner's per-run debug
  folder captures the camera/robot/stack subprocess logs for that run
  (`debug_recorder.py`, `bridge_node.py`, `server_node.py`); wired into all
  three planner nodes (`llm_planner_node.py` upgraded to
  `MultiThreadedExecutor`/`ReentrantCallbackGroup` to support it safely).
- `ur_driver_supervisor` now aborts and retries immediately on an `[ERROR]`
  log marker instead of always waiting the full 30s readiness timeout.

#### 4. Settings consolidation (`8389578`)

Moved scattered per-class setting variables (GUI supervisors, server node)
into `config.py` as the single `pydantic-settings` source of truth.

#### 5. Bbox grounding mode (uncommitted, `feature/vlm-bbox-grounding`)

Added a `grounding_mode` toggle (`point` vs `bbox`) for VLM pixel grounding:
- All 6 reasoning-method VLM prompt variants gained a bbox-output template
  (`[x_min, y_min, x_max, y_max]` instead of a single `[x, y]` click).
- `vlm_planner_node.py`: new `grounding_mode` ROS param threaded into
  `EmbodiedAgent`; `_save_debug_frame` draws bbox rectangles; `_deproject_plan`
  handles bbox-center deproject + derives `grasp_width` from box width.
- Full GUI wiring: `app.py` (`StackRequest.grounding_mode`),
  `stack_supervisor.py` (launch arg forwarding), `index.html`/`app.js`
  (pixel-grounding dropdown, shown only in VLM/VLM_LLM mode).

#### 6. CoT-SC timeout root-cause fix (uncommitted, same branch)

Investigated an intermittent `cot_sc` timeout (plan generated in the debug
folder but never executed). Root cause: (a) no provider client in
`FoundationClients` had ever had an explicit HTTP request timeout configured
(SDK defaults up to 600s, exceeding the app's 300s `PLAN_TIMEOUT_S`), and (b)
`cot_sc.py` ran its `k=5` independent plan samples **sequentially**, giving
it 5x the exposure to any single slow/stuck call vs. single-call methods
like `fhp`. Fixes: added a bounded `timeout` (default 60s) to every provider
client constructor in `base_client.py`; parallelized `CoT-SC`'s `k` samples
via `ThreadPoolExecutor`; added a `threading.Lock` around
`BaseFoundationClient._update_metrics()` since it's now hit concurrently by
`k` threads. Follow-up (2026-07-07): the bounded timeout was initially
hardcoded to 60s inside `base_client.py`; moved to a proper setting,
`Settings.REQUEST_TIMEOUT_S` in `config.py` (default `60.0`, tunable via
`ROBOREASON_REQUEST_TIMEOUT_S` env var / `.env`, same mechanism as
`PLAN_TIMEOUT_S`). `base_client.py` imports `robo_reason_bringup.config` for
this default (falling back to a hardcoded `60.0` only if that package isn't
importable, so the standalone `FoundationClients` example scripts still run
outside the ROS workspace); an explicit `timeout=` in `model_parameters`
still overrides it per-call. The user has since raised it to `120.0` for
`nebius/nvidia-nemotron-120b`'s legitimately longer per-call latency.

#### 7. Release-height fix — mid-body grasp / release-lift mismatch (uncommitted, same branch, 2026-07-06)

Root cause of the reported "robot releases objects ~3cm (or more) too high"
symptom, found by inspecting the pick/release geometry (see
`docs/GRASP_GEOMETRY_PIPELINE.md`). **Not** a table-calibration issue —
`board_in_base_z` and `scene_mock.json`'s `surface_z` already agreed
(`-0.03 m`). The actual bug:

- On `pick`, the gripper descends **mid-body** — `descent = min(0.5 × height,
  max_descent)` — not to the object's top surface.
- But the paired `release` step's `object_height` (the amount the executor
  lifts the TCP by, in `ur5_skill_executor_node.py`) was set to the **full**
  object height, implicitly assuming the object was grasped right at its top.
- Net effect: release height overshoots by `descent` (≈ half the object's
  height for typical uncapped objects — a 6cm object → ~3cm error, matching
  the report; taller/clamp-capped objects get worse).

Fix: both `vlm_planner_node.py` (`_deproject_plan`) and
`vlm_llm_planner_node.py` (`_build_generated_scene`) now carry
`height - descent` (the true distance from grasp contact point to object
bottom) as the release lift amount, instead of the full `height`. Target
stacking math (`position.z + size[2]`) and pick contact-height/grasp-width
logic are unchanged — this only touches the held-object release-lift value.
Not yet tested on hardware.

#### 8. Reasoning-method robustness fixes + `VLM_REASONING_EFFORT` (uncommitted, same branch, 2026-07-08)

Triaged three issues reported from live hardware testing:

- **cot_sc `TypeError` crash**: a sampled plan action came back as a
  non-dict (e.g. `[3]`) and `UR5Action(**action_dict)` blew up with an
  unhandled `TypeError` instead of a catchable planning error.
  `reasoning_method.py`'s `_build_action()` now guards with
  `isinstance(action_dict, dict)` and raises `ActionParsingError` (with the
  offending value in the message) for both the non-dict case and
  `pydantic.ValidationError`. Regression test:
  `test_cot_sc_crashes_on_non_dict_action_in_plan`.
- **Unclosed `<think>` block → `ResponseParsingError`, no retry**: when a
  reasoning VLM/LLM exhausts `max_tokens` mid-`<think>` (never emits
  `</think>` or any JSON), the old `_THINK_BLOCK_RE` (which only matches
  *closed* blocks) left the raw dump untouched, so `_is_blank_response()`
  saw it as non-blank and the retry-once-with-higher-budget path never
  fired. Fixed by adding `_UNCLOSED_THINK_BLOCK_RE = re.compile(r'<think>.*',
  re.DOTALL)`, applied in `_strip_think_blocks()` right after the closed-block
  regex, so a trailing unclosed `<think>` is truncated to empty too and
  `_is_blank_response()` now correctly triggers the existing retry-once path
  with no changes needed to `_call_client()`'s retry trigger itself. Tests:
  `test_is_blank_response_covers_unclosed_think_block`,
  `test_call_client_retries_once_on_unclosed_think_block`.
- **Groq vs. Nebius VLM grounding precision**: confirmed identical raw image
  bytes/prompt text sent to both providers — the precision gap is model
  capability, not an input bug. `groq/qwen3.6-27b` visibly does imprecise
  percentage-estimation arithmetic inside its `<think>` block instead of
  grounding directly, unlike Nebius's purpose-built Qwen2.5-VL-72B-Instruct.
  Mitigation: added `Settings.VLM_REASONING_EFFORT` (default `''` = omitted,
  no behavior change) forwarding Groq's `reasoning_effort` request param
  (e.g. `'none'` disables the `<think>` CoT entirely) end-to-end —
  `config.py` → `reasoning_effort` ROS param on `vlm_planner_node.py` /
  `vlm_llm_planner_node.py` → conditionally added to `client_parameters` /
  `grounding_client_parameters` (only when non-empty) →
  `gui_stack.launch.py` launch arg → `stack_supervisor.py`'s
  `_launch_command()` → GUI `ConfigRequest`/`StackRequest` (`app.py`) →
  `bridge_node.py`'s `set_planner_config()` (VLM + VLM_LLM modes only) →
  new "Reasoning effort (VLM, Groq/Qwen3)" dropdown in `index.html`/`app.js`
  (Default/None/Low/Medium/High, visible only in VLM/VLM_LLM mode). Not yet
  A/B tested on hardware against the current Groq grounding behavior.

Full reasoning test suite: 45/45 passing after these changes.

---

### Session 5 — Release-Geometry Determinism, Zone-Collision Spacing, and the LLM-vs-VLM Benchmark

Branch `feature/refactoring`. Commit `a1b744a` (zone-collision spacing) is
landed on `main`-tracking history; everything else below is uncommitted
working-tree state as of this session (see Known Open Issues).

#### 1. Zone-release collision spacing (`a1b744a`, committed)

New `distribute_zone_releases()` in
`robo_reason_manager/robo_reason_manager/schemas.py`, called from
`plan_manager_node.py` right after `normalize_plan` and before
`PlanValidator`. Problem: asking for multiple objects into the same
zone/tray (or a "line" of objects) reliably produced two releases at (or
very near) the same point — LLM and VLM plans alike always resolve a
zone release to that zone's exact center.

- **Detection is mode-agnostic by design**: it compares release positions
  *to each other within the plan* (a fixed collision radius,
  `Settings.ZONE_PLACEMENT_COLLISION_RADIUS_M`), not against a scene's
  static `targets` registry — an earlier registry-based version only ever
  matched LLM's hand-authored positions, since VLM/VLM_LLM releases are
  independent depth deprojections that essentially never echo a registered
  target's coordinates exactly.
- **Same-z stack guard**: two releases at the same (x, y) but with
  meaningfully different z (`_STACK_Z_GAP_M`) are left alone — that's a
  deliberate stack (e.g. "stack the red cube on the blue cube"), not an
  accidental collision.
- Spacing pattern: a line along +y first (`ZONE_PLACEMENT_ITEMS_PER_ROW`
  items), wrapping into a grid along +x once a row is full — grows
  monotonically away from the untouched first occupant (never lands back
  on offset (0,0), unlike an earlier centered-row layout which put its
  middle slot exactly there for odd row widths). Unbounded rather than
  clamped to a zone's registered size, since size isn't reliably known in
  every mode (see `docs/TODO.md` #1, still open).
- The `approach` step immediately preceding a spaced-out release is nudged
  by the same (dx, dy) — otherwise the arm still flies to the original,
  now-occupied spot before sidestepping only at the release itself.
- Tests: `src/robo_reason_manager/test/test_schemas.py` (8 cases).

#### 2. LLM-mode release geometry made deterministic (uncommitted)

Two hardware-reported bugs traced back to the same root cause: every
LLM-mode prompt tells the model to compute release height/position via
its own arithmetic (`object_height = size[2]`, `release_position.z =
target.position.z + target.size[2]` or `= table.surface_z`), and that
arithmetic was repeatedly observed wrong on real hardware — consistent
with this session's broader finding (§5 below) that LLM arithmetic is the
weak link, not perception. Fixed by computing both deterministically in
code instead, in `llm_planner_node.py`, mirroring the pattern
`vlm_planner_node.py` already used for VLM mode:

- **`_fix_object_height(plan_steps, scene_json)`**: overwrites every
  release's `object_height` with `(paired pick's grasp z) −
  table_surface_z` — the true vertical drop from the actual grasp contact
  point to the table, regardless of whether the scene author put
  `position.z` near an object's top, middle, or base. Root cause of a
  reported "released ~3cm above where it should be" symptom: the prompt's
  `object_height = size[2]` convention silently assumes the grasp point is
  at the object's top; when the operator re-tuned `position.z` lower
  (to fix a separate pick-contact issue), release overshoot appeared as a
  side effect.
- **`_fix_release_height(plan_steps, scene_json)`**: overwrites every
  release's target x/y/z. Matches release coordinates against the scene by
  **footprint containment** (`position ± size/2`, not point-distance) —
  a target zone like a tray is tens of cm wide, so a release can land well
  inside it while being far from its center; point-distance matching
  missed this on hardware (a "line on the table" release measurably inside
  the tray's real footprint, 10cm+ from its center). When multiple zones'
  footprints contain a point (the generic `table` zone almost always does,
  alongside anything smaller sitting on it), the smallest-footprint match
  wins. A position that's an **exact echo** of a target's/object's own
  registered coordinates is read as intentional (stacking or a deliberate
  zone placement) and left alone; anything that merely *drifted* into a
  zone's footprint without echoing it is nudged back outside (along
  whichever axis needs the smaller move), but only when that zone's
  surface sits meaningfully above the bare table
  (`_NUDGE_HEIGHT_THRESHOLD_M` — otherwise nudging "away" from the
  generic, near-table-height `table` zone entry would just push objects
  off the table trying to escape it). Every object already picked up
  earlier in the *same* plan is excluded from matching (it's held, not
  resting at its original scene position anymore) — first version of this
  only excluded the currently-held object and produced false "stacking on
  a stale position" matches for the 3rd/4th object in a multi-pick plan.
- Both were iterated against real captured `debug/<run_id>/` data from
  failing hardware runs (not just synthetic tests) until the corrected
  numbers matched hand-derived expected values — see conversation history
  for the specific run IDs and worked arithmetic.

#### 3. VLM-mode grasp-width sampling fix (uncommitted)

`vlm_planner_node.py`'s `_deproject_plan` derives `grasp_width` for a pick
by deprojecting a bbox's left/right edge pixels and measuring real-world
distance — but those edge pixels sit exactly on the object's silhouette
boundary, the single worst place to get valid depth (occlusion/parallax),
and a single failed edge pixel aborted the *entire* batched Deproject call,
crashing the whole plan even though the actual pick/release points were
fine. Fixed by splitting it into its own best-effort call
(`_apply_grasp_width`, wrapped in try/except, never raises) sampling
inset from the raw edge by `Settings.GRASP_WIDTH_EDGE_INSET_FRAC` (0.15)
instead of the boundary itself — landing on the object's surface instead
of its silhouette edge — with any failure just leaving the VLM's own
width guess in place instead of aborting.

#### 4. Benchmark infrastructure (new, uncommitted — `benchmark/`)

Built to run the LLM-vs-VLM comparison adapted from Favali, Sabattini,
Villani (RO-MAN 2025) — task-length / goal-specificity / affordance-
perception axes, TS/TSR/AETS metrics (Eq. 14-16) — scoped to what's
runnable in ~3 days on this hardware with one LLM model, one VLM model,
no dynamic-event injection:

- **`benchmark/PLAN.md`** — the full plan: 6 conditions (Pick&Place,
  Sort/Stack, Arithmetic — each easy/hard) × 10 reps × 2 models = 120
  trials; exact prompts (adapted to the real scene's 4 colored cubes);
  a coarse-then-refine collection schedule.
- **Semi-automatic logging** (TS/safety and TSR/correctness genuinely need
  a human observer on real hardware — no collision sensor or vision-based
  final-state check exists in this codebase):
  - `bridge_node.py::_record_execution_outcome` — every `/execute_plan`
    call now writes `debug/<run_id>/execution_result.json` +
    `debug/benchmark_summary.csv` (steps executed, success/error),
    tagged `is_benchmark` from a new GUI "Benchmark trial" checkbox
    (`index.html`/`app.js`, plumbed through `CommandRequest`/
    `ExecuteRequest` in `app.py`) so casual testing doesn't pollute the
    dataset.
  - `bridge_node.py::record_benchmark_annotation` + `GET
    /api/benchmark/tasks` / `POST /api/benchmark/annotate` — an inline
    form in the GUI, shown after a flagged execution finishes, asking
    exactly two questions (safe? how many sub-tasks completed?) and
    computing TS/TSR/AETS server-side into `benchmark/results.csv`.
  - `benchmark/benchmark_annotate.py` — the same thing as a standalone
    CLI script (offline/after-the-fact annotation); auto-selects the
    latest *flagged* run, skipping any unflagged casual runs in between.
    Its `TASKS` table must stay in sync with `bridge_node.py`'s
    `BENCHMARK_TASKS` (duplicated deliberately — no shared import path
    between this plain script and the ROS package).
  - New settings: `ZONE_PLACEMENT_COLLISION_RADIUS_M`,
    `ZONE_PLACEMENT_DEFAULT_SPACING_M`, `ZONE_PLACEMENT_MARGIN_M`,
    `ZONE_PLACEMENT_ITEMS_PER_ROW` (§1), `GRASP_WIDTH_EDGE_INSET_FRAC`
    (§3) — all in `robo_reason_bringup/config.py`.
- **`benchmark/plot_results.py`** — reads `results.csv`, prints aggregate
  tables, and writes 8 report-ready PNGs to `benchmark/figures/`
  (headline summary, by-task TS/TSR, AETS, easy-vs-hard, plan length,
  issue rate, a note-derived failure-mode breakdown, plus `*_pp_sort`
  variants scoped to Pick&Place + Sort/Stack only for general-audience
  presentation use). Palette/mark choices follow this repo's dataviz
  skill conventions (fixed categorical order, colorblind-validated hues,
  figure-level legends rather than in-axes ones — several bars hit 100%,
  leaving no corner reliably empty for a per-axes legend).

#### 5. Benchmark results (120 trials, run 2026-07-15 through 2026-07-20)

Full data in `benchmark/results.csv`; 3 rows that had accidentally used a
different LLM model (`nebius/google-gemma-27b` instead of the fixed
`nebius/nvidia-nemotron-120b`) were corrected in place per the operator's
instruction (no re-run). Headline: **LLM 95% safe / 97% success-rate, VLM
82% safe / 66% success-rate** overall (`nebius/qwen3-2.5-70b`, `cot_sc`
fixed for both).

The notable finding — reading all 31 non-empty operator notes rather than
trusting the aggregate numbers alone — is that VLM's biggest failure mode
is **not** a color/vision-grounding problem, despite looking like one at
first (13 notes literally say "picked the white cube instead of the red
one"). Evidence against a vision explanation: **zero** such mixups occur
in `pp_easy`/`pp_hard`, where color is stated directly ("pick the red
cube"); **12 of 13** occur in the arithmetic task, where the model must
first compute which color it needs from a stated value mapping
(`blue=1, red=2, white=3, orange=4`) before grounding it. Red(2) and
white(3) are adjacent in that list — consistent with an off-by-one slip in
the comparison/arithmetic, not a camera/color-perception failure. Filed
under "wrong object chosen (reasoning/value-mapping)" — 17 of 31 total
logged issues, the dominant category by a wide margin (see
`benchmark/figures/failure_modes.png`).

Step-count comparison (answering "does VLM take a different number of
actions for the same task"): no — LLM and VLM plan lengths are within
~1 step of each other for every task; VLM's higher plan-length *variance*
on hard tasks traces mostly to silently omitting one of four objects from
a "sort/put all" plan (an incomplete-enumeration failure, not a crash) —
checked by hand rather than assumed.

#### 6. `docs/TODO.md` (new)

Captures forward-looking items not implemented this session: zone-bounds-
aware release placement (opportunistic, using real tray dimensions where
known), a non-grasping push/nudge skill (move an object via the EE without
gripping), a `scene_mock.json` schema change from `position`+`size` to an
explicit bounding box for `targets.*` (agreed, deliberately deferred until
after the benchmark push), rewriting the skill/primitive prompt
descriptions for clarity, a multi-call "chat" interaction model (letting
one task span several LLM/VLM calls that can see each other's state), and
exposing this session's deterministic geometry fixes (§2 above) as
callable skills the model can invoke directly instead of silent
post-processing.

#### 7. `scene_mock.json` — real-world scene change (operator-authored)

Objects changed from 4 black shapes distinguished by type (cube/hexagon/
tower/cylinder) to **4 cubes distinguished only by color** (blue, red,
white, orange) — the operator's own edit, made to match a physical setup
change and to give the benchmark's specificity axis a natural "refer by
color vs. refer by the shared class" contrast. An old copy was kept
alongside as `src/robo_reason_task_interface/config/scene_mock old.json`
(untracked, not cleaned up — presumably intentional, left alone).

---

## Known Open Issues (updated 2026-07-22)

- **Working tree is uncommitted** and covers all of Session 5 §2-4:
  `config.py`, `app.py`, `bridge_node.py`, `app.js`/`index.html`/
  `style.css`, `llm_planner_node.py`, `vlm_planner_node.py`,
  `docs/TODO.md`, plus new untracked `benchmark/` (results, plan, scripts,
  figures). Only §1 (zone-collision spacing, `a1b744a`) is committed.
  Also untracked: `CLAUDE.md`, `docs/AUDIT_FINDINGS.md`, two
  `docs/council-*` artifacts from an unrelated `/llm-council` invocation,
  and `scene_mock old.json` (Session 5 §7) — none of these are part of
  the Session 5 changes proper.
- `_fix_object_height`/`_fix_release_height` (Session 5 §2) and the
  grasp-width edge-inset fix (§3) are verified against real captured
  hardware `debug/` data and unit-level replay, but not yet through a full
  fresh end-to-end hardware run since landing — next session should
  re-run the `sort_hard`/`arith_hard` prompts that originally surfaced
  these bugs and confirm no regressions.
- The nudge-outside logic in `_fix_release_height` fixes the *height* and
  *position* for a release that drifts into a zone it wasn't meant for,
  but doesn't stop the LLM from choosing that drifted position in the
  first place — still an open, harder problem (see `docs/TODO.md` #7's
  "expose the geometry fixes as callable skills" as one possible
  direction).
- `VLM_REASONING_EFFORT='none'` mitigation (Session 4 §8) still not A/B
  tested on hardware — carried over, unchanged since last update.
  Somewhat superseded in relevance by Session 5 §5's finding that the
  dominant VLM failure mode is arithmetic/reasoning under load rather
  than raw grounding precision, which `reasoning_effort` was aimed at.
- The plane-induced-homography → depth-based top-down rectification
  rewrite: still not started, carried over unchanged.
  `docs/TODO.md` now additionally tracks the bounding-box scene schema,
  push/nudge skill, multi-call chat model, and skill-ified geometry fixes
  as separate, not-yet-started items — see that file for the full list
  rather than duplicating it here.
