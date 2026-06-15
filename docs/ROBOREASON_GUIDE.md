# RoboReason ROS2 — Operator Guide

Step-by-step instructions to launch and use the full system, from camera to robot execution.

---

## Prerequisites

Before launching anything:

1. Load `ec_with_gripper.urp` on the UR5 pendant and press **Play**.
2. Make sure you are inside the Docker container:
   ```bash
   roboreason-ur   # or: docker exec -it <container> bash
   ```
3. Source the workspace (if not already in `.bashrc`):
   ```bash
   source /opt/ros/humble/setup.bash
   source /root/ws/install/setup.bash
   ```
4. Export your Groq API key:
   ```bash
   export GROQ_API_KEY=gsk_...
   ```

---

## Full Launch Sequence (5 terminals)

Open each terminal inside the container. Launch in order.

---

### Terminal 1 — Orbbec Camera

```bash
cd /root/ws/src/roboreason-ros2
./scripts/run_orbbec_registered.sh
```

Wait until you see depth stream initialization messages. The camera publishes:
- `/camera/color/image_raw`
- `/camera/depth/image_raw`
- `/camera/color/camera_info`

---

### Terminal 2 — Camera Services (ChArUco + Deproject)

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

A window will open showing the live camera feed with the ChArUco overlay. Verify:
- The coordinate triad on the board is right-handed (X=red, Y=green, Z=blue).
- The `tvec` text at the top-left shows plausible distances.

**Key parameters:**

| Parameter | Default | Description |
|---|---|---|
| `show_overlay` | `false` | Open the OpenCV visualization window |
| `charuco_z_sign` | `1.0` | 1.0 = right-handed triad (correct) |
| `board_in_base_x/y/z` | see above | Measured position of the board origin in `base_link` (m) |
| `board_in_base_roll/pitch/yaw` | see above | Intrinsic XYZ rotation of the board frame in `base_link` (rad) |
| `z_offset_m` | `0.01` | Extra Z lift added to all deprojected points (m) |
| `charuco_squares_x/y` | `3/4` | Board grid size |
| `charuco_square_length_m` | `0.062` | Physical square side length (m) |
| `charuco_marker_length_m` | `0.031` | Physical ArUco marker side length (m) |

---

### Terminal 3 — UR5 Driver

```bash
ros2 launch ur_robot_driver ur_control.launch.py \
  ur_type:=ur5 \
  robot_ip:=192.168.2.60 \
  reverse_ip:=192.168.2.80 \
  kinematics_params_file:=/root/ws/my_robot_calibration.yaml \
  launch_rviz:=false
```

Wait until the driver prints `Robot ready to receive control commands`.

---

### Terminal 4 — RoboReason Stack

Choose **LLM mode** or **VLM mode**:

#### LLM Mode
Uses a static scene description from `scene_mock.json`. No live camera perception.

```bash
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=LLM \
  reasoning_method:=fhp \
  model_name:=groq/llama4-scout-17b \
  temperature:=0.1
```

#### VLM Mode
Uses the live camera image for object detection. Requires Terminals 1 and 2 to be running.

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
| `reasoning_method` | `fhp` | See reasoning methods table below |
| `model_name` | `groq/llama4-scout-17b` | LLM/VLM model (see models below) |
| `temperature` | `0.1` | 0.0 = deterministic |
| `tmp_dir` | `/root/ws/src/vlm_frames` | Where VLM captures are saved |
| `use_mock_llm` | `false` | Skip LLM, use hardcoded plan (LLM mode only) |
| `include_task_interface` | `false` | Launch CLI in same process (not recommended) |

---

### Terminal 5 — Task Interface (CLI)

```bash
ros2 run robo_reason_task_interface task_interface_node
```

Type your command at the `>` prompt and press Enter. Examples:
```
> Pick the red cube and place it in zone A
> Take one object and position it elsewhere on the table
> Move the blue cube to the right side of the table
```

Type `quit` to exit.

---

## Reasoning Methods

Select with `reasoning_method:=<value>`.

| Value | Name | How it works | Best for |
|---|---|---|---|
| `fhp` | Finite Horizon Planning | Single LLM call → full plan | Fast, reliable, default |
| `ffhp` | Fast FHP | Like FHP but replans on failure | Slightly more robust |
| `react` | ReAct | Alternates think / act steps | Complex multi-step tasks |
| `cot_sc` | Chain-of-Thought Self-Consistency | K plans generated, most consistent wins | High accuracy, slower |
| `always_act` | Always Act | One LLM call per action, no ahead planning | Simple one-step tasks |
| `self_refine` | Self-Refine | Plan → critique → refine N times | Quality-focused |
| `tot` | Tree of Thoughts | Builds a tree of plans, picks best path | Most robust, slowest |

