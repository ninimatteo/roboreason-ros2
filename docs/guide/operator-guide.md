# RoboReason ROS2 — Operator Guide

Step-by-step instructions to launch and use the full system, from dry-run to real robot.

> **Recommended path:** use the web GUI (below) — it supervises the stack and
> the UR driver for you, with live retuning and an emergency-stop button. The
> manual multi-terminal sequence further down still works and is useful for
> scripted/headless runs, or when the GUI itself is unavailable.

---

## Web GUI

`robo_reason_gui` is a FastAPI server with an embedded `rclpy` bridge node
(`GuiBridgeNode`) that serves a browser control panel. It owns the ROS2 stack
and the UR driver as supervised child processes, so there's no manual
multi-terminal launch sequence and no babysitting the flaky UR driver.

### Launch

```bash
ros2 run robo_reason_gui gui_node
# binds 0.0.0.0:8080; container runs --network host, so open
# http://localhost:8080 on the host
```

### Panel overview

- **Command** — type a natural-language instruction; **Send** runs `/plan_task`
  then `/execute_plan`, and the plan card updates step-by-step as
  `/execution_log` streams over a WebSocket. **Stop** is the emergency stop
  (see below); **Clear** empties the chat history.
- **Camera** (center) — live preview via `/api/camera/frame`, with VLM target
  pixels and the ChArUco axis overlay drawn on top when available.
- **Plan** — the animated, per-step plan view.
- **Configuration** — mode (`LLM`/`VLM`), reasoning method, provider, model,
  temperature, mock LLM. **Apply to running planner** pushes these onto the
  live `llm_planner_node`/`vlm_planner_node` via ROS `SetParameters` — no
  relaunch needed.
- **Camera (service)** — start/stop the Orbbec camera script, **Recalibrate
  ChArUco** to force a fresh camera→base transform without restarting the
  camera node, and a "Calibrated" LED reflecting `/camera/calibration_status`.
- **UR driver** — start/reconnect/stop the real driver with robot/reverse IP;
  shows state (stopped/connecting/connected/failed), attempt history and a log
  tail. Robot-connection LED in the header mirrors four probes: trajectory
  controller, joint states, gripper I/O service, and the teach pendant
  (reverse interface) — click it for the detail popover.
- **Stack** — start/restart/stop the ROS2 stack, with independent mock toggles
  for robot and camera (`gui_stack.launch.py`).
- **Backend** — ROS bridge node name, discovered node count/list.

### Emergency stop

The **Stop** button next to Send calls `POST /api/execute/cancel`, which:

1. Cancels the in-flight `/execute_skill` goal (`goal_handle.cancel_goal_async()`).
2. Aborts the rest of the plan (the manager's step loop checks a stop flag
   before every remaining step).
3. Sends a `move_home` skill goal to return the robot to its home joints.
4. Clears the stop flag so the system is ready for a new command.

This is served by a new `/cancel_execution` service
(`robo_reason_interfaces/srv/CancelExecution.srv`) on `plan_manager_node`, and
is not gated behind the GUI's single-command lock — it's callable even while a
plan is mid-execution.

