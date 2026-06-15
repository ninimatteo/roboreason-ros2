# RoboReason-ROS2

LLM-driven task planning and execution for the UR5cb robotic arm, built on ROS2 Humble.

Ported and extended from [RoboReason-Lab](https://github.com/aislabunimi/RoboReason-Lab).

---

## Overview

The system lets you type a natural language command (e.g. *"pick the red cube and place it in zone A"*) and have a large language model generate a robot action plan, validate it, and execute it — either in a software dry-run or on the real UR5cb.

```
You type a command
      ↓
[task_interface_node]   — terminal CLI, loads the scene
      ↓  /plan_task service
[llm_planner_node]      — LLM generates a plan (or mock)
      ↓  /execute_plan service
[plan_manager_node]     — walks through each action
      ↓  /execute_skill action
[fake_skill_executor]   — simulates execution (Phase 0)
   OR
[ur5_skill_executor]    — real UR5cb execution (Phase 1)
```

---

## Package Structure

```
roboreason-ros2/
├── src/
│   ├── robo_reason_interfaces/       # ROS2 service + action definitions (CMake)
│   │   ├── srv/
│   │   │   ├── PlanTask.srv
│   │   │   ├── ExecutePlan.srv
│   │   │   ├── GetImage.srv
│   │   │   └── Deproject.srv
│   │   ├── msg/
│   │   │   └── PixelArray.msg
│   │   └── action/
│   │       └── ExecuteSkill.action
│   │
│   ├── robo_reason_prompts/          # All LLM/VLM prompt templates (no nodes)
│   │   └── robo_reason_prompts/
│   │       ├── fhp_ffhp_prompts.py
│   │       ├── react_prompts.py
│   │       ├── cot_sc_prompts.py
│   │       ├── always_act_prompts.py
│   │       ├── self_refine_prompts.py
│   │       ├── tot_prompts.py
│   │       └── predicates_prompts.py
│   │
│   ├── robo_reason_reasoning/        # LLM/VLM clients + 7 reasoning methods (no nodes)
│   │   └── robo_reason_reasoning/
│   │       ├── embodied_agent.py     # Orchestrates reasoning method selection
│   │       ├── FoundationClients/    # LLMClient, VLMClient, BaseClient
│   │       ├── extraction_classes.py # UR5Action Pydantic model
│   │       ├── skills.py             # UR5 skill descriptions for LLM/VLM
│   │       ├── fhp_ffhp.py
│   │       ├── react.py
│   │       ├── cot_sc.py
│   │       ├── always_act.py
│   │       ├── self_refine.py
│   │       └── tot.py
│   │
│   ├── robo_reason_planner/          # LLM/VLM planner node
│   │   └── robo_reason_planner/
│   │       ├── llm_planner_node.py   # exposes /plan_task; LLM or VLM mode
│   │       └── command_grounding.py  # validates user command against scene
│   │
│   ├── robo_reason_manager/          # Plan manager node
│   │   └── robo_reason_manager/
│   │       ├── plan_manager_node.py  # exposes /execute_plan service
│   │       ├── world_state.py        # software model of the scene
│   │       ├── plan_validator.py     # pre-flight plan checker (mode-aware)
│   │       └── schemas.py            # skill definitions and JSON helpers
│   │
│   ├── robo_reason_executor/         # Skill executor nodes
│   │   └── robo_reason_executor/
│   │       ├── fake_skill_executor_node.py   # dry-run (Phase 0)
│   │       └── ur5_skill_executor_node.py    # real UR5cb (Phase 1)
│   │
│   ├── robo_reason_task_interface/   # Terminal CLI node
│   │   ├── config/
│   │   │   └── scene_mock.json       # scene description + workspace limits
│   │   └── robo_reason_task_interface/
│   │       └── task_interface_node.py
│   │
│   ├── robo_reason_bringup/          # Launch files only (no Python code)
│   │   └── launch/
│   │       ├── dry_run.launch.py           # full system (CLI included)
│   │       ├── dry_run_services.launch.py  # services only (no CLI)
│   │       └── real_robot.launch.py        # real UR5, LLM or VLM mode
│   │
│   └── vlm_camera_service/           # Camera bridge for VLM mode
│       ├── launch/
│       │   └── camera_services.launch.py
│       └── vlm_camera_service/
│           ├── camera_services_node.py   # /camera/get_image + /camera/deproject
│           ├── pixel_overlay_viewer_node.py  # live OpenCV debug window
│           └── charuco_utils.py          # ChArUco detection + pose estimation
```

---

## ROS2 Interfaces

### Services

**`/plan_task`** (`PlanTask.srv`)
```
Request:
  string user_command    # Natural language command
  string scene_json      # JSON scene description
Response:
  bool   success
  string plan_json       # List of UR5Actions as JSON
  string error_message
```

**`/execute_plan`** (`ExecutePlan.srv`)
```
Request:
  string plan_json       # Output of /plan_task
  string scene_json
Response:
  bool   success
  string final_state_json
  string report
  string error_message
```

### Actions

**`/execute_skill`** (`ExecuteSkill.action`)
```
Goal:
  string skill_name
  string skill_args_json
Result:
  bool   success
  string result_json
  string error_message
Feedback:
  string status
  float32 progress       # 0.0 → 1.0
```

---

## ROS2 Nodes

### `task_interface_node` (`robo_reason_task_interface`)
Interactive terminal. Loads `scene_mock.json`, prompts you for a command, calls `/plan_task`, then `/execute_plan`, and prints the result. This is your entry point.

### `llm_planner_node` (`robo_reason_planner`)
Exposes the `/plan_task` service. Supports two modes:
- **LLM mode**: passes `scene_mock.json` to `EmbodiedAgent(client_type='llm')`.
- **VLM mode**: captures a live RGB frame via `/camera/get_image`, passes it to `EmbodiedAgent(client_type='vlm')` which returns pixel coordinates, then batch-deprojection via `/camera/deproject` converts them to 3D `base_link` coordinates.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `mode` | string | `LLM` | `LLM` or `VLM` |
| `use_mock_llm` | bool | false | Skip LLM, use hardcoded plan (LLM mode only) |
| `reasoning_method` | string | `fhp` | Which reasoning strategy to use |
| `model_name` | string | `groq/llama4-scout-17b` | LLM/VLM model |
| `temperature` | double | 0.1 | LLM/VLM temperature |
| `tmp_dir` | string | `/root/ws/src/vlm_frames` | VLM frame capture directory |

### `plan_manager_node` (`robo_reason_manager`)
Exposes the `/execute_plan` service. Validates the plan against the scene, then executes each skill one at a time via the `/execute_skill` action server. Updates world state after each step and publishes logs.

Mode-aware validation: in LLM mode, holding-state guardrails (pick/release ordering) are active. In VLM mode, only workspace limits are enforced (object positions are not known in advance).

### `camera_services_node` (`vlm_camera_service`)
Required for VLM mode. Subscribes to Orbbec camera topics, exposes:
- `/camera/get_image` — returns the latest RGB frame.
- `/camera/deproject` — converts pixel `[u, v]` to 3D `[x, y, z]` in `base_link` using depth + ChArUco pose.

### `pixel_overlay_viewer_node` (`vlm_camera_service`)
Optional debug window showing the live camera feed with ChArUco axes and deprojected pixel markers overlaid.

### `fake_skill_executor_node` (`robo_reason_executor`)
Phase 0 dry-run. Accepts any skill, publishes fake progress (30% → 70% → 100%), waits 0.3s per step, always returns success. No robot required.

### `ur5_skill_executor_node` (`robo_reason_executor`)
Phase 1 real robot. Drop-in replacement for the fake executor. Uses `UR5CBPrimitives` to compute IK and send joint trajectories. Requires the `ur5cb_interface_node` in the workspace.

---

## Skills

| Skill | Required Args | What it does |
|---|---|---|
| `approach` | `target_position [x,y,z]`, `offset` (m), `approach_direction` (x/y/z) | Move end-effector near a position |
| `pick` | `target_position [x,y,z]`, `grasp_axis` | Close gripper on object |
| `release` | `release_position [x,y,z]` | Open gripper at position |
| `move_home` | — | Return arm to home configuration |
| `wait` | `time` (s) | Pause execution |

---

## Reasoning Methods

Selected via the `reasoning_method` parameter.

| Method | Parameter value | How it works |
|---|---|---|
| Finite Horizon Planning | `fhp` | Single LLM call → full plan, executed step by step |
| Fast FHP | `ffhp` | Like FHP but replans if needed |
| ReAct | `react` | Alternates think / act steps |
| Chain-of-Thought Self-Consistency | `cot-sc` | Generates K plans, picks the most consistent |
| Always Act | `always-act` | One LLM call per action, no plan ahead |
| Self-Refine | `self-refine` | Generates plan → critiques → refines N times |
| Tree of Thoughts | `tot` | Builds a tree of plan branches, picks best path |

---

## The Action Model (`UR5Action`)

Every plan step is a `UR5Action` Pydantic model:

```python
class UR5Action(BaseModel):
    action_name: str              # one of the 5 skills above
    target_position: list[float]  # [x, y, z] in meters, robot base frame
    release_position: list[float] # [x, y, z] for release skill
    offset: float = 0.1           # approach stand-off distance (m)
    approach_direction: str = 'z' # 'x', 'y', or 'z'
    grasp_axis: str = 'z'
    come_back: bool = False       # return to approach pose after grasp
    time: float = 0.0             # for wait skill
    score: float = 1.0            # LLM confidence 0–1
```

---

## Scene Format (`scene_mock.json`)

```json
{
  "frame": "base_link",
  "table": { "dimensions": [1.2, 0.8], "height": 0.0 },
  "robot": { "holding": null },
  "workspace": { "x": [0.25, 0.75], "y": [-0.40, 0.40], "z": [0.0, 0.5] },
  "objects": [
    {
      "name": "red_cube",
      "color": "red",
      "position": [0.45, -0.15, 0.025],
      "size": 0.05,
      "graspable": true,
      "state": "on_table"
    }
  ],
  "targets": [
    { "name": "zone_a", "position": [0.72, -0.22, 0.0], "size": [0.15, 0.15] }
  ]
}
```

---

## Setup

### Environment variables

Create `src/robo_reason_planner/.env` (loaded by `llm_planner_node`):
```
GROQ_API_KEY=gsk_your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com).

### Build

```bash
# Must be outside any Python venv
deactivate 2>/dev/null; cd /root/ws
colcon build --symlink-install
source install/setup.bash
```

---

## Quick Launch Reference

### Dry-run (no robot, no camera)

```bash
# All nodes + CLI in one terminal
ros2 launch robo_reason_bringup dry_run.launch.py use_mock_llm:=true

# Two-terminal (services + CLI separately)
ros2 launch robo_reason_bringup dry_run_services.launch.py use_mock_llm:=true
ros2 run robo_reason_task_interface task_interface_node
```

### Real Robot — LLM Mode

```bash
# T1: UR5 driver
ros2 launch ur_robot_driver ur_control.launch.py \
  ur_type:=ur5 robot_ip:=192.168.2.60 reverse_ip:=192.168.2.80 \
  use_fake_hardware:=false \
  initial_joint_controller:=scaled_joint_trajectory_controller

# T2: RoboReason stack
export GROQ_API_KEY=gsk_...
ros2 launch robo_reason_bringup real_robot.launch.py mode:=LLM reasoning_method:=fhp

# T3: CLI
ros2 run robo_reason_task_interface task_interface_node
```

### Real Robot — VLM Mode

```bash
# T1: Orbbec camera
./scripts/run_orbbec_registered.sh

# T2: Camera services + ChArUco overlay
ros2 launch vlm_camera_service camera_services.launch.py \
  show_overlay:=true charuco_z_sign:=1.0 \
  board_in_base_x:=-0.224 board_in_base_y:=-0.348 \
  board_in_base_roll:=3.14159 board_in_base_yaw:=1.5708 \
  z_offset_m:=0.01

# T3: UR5 driver (same as LLM mode)

# T4: RoboReason stack
export GROQ_API_KEY=gsk_...
ros2 launch robo_reason_bringup real_robot.launch.py mode:=VLM

# T5: CLI
ros2 run robo_reason_task_interface task_interface_node
```

For a detailed step-by-step guide including all parameters and troubleshooting, see [`docs/ROBOREASON_GUIDE.md`](docs/ROBOREASON_GUIDE.md).

---

## Design Notes

### Why separate packages?
Each package has a single responsibility. You can replace, test, or evolve any layer independently. For example:
- Swap `robo_reason_prompts` to change how the LLM is instructed without touching any node.
- Swap `robo_reason_executor` nodes without touching the planner or manager.
- Develop `robo_reason_reasoning` methods in isolation without a running robot.

### Why two executors?
Phase 0 (`fake`) lets you validate the LLM reasoning and ROS2 plumbing with zero hardware risk. Phase 1 (`ur5`) is a drop-in swap. The action server interface is identical — the planner and manager never know which is running.

### Why ROS2 services and not a single node?
Each layer can be replaced independently. You can call `/plan_task` from a web UI, a Jupyter notebook, or another robot. The plan is a plain JSON string, so it's language and framework agnostic.

### Why Threading.Event in plan_manager?
ROS2 service callbacks are synchronous, but sending action goals is asynchronous. We use `threading.Event` + `add_done_callback` to block the service callback until the skill finishes, without deadlocking the executor. This requires `MultiThreadedExecutor` + `ReentrantCallbackGroup`.
