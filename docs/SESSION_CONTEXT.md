# Session Context — RoboReason ROS2 VLM Pipeline

**Date:** 2026-06-11

---

## Project Overview

**RoboReason ROS2** is an LLM/VLM-based task planner for a UR5cb robot equipped with an OnRobot RG2 gripper. The system uses large language models and, optionally, vision-language models to interpret tasks, reason about the environment, and generate executable robot plans.

### ROS2 Package Structure

The workspace contains 9 ROS2 packages:

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

### Service / Action Data Flow

```
task_interface
    → /plan_task (service)
        → /execute_plan (action)
            → /execute_skill (service, per skill)
                → joint trajectory commands (UR5cb)
                → gripper SetIO commands (OnRobot RG2)
```

### Docker Container

- **Image tag:** `roboreason:ur`
- **Launch alias:** `roboreason-ur`
- The alias was updated during this session to include `--privileged -v /dev:/dev` to allow USB camera access from inside the container.
- **Workspace mount:** `/home/matteonini/docker_ws/ros2_humble` on the host is mounted to `/root/ws` inside the container.

---

## VLM Pipeline Architecture

### Mode Selection

The planner node supports two operating modes, selected at launch time via the `mode` parameter:

**`mode='LLM'` (default)**
- Uses `EmbodiedAgent` with Groq LLM backend (or a mock).
- Scene information comes from `scene_mock.json` (static description).
- No camera hardware required.

**`mode='VLM'`**
- Camera node (Orbbec) publishes `/camera/color/image_raw` and `/camera/depth/image_raw`.
- The planner calls `/camera/get_image` (async/polled) to capture an RGB frame, which is saved to disk at `/tmp/roboreason_vlm/<task_id>/<index>_<stamp>.png`.
- The frame path is passed to `EmbodiedAgent(client_type='vlm')` which calls the VLM API and returns pixel coordinates `[w, h]` for objects of interest.
- The planner calls `/camera/deproject` in batch mode, converting pixel coordinates to 3D `[x, y, z]` in the `base_link` frame.
- The resulting 3D pose is forwarded through the standard manager/executor pipeline.

### Concurrency Requirement

`MultiThreadedExecutor` and `ReentrantCallbackGroup` are **required** in VLM mode. Without them, service calls made from inside other service callbacks deadlock because ROS2 cannot process the response of the inner call while the outer callback is still executing.

---

## EmbodiedAgent Refactor

This refactor was done by colleagues on the branch `embodied-agent-generalized`.

### Constructor Changes

| Old parameter | New parameter |
|---|---|
| `llm_parameters` | `client_parameters` |
| (not present) | `client_type='llm'` or `client_type='vlm'` |

### Observation Dictionary Changes

| Mode | Old key | New key / value |
|---|---|---|
| LLM | `'environment_map'` | unchanged |
| VLM | `'environment_map'` | `'image': str` (file path to saved frame) |

### Planner Update

The planner node was updated to match the new constructor signature:

```python
EmbodiedAgent(client_parameters=..., client_type='llm')   # LLM mode
EmbodiedAgent(client_parameters=..., client_type='vlm')   # VLM mode
```

And the observation passed to the agent in VLM mode:

```python
observation = {'image': image_paths[-1]}
```

---

## FoundationClients Import Bugs Fixed

Both `llm_client.py` and `vlm_client.py` contained a broken `try/except` import fallback:

```python
# BROKEN — only works when running as a script, fails when installed by colcon
try:
    from .base_client import BaseClient
except ImportError:
    from src.base_client import BaseClient
```

The fallback `from src.base_client` resolves incorrectly when the package is installed as a Python package by colcon. The fix replaces both with the correct relative import:

```python
from .base_client import BaseClient
```

**Files fixed:**
- `src/robo_reason_reasoning/robo_reason_reasoning/FoundationClients/src/llm_client.py`
- `src/robo_reason_reasoning/robo_reason_reasoning/FoundationClients/src/vlm_client.py`

Additionally, `base_client.py` uses `pandas` for usage metrics tracking, and `vlm_client.py` uses `Pillow` for image handling. Both were added to the Dockerfile as pip dependencies.

---

## VLM Camera Service Package

**Branch:** `VLM_Camera`
**Package name:** `vlm_camera_service`

### What the Package Does