### HTTP API

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/api/health` | ROS + robot connectivity + calibration snapshot |
| GET  | `/api/options` | selector options built from the model registry |
| GET  | `/api/preflight` | detect a duplicate `/execute_skill` action server before running a plan |
| POST | `/api/plan` | plan a command via `/plan_task` (no execution) |
| POST | `/api/execute` | execute a previously-planned plan via `/execute_plan` |
| POST | `/api/execute/cancel` | emergency stop — cancel + abort + return home |
| POST | `/api/config` | live-retune the running planner (`SetParameters`) |
| GET/POST | `/api/stack[/start\|/stop\|/restart]` | stack supervisor |
| GET/POST | `/api/driver[/start\|/stop\|/reconnect]` | UR driver supervisor |
| GET  | `/api/camera/status` | whether `/camera/get_image` is reachable |
| GET/POST | `/api/camera/service[/start\|/stop]` | Orbbec camera process supervisor |
| POST | `/api/camera/recalibrate` | force ChArUco re-calibration |
| GET  | `/api/camera/frame` | latest RGB frame as JPEG (503 if unavailable) |
| WS   | `/ws/execution` | live `/execution_log` stream |

See [`src/robo_reason_gui/README.md`](../src/robo_reason_gui/README.md) for the
architecture diagram and build notes.

---

## Prerequisites

Before launching anything:

1. Load `ec_with_gripper.urp` on the UR5 pendant and press **Play**.
2. Enter the Docker container:
   ```bash
   roboreason-ur   # or: docker exec -it <container> bash
   ```
3. Source the workspace (if not already in `.bashrc`):
   ```bash
   source /opt/ros/humble/setup.bash
   source /root/ws/install/setup.bash
   ```
4. Export your API key:
   ```bash
   export GROQ_API_KEY=gsk_...
   ```

---

## LLM Dry-Run (No Robot, No Camera)

Tests the full planning and validation pipeline with a fake executor. No hardware needed.

### Option A — Mock plan (no API key needed)

```bash
ros2 launch robo_reason_bringup dry_run.launch.py use_mock_llm:=true
```

In a second terminal:
```bash
ros2 run robo_reason_task_interface task_interface_node
```

Type a command → the mock planner hardcodes a pick-and-place plan from `scene_mock.json`.

### Option B — Real LLM, fake executor

```bash
export GROQ_API_KEY=gsk_...
ros2 launch robo_reason_bringup dry_run_services.launch.py \
  use_mock_llm:=false \
  reasoning_method:=fhp \
  model_name:=groq/openai-oss-120b
```

In a second terminal:
```bash
ros2 run robo_reason_task_interface task_interface_node
```

The LLM generates a real plan; `fake_skill_executor_node` logs each action and returns success immediately (no robot movement).

---

## VLM Dry-Run (No Robot, No Camera)

Tests the full VLM pipeline — from image to plan to execution — using PNG files as the mock camera input. No Orbbec camera, no UR5 needed.

### Preparation

Place at least one `.png` image in the mock frames folder:
```bash
mkdir -p /root/ws/src/mock_frames
cp /path/to/your/test_image.png /root/ws/src/mock_frames/
```

The image should show the scene from a top-down perspective (as the real camera would see it). Any RGB PNG works.

### Launch

```bash
export GROQ_API_KEY=gsk_...

# Terminal 1: all services
ros2 launch robo_reason_bringup vlm_dry_run.launch.py \
  images_dir:=/root/ws/src/mock_frames \
  model_name:=groq/qwen3.6-27b \
  reasoning_method:=fhp

# Terminal 2: CLI
ros2 run robo_reason_task_interface task_interface_node
```

**What happens:**
1. `mock_camera_service_node` loads the PNG and serves it via `/camera/get_image`.
2. `vlm_planner_node` sends the image to the VLM; receives pixel coordinates back.
3. `/camera/deproject` maps pixel `[u, v]` to workspace `[x, y, z]` linearly.
4. `plan_manager_node` validates and logs the plan (VLM mode — no hold-state checks).
5. `fake_skill_executor_node` logs each skill, no robot movement.

**VLM dry-run parameters:**

| Parameter | Default | Description |
|---|---|---|
| `reasoning_method` | `fhp` | Reasoning method |
| `model_name` | `groq/qwen3.6-27b` | VLM model (must support vision) |
| `temperature` | `0.1` | LLM temperature |
| `images_dir` | `/root/ws/src/mock_frames` | Folder with `.png` files to serve |
| `include_task_interface` | `false` | Launch CLI in same process |

The mock camera cycles through all PNGs alphabetically — each `/camera/get_image` call advances to the next file.

---

## Full Launch Sequence — Real Robot (5 terminals)

### Terminal 1 — Orbbec Camera (VLM mode only)

```bash
cd /root/ws/src/roboreason-ros2
./scripts/run_orbbec_registered.sh
```

Wait until depth stream initialization messages appear. The camera publishes:
- `/camera/color/image_raw`
- `/camera/depth/image_raw`
- `/camera/color/camera_info`

### Terminal 2 — Camera Services (VLM mode only)

```bash
ros2 launch vlm_camera_service camera_services.launch.py \
  show_overlay:=true \
  charuco_z_sign:=1.0 \
  board_in_base_x:=-0.224 \
  board_in_base_y:=-0.348 \
  board_in_base_z:=0.0 \
  board_in_base_roll:=3.14159 \
  board_in_base_pitch:=0.0 \
  board_in_base_yaw:=1.5708 \
  z_offset_m:=0.01
