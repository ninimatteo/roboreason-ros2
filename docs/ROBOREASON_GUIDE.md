# RoboReason ROS2 — Operator Guide

Step-by-step instructions to launch and use the full system, from dry-run to real robot.

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
  model_name:=groq/llama4-scout-17b
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
  model_name:=groq/llama4-scout-17b \
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
| `model_name` | `groq/llama4-scout-17b` | VLM model (must support vision) |
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
  model_name:=groq/llama4-scout-17b \
  temperature:=0.1
```

**VLM Mode** (uses live camera; requires Terminals 1–3):
```bash
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=VLM \
  model_name:=groq/llama4-scout-17b \
  temperature:=0.1
```

**All launch parameters:**

| Parameter | Default | Description |
|---|---|---|
| `mode` | `LLM` | `LLM` or `VLM` |
| `robot_ip` | `192.168.2.60` | UR5 robot IP |
| `reasoning_method` | `fhp` | Reasoning method (LLM mode) |
| `model_name` | `groq/llama4-scout-17b` | LLM/VLM model |
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

### Groq (fast, free tier available)

| `model_name` | API model ID | Notes |
|---|---|---|
| `groq/llama4-scout-17b` | `meta-llama/llama-4-scout-17b-16e-instruct` | Default — vision enabled |
| `groq/llama4-maverick-17b` | `meta-llama/llama-4-maverick-17b-128e-instruct` | Higher quality |
| `groq/llama3.3-70b` | `llama-3.3-70b-versatile` | Reliable JSON, no vision |
| `groq/llama3.1-8b` | `llama-3.1-8b-instant` | Fastest, less accurate |
| `groq/moonshotai-kimik2-32b` | `moonshotai/kimi-k2-instruct-0905` | High quality |
| `groq/qwen3-32b` | `qwen/qwen3-32b` | Strong reasoning |
| `groq/openai-oss-20b` | `openai/gpt-oss-20b` | OpenAI OSS model |
| `groq/openai-oss-120b` | `openai/gpt-oss-120b` | Largest OSS model on Groq |

### Nebius (OpenAI-compatible API)

Set `NEBIUS_API_KEY` and use `nebius/` prefix.

| `model_name` | API model ID | Notes |
|---|---|---|
| `nebius/qwen3-2.5-70b` | `Qwen/Qwen2.5-VL-72B-Instruct` | Vision enabled |
| `nebius/google-gemma-27b` | `google/gemma-3-27b-it` | Good reasoning |
| `nebius/nvidia-nemotron-30b` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Efficient |
| `nebius/nvidia-nemotron-120b` | `nvidia/nemotron-3-super-120b-a12b` | Largest |
| `nebius/kimi-k2.6` | `moonshotai/Kimi-K2.6` | High quality |

> For VLM mode, only vision-enabled models work: `groq/llama4-scout-17b` and `nebius/qwen3-2.5-70b`.

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
export ROBOREASON_MODEL_NAME=groq/llama3.3-70b
export ROBOREASON_REASONING_METHOD=react
export ROBOREASON_ROBOT_IP=192.168.2.61
export ROBOREASON_Z_OFFSET_M=0.02
```

Or add them to a `.env` file in the working directory.

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