**`camera_services_node`**
- Subscribes to `/camera/color/image_raw`, `/camera/depth/image_raw`, `/camera/color/camera_info`.
- Exposes two ROS2 services:
  - `/camera/get_image` — captures and returns an RGB frame (or saves it to disk).
  - `/camera/deproject` — takes pixel coordinates and returns 3D world-frame coordinates.
- Publishes `/camera/debug_pixels` for visualization.

**`pixel_overlay_viewer_node`**
- Displays a live OpenCV window showing the RGB feed.
- Overlays detected ChArUco board axes and VLM pixel markers.

**`charuco_utils.py`**
- Full ChArUco board detection using `cv2.aruco`.
- Pose estimation returning `rvec` and `tvec` (rotation vector and translation vector) of the board in the camera frame.

### What Was Missing and What Was Added

The Deproject service originally returned coordinates in the **camera frame**, not in `base_link`. A full transform chain was implemented.

**Added to `charuco_utils.py`:**
- `board_pose_to_matrix` — converts `(rvec, tvec)` to a 4×4 homogeneous transform matrix `T_cam_board`.
- `compute_T_base_camera` — computes `T_base_cam = T_base_board @ inv(T_cam_board)`.
- `transform_point_to_base` — applies the transform to a single 3D point.
- `rotation_matrix_to_quaternion` — converts a 3×3 rotation matrix to quaternion `(x, y, z, w)`.
- `T_to_transform_stamped` — wraps a 4×4 transform into a `geometry_msgs/TransformStamped`.
- `rpy_to_rotation_matrix` — converts roll/pitch/yaw (intrinsic ZYX) to a 3×3 rotation matrix.

**Added to `camera_services_node.py`:**
- Parameters: `board_in_base_x`, `board_in_base_y`, `board_in_base_z`, `board_in_base_roll`, `board_in_base_pitch`, `board_in_base_yaw`.
- These parameters describe the measured pose of the ChArUco board in the `base_link` frame.
- `T_base_board` is built from these parameters at startup.
- `T_base_camera` is cached each time the ChArUco board is detected.
- A TF2 `StaticTransformBroadcaster` publishes the computed camera-to-base transform on `/tf_static`.
- `_publish_camera_tf()` is called whenever a fresh board detection is available.

### Transform Chain

```
T_base_cam = T_base_board @ inv(T_cam_board)
```

where:
- `T_cam_board` is obtained from ArUco pose estimation (live, per-frame).
- `T_base_board` is fixed and comes from the manually measured `board_in_base_*` parameters.

When the ChArUco board is **visible**: Deproject returns coordinates in `base_link`.
When the ChArUco board is **not visible**: the node emits a warning and returns coordinates in the camera frame.

### Bugs Fixed in the Camera Package

1. **`setup.py` entry points** pointed to `vlm_camera_service_mock` (wrong module name). Fixed to `vlm_camera_service`.

2. **`setup.cfg` install path** was `vlm_camera_service_mock`. Fixed to `vlm_camera_service`.

3. **Imports in `camera_services_node.py` and `pixel_overlay_viewer_node.py`** imported interfaces from `vlm_camera_interfaces` (a package that does not exist). Fixed to import from `robo_reason_interfaces`.

4. **`Deproject.srv` was missing fields** that the node was trying to set on the response object: `charuco_pose_available`, `charuco_points`, `charuco_frame_id`. Because these fields were absent from the `.srv` definition, the assignment was silently swallowed as a Python attribute error, the response was never properly populated, and the service call **hung indefinitely** on the client side. The fields were added to the `.srv` file (see next section).

---

## ROS2 Interfaces Changes

### `Deproject.srv`

Three fields were added to the **response** section:

```
bool charuco_pose_available
geometry_msgs/Point[] charuco_points
string charuco_frame_id
```

- `charuco_pose_available`: whether the ChArUco board was detected in the current frame.
- `charuco_points`: the deprojected points expressed in the ChArUco board frame (useful for debugging — if `z ≈ 0`, the ArUco detection is correct and any remaining error is in the `board_in_base_*` parameters).
- `charuco_frame_id`: the TF frame name of the ChArUco board.

### `PixelArray.msg`

Already present in `robo_reason_interfaces/msg/`. No changes needed.

---

## Orbbec Camera Setup

**Camera model:** Orbbec Gemini 330 series (depth-registered RGB-D)

### Launch Script

```bash
./scripts/run_orbbec_registered.sh
```

This script launches the `orbbec_camera` ROS2 package with `depth_registration:=true`, which aligns the depth image to the color camera frame.