```

An OpenCV window opens with the live camera feed and ChArUco overlay. Verify:
- The coordinate triad on the board is right-handed (X=red, Y=green, Z=blue).
- The `tvec` text shows plausible distances from the camera.

**Key parameters:**

| Parameter | Default | Description |
|---|---|---|
| `show_overlay` | `false` | Open the OpenCV visualization window |
| `charuco_z_sign` | `1.0` | Sign of the Z axis — 1.0 = right-handed triad |
| `board_in_base_x/y/z` | `0.0` | Measured position of board origin in `base_link` (m) |
| `board_in_base_roll/pitch/yaw` | `0.0` | Intrinsic XYZ rotation of board frame in `base_link` (rad) |
| `z_offset_m` | `0.01` | Extra Z lift added to all deprojected points (m) |
| `charuco_squares_x` | `5` | Board columns |
| `charuco_squares_y` | `7` | Board rows |
| `charuco_square_length_m` | `0.03` | Physical square side (m) |
| `charuco_marker_length_m` | `0.015` | Physical ArUco marker side (m) |

### Terminal 3 — UR5 Driver

```bash
ros2 launch ur_robot_driver ur_control.launch.py \
  ur_type:=ur5 \
  robot_ip:=192.168.2.60 \
  reverse_ip:=192.168.2.80 \
  kinematics_params_file:=/root/ws/my_robot_calibration.yaml \
  launch_rviz:=false
```

Wait until `Robot ready to receive control commands` appears.

### Terminal 4 — RoboReason Stack

**LLM Mode** (uses `scene_mock.json` for environment description):
```bash
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=LLM \
  reasoning_method:=fhp \
  model_name:=groq/openai-oss-120b \
  temperature:=0.1
```

**VLM Mode** (uses live camera; requires Terminals 1–3):
```bash
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=VLM \
  model_name:=groq/qwen3.6-27b \
  temperature:=0.1
