# Session Context — RoboReason ROS2 VLM Pipeline

**Last updated:** 2026-06-24

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
  model_name:=groq/llama4-scout-17b \
  temperature:=0.1
```

### VLM Mode (Real Robot)
```bash
export GROQ_API_KEY=gsk_...
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=VLM \
  model_name:=groq/llama4-scout-17b \
  temperature:=0.1
```

### Task Interface CLI
```bash
ros2 run robo_reason_task_interface task_interface_node
```

---

## Known Open Issues

- VLM may appear to use a stale image if the model hallucinates object positions. Verify by checking the saved image in `/root/ws/src/vlm_frames/<latest>/` — if the image is correct but coordinates are wrong, it is a model reasoning issue.
- Workspace limits in `scene_mock.json` must be manually tuned to match the real table. The validator uses these even in VLM mode.
- `charuco_z_sign` default was -1.0 (left-handed); corrected to 1.0.