### Source and Build

- `OrbbecSDK_ROS2` was cloned to `/home/matteonini/docker_ws/ros2_humble/src/OrbbecSDK_ROS2`.
- Built from the workspace root `/root/ws` inside the container.
- **Build order is important:** `orbbec_camera_msgs` must be built before `orbbec_camera`.
- The Python virtual environment (venv) must be **deactivated** before building to avoid Cython scanning errors.

### APT Dependencies Installed

```
ros-humble-camera-info-manager
ros-humble-image-transport
ros-humble-image-transport-plugins
ros-humble-image-publisher
ros-humble-diagnostic-updater
ros-humble-tf2
ros-humble-tf2-ros
ros-humble-tf2-msgs
ros-humble-tf2-sensor-msgs
ros-humble-camera-calibration-parsers
ros-humble-statistics-msgs
ros-humble-backward-ros
libgflags-dev
nlohmann-json3-dev
libgoogle-glog-dev
libusb-1.0-0-dev
libudev-dev
libeigen3-dev
libssl-dev
libyaml-cpp-dev
```

### udev Rules

Orbbec udev rules were installed on the **host** (not in the container) to allow non-root USB device access:

```bash
sudo bash .../orbbec_camera/scripts/install_udev_rules.sh
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Docker Alias Update

The `roboreason-ur` alias in `~/.bashrc` was updated to include:

```
--privileged -v /dev:/dev
```

This passes all host device nodes into the container, which is required for USB camera access.

---

## Dockerfile Changes

**File:** `Dockerfile.roboreason`
**Target tag:** `roboreason:ur`

### ROS APT packages added

```
ros-humble-cv-bridge
python3-opencv
ros-humble-camera-info-manager
ros-humble-image-transport
ros-humble-image-transport-plugins
ros-humble-image-publisher
ros-humble-diagnostic-updater
ros-humble-tf2
ros-humble-tf2-ros
ros-humble-tf2-msgs
ros-humble-tf2-sensor-msgs
ros-humble-camera-calibration-parsers
ros-humble-statistics-msgs
ros-humble-backward-ros
```

### System APT packages added

```
libgflags-dev
nlohmann-json3-dev
libgoogle-glog-dev
libusb-1.0-0-dev
libudev-dev
libeigen3-dev
libssl-dev
libyaml-cpp-dev
```

### pip packages added

```
pandas
Pillow
openai
```

---

## Camera Calibration Status (IN PROGRESS)

### Approach

- A ChArUco board is printed and **glued to the table at a fixed, known location** relative to the robot base.
- The node detects the board in every camera frame and computes the camera pose from it.
- The `board_in_base_*` parameters encode the manually measured pose of the board in `base_link`.

### Board Parameters

| Parameter | Value |
|---|---|
| Dictionary | `DICT_6X6_250` |
| Layout | 3×4 squares |
| `square_length` | 0.06 m |
| `marker_length` | 0.03 m |

These were updated from earlier placeholder defaults.

### Verification

- The `pixel_overlay_viewer_node` confirms that the ChArUco board axes are correctly overlaid on the RGB feed (detection is working).
- The Deproject service now responds (previously hung indefinitely due to the missing `.srv` fields bug).

### Current Problem (UNSOLVED)

Deprojected 3D coordinates in `base_link` are wrong:

| Axis | Expected | Observed | Status |
|---|---|---|---|
| X | ~correct | ~correct | OK |
| Y | correct | off by ~20 cm | Wrong direction / sign |
| Z | 0.0 (on table) | ~0.9 m | Severely wrong |

**Hypothesis for Z error:** The value ~0.9 m is approximately the height of the camera above the table. This suggests that the rotation component of `T_base_camera` is not correctly mapping the camera Z axis (which points toward the scene, away from the camera) into the base Z axis (which points upward). The likely root cause is incorrect `board_in_base_roll` and/or `board_in_base_pitch` values.

**Hypothesis for Y error:** The axis orientation of the board relative to the robot base may be misidentified, resulting in a sign flip or axis swap for Y.

### Debugging Approach Suggested

Inspect the `charuco_points` field in the Deproject service response:

- If `charuco_points.z ≈ 0`: ArUco detection and board-frame deprojection are correct. The error is **purely in `board_in_base_*` parameters**.
- If `charuco_points.z ≈ 0.9 m`: The ArUco detection itself is wrong (wrong depth or wrong pose).

### Pending Questions

- What is the camera mount angle? (straight down, angled, sideways?)
- Is the ChArUco board lying flat on the table with the pattern facing up?
- What exact measurement method was used for `board_in_base_x` and `board_in_base_y`? (ruler from which point on the robot base?)

---

## Launch Commands Reference

### Camera Driver (Orbbec)

```bash
./scripts/run_orbbec_registered.sh
```

### Camera Service + Overlay Viewer

```bash
ros2 launch vlm_camera_service camera_services.launch.py \
  show_overlay:=true \
  board_in_base_x:=0.0 \
  board_in_base_y:=-0.20 \
  board_in_base_z:=0.0 \
  board_in_base_roll:=0.0 \
  board_in_base_pitch:=0.0 \
  board_in_base_yaw:=0.0