```

**All launch parameters:**

| Parameter | Default | Description |
|---|---|---|
| `mode` | `LLM` | `LLM` or `VLM` |
| `robot_ip` | `192.168.2.60` | UR5 robot IP |
| `reasoning_method` | `fhp` | Reasoning method (LLM mode) |
| `model_name` | `groq/qwen3.6-27b` | LLM/VLM model |
| `temperature` | `0.1` | 0.0 = deterministic |
| `use_mock_llm` | `false` | Skip LLM, use hardcoded plan (LLM mode only) |
| `include_task_interface` | `false` | Launch CLI in same process (not recommended) |

### Terminal 5 — Task Interface (CLI)

```bash
ros2 run robo_reason_task_interface task_interface_node
```

Type commands at the `>` prompt:
```
> Pick the red cube and place it in zone A
> Move the blue object to the right side of the table
```

Type `quit` to exit.

---

## Reasoning Methods

Select with `reasoning_method:=<value>`.

| Value | Name | How it works | Best for |
|---|---|---|---|
| `fhp` | Finite Horizon Planning | One LLM call → full plan | Fast, reliable, default |
| `ffhp` | Feasible FHP | Like FHP, replans on failure | More robust pick/place |
| `react` | ReAct | Alternates reasoning thoughts and actions | Complex multi-step tasks |
| `cot_sc` | CoT Self-Consistency | K plans generated, most consistent wins | High accuracy, slower |
| `always_act` | Always Act | One LLM call per action, no plan ahead | Simple reactive tasks |
| `self_refine` | Self-Refine | Plan → critique → refine N times | Quality-focused |
| `tot` | Tree of Thoughts | Explores plan tree, picks best branch | Most robust, slowest |

**Recommendation:** Use `fhp` for most tasks. Use `react` or `tot` for ambiguous or multi-constraint tasks.

---

## Available Models

> **2026-09-03 catalog re-check.** Both provider catalogs drifted during the
> six-week hardware pause. Groq dropped `qwen3-32b` (no replacement — it had
> no vision anyway, use `openai-oss-120b`) and added a second vision model,
> `qwen3.8-27b`. Nebius dropped `qwen3-2.5-70b` (`Qwen/Qwen2.5-VL-72B-Instruct`)
> **entirely** — this was the benchmark's VLM-mode model. Two of the
> surviving Nebius entries (`nvidia-cosmos3-33b`, `kimi-k2.6`) turned out to
> be vision-capable too and moved into that section below; one brand-new
> vision model (`minicpm-v-4.5`) was added. All launch-file defaults and
> this table have been updated to current model keys; if you have your own
> scripts or `.env` files still referencing an old key, update them too.
>
> **Verify against `https://api.tokenfactory.nebius.com/v1/models`**, not
> `api.studio.nebius.com` — they are different hosts with different
> catalogs, and only the former is what `base_client.py`'s nebius branch
> actually calls.
>
> **`kimi-k2.6` is the default VLM-mode model for Nebius** (first entry in
> `options.py`'s `VISION_MODELS['nebius']`) — `nvidia-cosmos3-33b` held that
> slot until 2026-09-04, when it returned a live `409 - model is stopped`
> on real hardware (a Nebius-side deployment state, not our bug; see the
> 2026-09-04 note below) while `kimi-k2.6` ran `sort_hard` and `arith_hard`
> clean back to back. `minicpm-v-4.5` is the third option. **None is a
> verified equivalent** to the July benchmark's `qwen3-2.5-70b` (gone from
> the catalog entirely) — different families, no shared trial data on this
> task, and none matches its 72B scale either (`kimi-k2.6`: 1T-parameter
> MoE, 32B active/token; `nvidia-cosmos3-33b`: 64B; `minicpm-v-4.5`: 8B,
> smallest). Any new `mode=VLM` + Nebius trial is a fresh baseline, not
> comparable to the July VLM arm's numbers.
>
> **Vision models now also work in `mode=LLM`** (text-only, no image) —
> `options.py` used to hide them there; changed 2026-09-04 so they can be
> compared 1:1 against text-only models on the same task.
>
> **2026-09-04 — every entry below live-tested** with an actual chat
> completion, not just a catalog listing (which has lagged real
> availability in both directions: `moonshotai/Kimi-K3` 404'd that morning,
> reappeared and worked a few hours later the same day — this catalog
> changes within a session, not just week to week). Four otherwise-valid
> ids came back `409 - model is stopped` (a Nebius-side deployment/warm-up
> state): `nemotron-3-nano-omni`, `llama-3.1-nemotron-ultra-253b`,
> `qwen3-next-80b-thinking`, and `nvidia-cosmos3-33b` — kept in the table
> below (the ids are real) but flagged; re-check live before relying on any
> of them. Added `glm-5.3-flash`, new since the 09-03 check.

### Groq (fast, free tier available)

| `model_name` | API model ID | Notes |
|---|---|---|
| `groq/openai-oss-20b` | `openai/gpt-oss-20b` | OpenAI OSS model |
| `groq/openai-oss-120b` | `openai/gpt-oss-120b` | Largest OSS model on Groq |
| `groq/qwen3.6-27b` | `qwen/qwen3.6-27b` | Vision enabled — use for VLM mode |
| `groq/qwen3.8-27b` | `qwen/qwen3.8-27b` | Vision enabled, newer |

### Nebius (OpenAI-compatible API)

Set `NEBIUS_API_KEY` and use `nebius/` prefix.

