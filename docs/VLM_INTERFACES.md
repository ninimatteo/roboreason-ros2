# VLM Workspace — Interface Contracts

This document defines the contracts between the three components being developed
in parallel for the VLM pipeline:

| Component          | Owner       | Role                                                        |
|--------------------|-------------|-------------------------------------------------------------|
| **Planner**        | Matteo      | Orchestrates the flow, calls the camera + agent             |
| **VLM/LLM agent**  | colleagues  | Generates a plan from task + image (pixel coordinates)      |
| **Camera node**    | colleagues  | Provides RGB frames and pixel → 3D deprojection             |

Agree on these contracts first so everyone can develop and test independently
(each side mocks the others).

---

## Target workflow (VLM mode)

```
task_interface ──/plan_task(user_command)──► PLANNER
                                              │
                                              ├─ 1. GetImage ──────────► CAMERA NODE ── returns RGB frame
                                              │
                                              ├─ 2. agent.step(task, image) ──► VLM AGENT ── returns plan in PIXEL coords [w, h]
                                              │
                                              ├─ 3. Deproject(all [w,h]) ─────► CAMERA NODE ── returns [x, y, z] in base frame
                                              │
                                              └─ 4. substitute [x,y,z] into the plan ──/plan_task response (plan_json)──► manager → executor
```

The output `plan_json` is **identical in structure** to the LLM-mode plan, so the
manager and executor need no changes — they always receive base-frame coordinates.

---

## 1. Camera node — ROS2 services

The camera node must expose **two services**. Both live in `robo_reason_interfaces`.

### `GetImage` — `robo_reason_interfaces/srv/GetImage`

The planner requests the latest RGB frame.

```
# Request: (empty)
---
# Response
bool success
sensor_msgs/Image image     # RGB image, latest frame from the camera
string frame_id             # optical frame the image was captured in
string error_message
```

- **Service name (suggested):** `/camera/get_image`
- Return the most recent frame available; the planner only needs a single snapshot
  per task (Phase-1 "instantaneous perception").

### `Deproject` — `robo_reason_interfaces/srv/Deproject`

The planner sends a batch of pixel coordinates and gets back 3D points in the
robot base frame.

```
# Request
uint32[] u   # pixel column = width  (w), one entry per point
uint32[] v   # pixel row    = height (h), one entry per point
---
# Response
bool success
geometry_msgs/Point[] points  # x, y, z in robot base frame, SAME ORDER as the request
string frame_id               # frame the points are in (e.g. "base_link")
string error_message
```

- **Service name (suggested):** `/camera/deproject`
- `u` and `v` always have equal length; `points[i]` corresponds to `(u[i], v[i])`.
- **Batched on purpose:** the planner collects every pixel in the plan and sends a
  single call, so the camera node should handle an array.
- Deprojection uses the depth frame + camera intrinsics + the camera→base transform.
  The planner does NOT need to know how — it only consumes base-frame points.

---

## 2. VLM agent — Python interface

The agent is instantiated and called **inside the planner process** (same pattern as
today's `EmbodiedAgent`). The contract is a Python method, not a ROS service.

```python
agent = VLMEmbodiedAgent(
    reasoning_mode=<str>,        # e.g. 'fhp'
    vlm_parameters=<dict>,       # model_name, temperature, etc.
)

result = agent.step(observation={
    'user_request': str,         # the task description
    'image': <image>,            # see "Image format" below
})

# result.action            -> a single action with PIXEL coordinates
# result.end_of_simulation -> bool
```

### Plan coordinate convention (agent output)

The agent produces the **same action schema** as the LLM agent, with one difference:
positions are given in **image pixel coordinates `[w, h]`** instead of world `[x, y, z]`.

Example action from the agent:
```json
{
  "action_name": "pick",
  "target_position": [w, h],     // pixel coords, NOT meters
  "grasp_axis": "z",
  "come_back": true
}
```

The planner is responsible for replacing every `[w, h]` with the deprojected
`[x, y, z]` before sending the plan downstream.

> **Open question for the agent owners:** how should a pixel that has no valid depth
> (e.g. deprojection fails) be reported back? Proposal: the planner marks the step as
> failed and aborts the plan. Confirm this is acceptable.

### Image format

To be decided between planner + agent owners. Two options:
- **ROS message** (`sensor_msgs/Image`) passed straight through — simplest, but the
  agent depends on `cv_bridge` to decode.
- **NumPy array** (`HxWx3`, RGB, uint8) — the planner decodes with `cv_bridge` once
  and hands the agent a plain array. **Recommended** — keeps the agent ROS-agnostic
  and easier to unit-test.

---

## 3. Planner — parameter and mode

The planner gains a `mode` parameter, default `'LLM'`:

| Parameter | Values          | Default | Effect                                              |
|-----------|-----------------|---------|-----------------------------------------------------|
| `mode`    | `'LLM'` `'VLM'` | `'LLM'` | `'LLM'` = current scene-JSON path (unchanged). `'VLM'` = image + deproject path. |

- In `'LLM'` mode the camera services are **never called** — fully backward compatible.
- The `/plan_task` request is unchanged (`user_command` + `scene_json`); in `'VLM'`
  mode `scene_json` is ignored and the planner gets perception from the camera node.

Set via launch:
```bash
ros2 launch robo_reason_bringup real_robot.launch.py mode:=VLM
```

---

## Mocking for parallel development

- **Planner dev (no camera/agent yet):** stub `GetImage`/`Deproject` with a fake node
  returning canned data, and a fake agent returning a known pixel-plan.
- **Camera dev (no planner):** test services with `ros2 service call`.
- **Agent dev (no camera):** feed a saved image file and inspect the pixel-plan output.

---

## Summary of files added in `robo_reason_interfaces`

- `srv/GetImage.srv`
- `srv/Deproject.srv`
- `CMakeLists.txt` — added `sensor_msgs`, `geometry_msgs`, and the two new srvs
- `package.xml` — added `sensor_msgs`, `geometry_msgs` deps

Build to generate the Python/C++ bindings:
```bash
colcon build --packages-select robo_reason_interfaces
source install/setup.bash
```