**Recommendation:**
- Use `fhp` for most tasks (fast, deterministic with `temperature:=0.0`).
- Use `react` or `tot` for more complex or ambiguous commands.
- In VLM mode, `fhp` is the most tested and stable option.

---

## Available Models

| Model name | Provider | Notes |
|---|---|---|
| `groq/llama4-scout-17b` | Groq | Default, fast, reliable JSON output |
| `groq/llama3.3-70b` | Groq | Higher quality, slower |
| `groq/llama3.1-8b` | Groq | Fastest, less accurate |

> Only `groq/llama*` models reliably produce parseable plan JSON. Other providers in the registry (Anthropic, Nebius, Gemini) require additional testing.

---

## LLM Mode — Detailed

The planner reads `scene_mock.json` (loaded by `task_interface_node`) and passes it to the LLM as the environment description. Object positions are hardcoded.

**Workspace limits** (in `scene_mock.json`) are enforced by the plan validator:
```json
"limits": {
  "x": [0.15, 0.85],
  "y": [-0.35, 0.35],
  "z": [-0.05, 0.55]
}
```
Adjust these to match your real table if the validator rejects valid positions.

**Guardrails active in LLM mode:**
- Pick fails if robot is already holding something.
- Release fails if robot is not holding anything.
- All positions validated against workspace limits.

---

## VLM Mode — Detailed

### How it works

1. User types a command.
2. `task_interface_node` sends it to `/plan_task`.
3. `llm_planner_node` calls `/camera/get_image` → gets the latest RGB frame.
4. Frame is saved to `/root/ws/src/vlm_frames/<task_id>/0000_<stamp>.png`.
5. Frame path is passed to `EmbodiedAgent(client_type='vlm')`.
6. VLM returns pixel coordinates `[w, h]` for each action target.
7. `llm_planner_node` calls `/camera/deproject` with all pixel coordinates.
8. `camera_services_node` converts pixels → 3D `[x, y, z]` in `base_link` using depth + ChArUco transform.
9. 3D plan is returned and passed to `plan_manager_node`.
10. Plan is validated (workspace limits only — no object-matching guardrails).
11. Skills are executed on the real robot.

### Checking VLM captures

After each command, inspect what image the VLM actually received:
```bash
ls -lt /root/ws/src/vlm_frames/
# Browse into the latest folder
```
From the host: `~/docker_ws/ros2_humble/src/vlm_frames/`

If the image is correct but the VLM picks the wrong position, it is a model reasoning issue (not a stale image).

### Guardrails in VLM mode

- Workspace limits are still enforced (from `scene_mock.json`).
- Holding-state checks (pick/release ordering) are **disabled** because object positions are not hardcoded.

---

## Dry-Run (No Robot)

For testing without the UR5:

```bash
# All nodes including CLI in one terminal
ros2 launch robo_reason_bringup dry_run.launch.py use_mock_llm:=true

# Or: services only, CLI in a separate terminal
ros2 launch robo_reason_bringup dry_run_services.launch.py use_mock_llm:=true
ros2 run robo_reason_task_interface task_interface_node
```

The `fake_skill_executor_node` simulates every skill successfully (0.3 s per step).

---

## Troubleshooting

### Camera not received (`waiting for camera inputs`)
- Check that the Orbbec camera is running (Terminal 1).
- Verify topics exist: `ros2 topic list | grep camera`
- QoS must be BEST_EFFORT on both sides — already set correctly in current code.

### ChArUco not detected
- Ensure the board is fully visible and well-lit.
- Check `show_overlay:=true` — the overlay window will show detection status.
- Minimum 4 corners required (`charuco_min_corners:=4`).

### Workspace limits error
- The detected position is outside `scene_mock.json` limits.
- Edit `robo_reason_task_interface/config/scene_mock.json` → `workspace.limits`.

### VLM picks wrong position
1. Check the saved image in `/root/ws/src/vlm_frames/<latest>/`.
2. If the image is correct: model reasoning issue — try a different `reasoning_method` or lower `temperature`.
3. If the image is stale: the camera subscription may not be receiving frames. Check Terminal 1 and 2.

### Build errors (Cython / stale symlinks)
```bash
deactivate   # must be outside any venv
cd /root/ws
colcon build --symlink-install
```
If stale symlink errors persist:
```bash
rm -rf build/orbbec_camera_msgs/ament_cmake_python/orbbec_camera_msgs/orbbec_camera_msgs
rm -rf build/robo_reason_interfaces/ament_cmake_python/robo_reason_interfaces/robo_reason_interfaces
colcon build --symlink-install
```

### Robot not moving
- Confirm the UR5 driver is running and `Robot ready` message appeared.
- Confirm the pendant program is in Play mode.
- Check `ur5_skill_executor_node` logs for IK errors.