| `model_name` | API model ID | Notes |
|---|---|---|
| `nebius/google-gemma-27b` | `google/gemma-3-27b-it` | Good reasoning |
| `nebius/nvidia-nemotron-30b` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Efficient |
| `nebius/nvidia-nemotron-120b` | `nvidia/nemotron-3-super-120b-a12b` | Largest — planner default (`config.py`) |
| `nebius/openai-oss-120b` | `openai/gpt-oss-120b` | Now also hosted on Nebius |
| `nebius/llama-3.3-70b` | `meta-llama/Llama-3.3-70B-Instruct` | |
| `nebius/deepseek-v4-pro` | `deepseek-ai/DeepSeek-V4-Pro` | |
| `nebius/deepseek-v4-flash` | `deepseek-ai/DeepSeek-V4-Flash-0731` | |
| `nebius/glm-5.1` | `zai-org/GLM-5.1` | |
| `nebius/glm-5.2` | `zai-org/GLM-5.2` | |
| `nebius/glm-5.3-flash` | `zai-org/GLM-5.3-Flash` | |
| `nebius/hermes-4-405b` | `NousResearch/Hermes-4-405B` | |
| `nebius/minimax-m3` | `MiniMaxAI/MiniMax-M3` | |
| `nebius/nemotron-3-ultra-550b` | `nvidia/Nemotron-3-Ultra-550b-a55b` | |
| `nebius/nemotron-3.5-lightning` | `nvidia/Nemotron-3_5-Lightning` | |
| `nebius/nemotron-3-nano-omni` | `nvidia/Nemotron-3-Nano-Omni` | ⚠️ 409 "model is stopped" as of 2026-09-04 |
| `nebius/llama-3.1-nemotron-ultra-253b` | `nvidia/Llama-3_1-Nemotron-Ultra-253B-v1` | ⚠️ 409 "model is stopped" as of 2026-09-04 |
| `nebius/qwen3-235b` | `Qwen/Qwen3-235B-A22B-Instruct-2507` | |
| `nebius/qwen3-30b` | `Qwen/Qwen3-30B-A3B-Instruct-2507` | |
| `nebius/qwen3.5-397b` | `Qwen/Qwen3.5-397B-A17B` | |
| `nebius/qwen3-next-80b-thinking` | `Qwen/Qwen3-Next-80B-A3B-Thinking` | ⚠️ 409 "model is stopped" as of 2026-09-04 |
| `nebius/kimi-k2.7-code` | `moonshotai/Kimi-K2.7-Code` | |
| `nebius/qwen3-embedding-8b` | `Qwen/Qwen3-Embedding-8B` | Embeddings, not for planning |
| `nebius/kimi-k2.6` | `moonshotai/Kimi-K2.6` | Vision enabled, **default for VLM mode** — 1T MoE, 32B active/token |
| `nebius/nvidia-cosmos3-33b` | `nvidia/Cosmos3-Super-Reasoner` | Vision enabled — 64B, physical-AI-reasoning. ⚠️ 409 "model is stopped" as of 2026-09-04 |
| `nebius/minicpm-v-4.5` | `openbmb/MiniCPM-V-4_5` | Vision enabled — 8B, smallest |

> For VLM mode, only vision-enabled models work: `groq/qwen3.6-27b`,
> `groq/qwen3.8-27b`, `nebius/kimi-k2.6`, `nebius/nvidia-cosmos3-33b`,
> `nebius/minicpm-v-4.5` (the GUI's model dropdown restricts VLM mode to
> these automatically — see `robo_reason_gui/options.py`'s
> `VISION_MODELS`). These same models also work in LLM mode (text-only,
> see the note above) — everything else in the tables above is LLM-only.
>
> **Speed tiers.** The GUI's model dropdown now tags each entry `(fast)`,
> `(medium)`, or `(slow)` — one live single-word chat-completion call per
> model, 2026-09-04, not averaged over repeats (noisy, but the tiers are
> wide enough to hold up as a first cut): fast <1s, medium 1-5s, slow >5s.
> All 4 Groq models are fast (dedicated LPU hardware). On Nebius, fast:
> `minicpm-v-4.5`, `qwen3-30b`, `qwen3-235b`, `openai-oss-120b`,
> `hermes-4-405b`, `nvidia-nemotron-30b`, `deepseek-v4-pro`, `minimax-m3`,
> `kimi-k2.7-code`, `nvidia-nemotron-120b`, `nemotron-3-ultra-550b`,
> `nemotron-3.5-lightning`, `kimi-k2.6`; medium: `google-gemma-27b`,
> `glm-5.2`, `llama-3.3-70b`, `qwen3.5-397b`; slow: `glm-5.1`
> (7.1s), `deepseek-v4-flash` (10.5s), `glm-5.3-flash` (49.1s — an outlier
> worth re-checking, possibly a cold-start rather than steady-state cost).
> See `options.py`'s `MODEL_SPEED_TIER`. Untagged in the dropdown: the 4
> models that were returning 409 "stopped" that day, and the
> embeddings-only model (excluded from both dropdowns entirely).

