# RoboReason-ROS2

LLM/VLM-driven task planning and execution for the UR5cb robotic arm, built on ROS2 Humble.

Ported and extended from [RoboReason-Lab](https://github.com/aislabunimi/RoboReason-Lab).

---

## Package Structure

```
roboreason-ros2/
├── src/
│   ├── robo_reason_interfaces/       # ROS2 service, action, message definitions (CMake)
│   │   ├── srv/  PlanTask · ExecutePlan · GetImage · Deproject
│   │   ├── msg/  PixelArray
│   │   └── action/  ExecuteSkill
│   │
│   ├── robo_reason_reasoning/        # LLM/VLM clients + 7 reasoning methods (no nodes)
│   │   └── robo_reason_reasoning/
│   │       ├── embodied_agent.py            # orchestrates reasoning method selection
│   │       ├── extraction_classes.py        # UR5Action Pydantic model
│   │       ├── skills.py                    # skill descriptions for LLM/VLM prompts
│   │       ├── fhp_ffhp.py · react.py · cot_sc.py · always_act.py · self_refine.py · tot.py
│   │       ├── reasoning_method.py          # base class
│   │       └── EmbodiedAgentsPrompts/       # all LLM/VLM prompt templates (split per method)
│   │           ├── fhp_ffhp_prompts.py · react_prompts.py · cot_sc_prompts.py
│   │           ├── always_act_prompts.py · self_refine_prompts.py · tot_prompts.py
│   │           └── predicates_prompts.py
│   │
│   ├── robo_reason_planner/          # Planner nodes (one per mode)
│   │   └── robo_reason_planner/
│   │       ├── llm_planner_node.py   # /plan_task — LLM mode (text + scene JSON)
│   │       ├── vlm_planner_node.py   # /plan_task — VLM mode (camera + deproject)
│   │       └── command_grounding.py  # validates user command against scene
│   │
│   ├── robo_reason_manager/          # Plan manager node
│   │   └── robo_reason_manager/
│   │       ├── plan_manager_node.py  # /execute_plan service
│   │       ├── world_state.py        # software model of the scene
│   │       ├── plan_validator.py     # pre-flight plan checker (mode-aware)
│   │       └── schemas.py            # skill definitions and JSON helpers
│   │
│   ├── robo_reason_executor/         # Skill executor nodes
│   │   └── robo_reason_executor/
│   │       ├── fake_skill_executor_node.py  # dry-run (logs actions, no robot)
│   │       └── ur5_skill_executor_node.py   # real UR5cb with OnRobot RG2 gripper
│   │
│   ├── robo_reason_task_interface/   # Terminal CLI node
│   │   ├── config/
│   │   │   └── scene_mock.json       # scene description + workspace limits
│   │   └── robo_reason_task_interface/
│   │       └── task_interface_node.py
│   │
│   ├── robo_reason_bringup/          # Launch files + centralized configuration
│   │   ├── launch/
│   │   │   ├── dry_run.launch.py          # LLM dry-run (all nodes)
│   │   │   ├── dry_run_services.launch.py # LLM dry-run (services only)
│   │   │   ├── vlm_dry_run.launch.py      # VLM dry-run (mock camera + vlm_planner)
│   │   │   └── real_robot.launch.py       # real UR5, selects LLM or VLM node
│   │   └── robo_reason_bringup/
│   │       └── config.py                  # pydantic-settings — single source of defaults
│   │
│   └── vlm_camera_service/           # Camera bridge for VLM mode
│       ├── launch/
│       │   └── camera_services.launch.py
│       └── vlm_camera_service/
│           ├── camera_services_node.py      # /camera/get_image + /camera/deproject (real)
│           ├── mock_camera_service_node.py  # /camera/get_image + /camera/deproject (mock)
│           ├── pixel_overlay_viewer_node.py # live OpenCV debug window
│           └── charuco_utils.py             # ChArUco detection + pose estimation
│
└── docs/
    ├── ROBOREASON_GUIDE.md  — step-by-step operator guide (launch, dry-run, calibration)
    └── SESSION_CONTEXT.md   — development log of recent fixes
```

---

## Node Overview

| Node | Package | Description |
|---|---|---|
| `llm_planner_node` | `robo_reason_planner` | `/plan_task` — LLM or mock, uses scene JSON |
| `vlm_planner_node` | `robo_reason_planner` | `/plan_task` — VLM, captures camera frame + deprojects |
| `plan_manager_node` | `robo_reason_manager` | `/execute_plan` — validates + executes plan step by step |
| `fake_skill_executor_node` | `robo_reason_executor` | `/execute_skill` action — dry-run, no robot |
| `ur5_skill_executor_node` | `robo_reason_executor` | `/execute_skill` action — real UR5cb + RG2 gripper |
| `task_interface_node` | `robo_reason_task_interface` | Interactive CLI entry point |
| `camera_services_node` | `vlm_camera_service` | Real camera: RGB capture + pixel→3D deproject |
| `mock_camera_service_node` | `vlm_camera_service` | Fake camera: serves PNGs, returns linear 3D coords |
| `pixel_overlay_viewer_node` | `vlm_camera_service` | Live debug window with pixel overlays |

---

## Installation

### Prerequisites

- ROS2 Humble
- Python 3.10+
- Colcon
- (VLM mode) OpenCV, cv_bridge, Orbbec camera ROS2 driver

### Python dependencies

```bash
pip install pydantic pydantic-settings python-dotenv
pip install groq openai anthropic google-generativeai
pip install roboticstoolbox-python spatialmath-python  # real robot only
```

### Build

```bash
# Must be outside any Python venv
deactivate 2>/dev/null
cd /root/ws
colcon build --symlink-install
source install/setup.bash
```

Procedure if the venv dependencies are not respected after the build

```bash
# activate venv
source venv/bin/activate
# build with the current venv data
python3 $(which colcon) build --symlink-install --cmake-args -DPython3_EXECUTABLE=$(which python3)
# source installation
source install/setup.bash
```

### API key

```bash
export GROQ_API_KEY=gsk_...
# Or add to a .env file in the working directory
```

---

## Quick Launch Reference

### LLM Dry-run (no robot, no camera)

```bash
# In the first terminal either call:
ros2 launch robo_reason_bringup dry_run.launch.py use_mock_llm:=true    # Mock plan (no API key needed)
ros2 launch robo_reason_bringup dry_run.launch.py use_mock_llm:=false reasoning_method:=fhp    # Real LLM, fake executor

# In the second terminal:
ros2 run robo_reason_task_interface task_interface_node
```

### VLM Dry-run (no robot, no camera — uses PNG images as mock camera)

```bash
# Place PNGs in /your_path/mock_frames/ first
ros2 launch robo_reason_bringup vlm_dry_run.launch.py \
  images_dir:=/your_path/mock_frames \
  model_name:=groq/qwen3.6-27b
# In a second terminal:
ros2 run robo_reason_task_interface task_interface_node
```

### Real Robot — LLM Mode

```bash
# T1: UR5 ROS2 driver
ros2 launch ur_robot_driver ur_control.launch.py \
  ur_type:=ur5 robot_ip:=192.168.2.60 reverse_ip:=192.168.2.80 \
  use_fake_hardware:=false \
  initial_joint_controller:=scaled_joint_trajectory_controller

# T2: RoboReason stack
ros2 launch robo_reason_bringup real_robot.launch.py mode:=LLM

# T3: CLI
ros2 run robo_reason_task_interface task_interface_node
```

### Real Robot — VLM Mode

```bash
# T1: Orbbec camera
./scripts/run_orbbec_registered.sh

# T2: Camera services
ros2 launch vlm_camera_service camera_services.launch.py \
  show_overlay:=true charuco_z_sign:=1.0 z_offset_m:=0.01 \
  board_in_base_x:=-0.224 board_in_base_y:=-0.348 \
  board_in_base_roll:=3.14159 board_in_base_yaw:=1.5708

# T3: UR5 driver (same as LLM mode)

# T4: RoboReason stack
ros2 launch robo_reason_bringup real_robot.launch.py mode:=VLM

# T5: CLI
ros2 run robo_reason_task_interface task_interface_node
```

For full step-by-step instructions, parameter reference, model table, and troubleshooting: see [`docs/ROBOREASON_GUIDE.md`](docs/ROBOREASON_GUIDE.md).

---

## Skills

| Skill | Required args | Effect |
|---|---|---|
| `approach` | `target_position`, `offset`, `approach_direction` | Move TCP to stand-off above/beside target |
| `pick` | `target_position`, `grasp_axis` | Lower to target, close gripper |
| `release` | `release_position`, `object_height` | Move to position, open gripper |
| `move_home` | — | Return arm to home joints |
| `wait` | `time` | Pause execution (seconds) |

## Reasoning Methods

| Parameter value | Name | How it works |
|---|---|---|
| `fhp` | Finite Horizon Planning | One LLM call → full plan (default) |
| `ffhp` | Feasible FHP | Like FHP, replans on failure |
| `react` | ReAct | Alternates reasoning steps and actions |
| `cot_sc` | CoT Self-Consistency | Generates K plans, picks most consistent |
| `always_act` | Always Act | One LLM call per action, no ahead-planning |
| `self_refine` | Self-Refine | Plan → critique → refine N times |
| `tot` | Tree of Thoughts | Explores plan tree, picks best branch |