```

### Full LLM Stack

```bash
ros2 launch robo_reason_bringup real_robot.launch.py \
  reasoning_method:=fhp \
  model_name:=groq/llama4-scout-17b \
  temperature:=0.0
```

### Full VLM Stack (once calibration is complete)

```bash
ros2 launch robo_reason_bringup real_robot.launch.py \
  mode:=VLM \
  model_name:=groq/llama4-scout-17b
```

---

## Key Files Modified in This Session

| File | Changes |
|---|---|
| `src/robo_reason_planner/robo_reason_planner/llm_planner_node.py` | VLM mode support, `MultiThreadedExecutor` + `ReentrantCallbackGroup`, camera service clients, image save/deproject pipeline |
| `src/robo_reason_bringup/launch/real_robot.launch.py` | Added `mode` and `tmp_dir` launch parameters |
| `src/vlm_camera_service/vlm_camera_service/camera_services_node.py` | `board_in_base_*` parameters, `T_base_camera` computation, TF2 static broadcast, `base_link` transform in Deproject, fixed `robo_reason_interfaces` imports |
| `src/vlm_camera_service/vlm_camera_service/charuco_utils.py` | Added calibration math: `board_pose_to_matrix`, `compute_T_base_camera`, `transform_point_to_base`, `rotation_matrix_to_quaternion`, `T_to_transform_stamped`, `rpy_to_rotation_matrix` |
| `src/vlm_camera_service/setup.py` | Fixed `entry_points` module names (`vlm_camera_service_mock` → `vlm_camera_service`) |
| `src/vlm_camera_service/setup.cfg` | Fixed install path (`vlm_camera_service_mock` → `vlm_camera_service`) |
| `src/robo_reason_interfaces/srv/Deproject.srv` | Added `charuco_pose_available`, `charuco_points`, `charuco_frame_id` to response |
| `src/robo_reason_reasoning/robo_reason_reasoning/FoundationClients/src/llm_client.py` | Fixed relative import of `BaseClient` |
| `src/robo_reason_reasoning/robo_reason_reasoning/FoundationClients/src/vlm_client.py` | Fixed relative import of `BaseClient` |
| `Dockerfile.roboreason` | Added all camera, CV, and ML dependencies; added `pandas`, `Pillow`, `openai` pip packages |
| `/home/matteonini/.bashrc` | Updated `roboreason-ur` alias with `--privileged -v /dev:/dev` |

---

## Next Steps

1. **Resolve Z coordinate error**
   - Check `charuco_points.z` in the Deproject response to determine if the error is in ArUco detection or in `board_in_base_*` parameters.
   - Verify camera mount angle (straight down vs. angled).
   - Verify board orientation (flat on table, pattern facing up).
   - Correct `board_in_base_roll`, `board_in_base_pitch`, and `board_in_base_yaw` accordingly.

2. **Resolve Y coordinate error**
   - Confirm axis orientation of the board relative to the robot base.
   - Check sign/direction of `board_in_base_y`.

3. **Test full VLM pipeline end-to-end**
   - Once calibration is verified: camera → EmbodiedAgent (VLM) → deproject → planner → manager → executor.

4. **VLMEmbodiedAgent implementation (colleagues)**
   - Currently the `fhp_ffhp` reasoning methods raise `NotImplementedError` when `client_type='vlm'`.
   - Colleagues need to implement the VLM reasoning path in `EmbodiedAgent`.

5. **Robust JSON parsing in `fhp_ffhp.py`**
   - Line 77: `json.loads(raw)['plan']` fails silently or raises an exception for empty or malformed LLM responses (particularly with non-llama models).
   - Needs a try/except with graceful fallback.