---

## LLM Mode — Detailed

The planner reads `scene_mock.json` (loaded by `task_interface_node`) and passes it as the environment description to the LLM. Object positions are defined there.

**Workspace limits** (in `scene_mock.json`) are enforced by the plan validator:
```json
"workspace": {
  "x": [0.15, 0.85],
  "y": [-0.35, 0.35],
  "z": [-0.05, 0.55]
}
```
Adjust these if the validator rejects valid positions.

**Active guardrails in LLM mode:**
- Pick fails if robot is already holding something.
- Release fails if robot is not holding anything.
- All positions validated against workspace limits.

---

## VLM Mode — Detailed

### How it works

1. User types a command.
2. `task_interface_node` sends it to `/plan_task`.
3. `vlm_planner_node` calls `/camera/get_image` → gets the latest RGB frame.
4. Frame is saved to `tmp_dir` (default: `/root/ws/src/vlm_frames/<uuid>/0000_<stamp>.png`).
5. Frame path is passed to `EmbodiedAgent(client_type='vlm')`.
6. VLM returns pixel coordinates `[h, w]` for each action target.
7. `vlm_planner_node` calls `/camera/deproject` with all pixel coordinates.
8. `camera_services_node` converts pixels → 3D `[x, y, z]` in `base_link` using depth + ChArUco.
9. 3D plan is returned to `plan_manager_node`.
10. Workspace limits enforced; holding-state checks disabled (VLM mode).
11. Skills executed on the real robot.

### Checking VLM captures

```bash
ls -lt /root/ws/src/vlm_frames/
# From host:
ls ~/docker_ws/ros2_humble/src/vlm_frames/
```

If the image is correct but the VLM picks wrong positions, it is a model reasoning issue — try a different `reasoning_method` or lower `temperature`.

---

## ChArUco Calibration

The ChArUco board is used to establish a one-shot camera-to-base transform.

### Board specification (default)

| Parameter | Value |
|---|---|
| Dictionary | `DICT_6X6_250` |
| Squares X × Y | 5 × 7 |
| Square side | 30 mm |
| Marker side | 15 mm |

Generate a matching board:
```python
import cv2
from cv2 import aruco

dict_ = aruco.getPredefinedDictionary(aruco.DICT_6X6_250)
board = aruco.CharucoBoard((5, 7), 0.03, 0.015, dict_)
img = board.generateImage((1000, 1400))
cv2.imwrite('charuco_board.png', img)
```

Print at 100% scale (no fit-to-page). Measure the printed square side to verify.

### Setting board_in_base pose

Measure the position and orientation of the board's origin (bottom-left corner when looking at the board face-on) in the robot `base_link` frame:

```bash
ros2 launch vlm_camera_service camera_services.launch.py \
  show_overlay:=true \
  board_in_base_x:=<measured_x> \
  board_in_base_y:=<measured_y> \
  board_in_base_z:=<measured_z> \
  board_in_base_roll:=<roll_rad> \
  board_in_base_pitch:=<pitch_rad> \
  board_in_base_yaw:=<yaw_rad>
```

With the board flat on the table: `roll=π`, `pitch=0`, `yaw` = rotation of board X-axis relative to robot X-axis.

### Verifying calibration

1. Launch with `show_overlay:=true`.
2. Check the overlay window: the triad should be right-handed, axes aligned with the board.
3. Command the robot to approach a known position (e.g., center of board).
4. Compare the commanded position with the physical target.

### z_offset_m

Add a small positive `z_offset_m` (e.g. `0.01`) to lift deprojected points slightly above the table surface. This compensates for table height measurement uncertainty and prevents the robot from trying to push into the surface.

---

## Configuration via Environment Variables

All defaults are defined in `robo_reason_bringup/robo_reason_bringup/config.py` and can be overridden with `ROBOREASON_` prefixed environment variables:

```bash
export ROBOREASON_MODEL_NAME=groq/openai-oss-120b
export ROBOREASON_REASONING_METHOD=react
export ROBOREASON_ROBOT_IP=192.168.2.61
export ROBOREASON_Z_OFFSET_M=0.02
export ROBOREASON_DEBUG_TIMEZONE=Europe/Berlin
```

Or add them to a `.env` file in the working directory.

---

## Debug Recorder

Every `/plan_task` call is recorded to `debug/<timestamp>-<id>/` under the
repo root (`Settings.DEBUG_DIR`), regardless of mode or reasoning method:

| Artifact | Contents |
|---|---|
| `command.txt` | the user's natural-language command |
| `config.json` | mode, reasoning method, model, temperature at call time |
| `response.json` | the parsed plan (empty string if the call raised — see below) |
| `error.txt` | present only on failure — exception type + message |
| `logs.txt` | planner log lines captured during the call |
| `raw/` | the captured camera frame (VLM mode only) |
| `debug/debug.png` | pixel/ChArUco overlay image (VLM mode only) |

A row is appended to a root-level `debug/summary.csv` for every run (mode,
reasoning method, provider/model, success, timestamp), making it easy to
scan a batch of runs for pass/fail patterns.

Timestamps use `Settings.DEBUG_TIMEZONE` (default `Europe/Berlin`, overridable
via `ROBOREASON_DEBUG_TIMEZONE`/`.env`) rather than the container's UTC clock,
so run folder names match the operator's local time.

**Note:** `response.json` is empty specifically when the run raised an
exception (`response=None` is passed to the recorder on any failure path) —
it is not necessarily evidence that the raw LLM/VLM completion itself was
empty. Check `error.txt` for the actual failure reason.

---

## Troubleshooting

### `waiting for camera inputs`
- Check the Orbbec camera is running (Terminal 1).
- Verify: `ros2 topic list | grep camera`

### ChArUco not detected
- Ensure the board is fully visible and well-lit.
- Check `show_overlay:=true` — the overlay window shows detection status.
- Minimum 4 corners required.

### Workspace limits error
- The detected position is outside `scene_mock.json` workspace limits.
- Edit `robo_reason_task_interface/config/scene_mock.json` → `workspace`.

### VLM picks wrong position
1. Check the saved image in `vlm_frames/<latest>/`.
2. If image is correct: model reasoning issue — try a different `reasoning_method` or `temperature:=0.0`.
3. If image is stale: camera subscription not receiving frames — check Terminal 1 and 2.

### `fhp`/`ffhp` fail on Groq with an empty completion (`JSONDecodeError`)
Seen with Groq's `qwen3.6-27b` on `fhp`/`ffhp` (chained predicates→plan calls,
longer prompt); `cot_sc`'s shorter single-shot prompt does not exhibit this.
Likely cause: the model's reasoning/thinking-token budget is exhausted before
it emits the final JSON on longer, chained prompts. Groq's client only sets
`response_format` when both `force_json` and `forced_json_schema` are given
(`fhp_ffhp.py` never passes a schema, so it relies purely on prompt-based JSON
enforcement — see `vlm_client.py`'s `_call_groq`). Check `debug/<run>/error.txt`
to confirm; try a shorter-schema reasoning method or a different model.

### `fhp`/`ffhp`/`always_act` "grounded" the wrong spot on Nebius (deproject: no valid depth)
This is not a JSON error — the plan parses fine, but the returned pixel
coordinates don't land on the target's actual surface. Correlates with the
same schema/prompt-complexity hypothesis as the Groq failure above, but
manifests as inaccurate grounding rather than a parse failure. Cross-check
against `debug/<run>/debug.png` (the pixel overlay) to see where the model
actually pointed.

### Build errors (stale symlinks)
```bash
deactivate   # must be outside any venv
cd /root/ws
colcon build --symlink-install
```

If errors persist:
```bash
rm -rf build/robo_reason_interfaces/ament_cmake_python/robo_reason_interfaces/robo_reason_interfaces
colcon build --symlink-install
```

### Robot not moving
- Confirm the UR5 driver is running and printed `Robot ready`.
- Confirm the pendant program is in Play mode.
- Check `ur5_skill_executor_node` logs for IK errors.
- Verify `home_joints` in the launch file matches the physical robot position.
