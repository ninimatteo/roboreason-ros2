# VLM Camera Service Bridge

Minimal ROS 2 Humble workspace for testing the future Planner <-> Camera Node
contract without requiring a real VLM or robot base transform.

It provides:

```text
/camera/get_image
/camera/deproject
/camera/debug_pixels
```

`/camera/get_image` uses the real RGB topic from the camera. `/camera/deproject`
accepts pixel coordinates from a test client or future VLM and uses the real
depth topic plus `CameraInfo` to return 3D points in the camera optical frame.
Every `/camera/deproject` request also republishes the requested pixels on
`/camera/debug_pixels`, so a debug viewer can overlay them on the live RGB image.
If the configured ChArUco board is visible, the service also returns those same
3D points in the ChArUco board frame and the overlay draws the board axes.

## Services

`/camera/get_image`

Type:

```text
vlm_camera_interfaces/srv/GetImage
```

Returns the latest real RGB frame as `sensor_msgs/msg/Image`.

`/camera/deproject`

Type:

```text
vlm_camera_interfaces/srv/Deproject
```

Takes arrays of pixel coordinates:

```text
u[] = pixel columns
v[] = pixel rows
```

Returns an array of `geometry_msgs/msg/Point` in the same order. The pixel
coordinates can still be fake/manual for testing, but the depth value is read
from the real depth image. `points` are in the camera frame. `charuco_points`
are filled only when the ChArUco board pose is available.

## Quick Start

```bash
cd vlm_camera_service_mock
conda activate gemini-vlm-grasp
source scripts/setup_ros_env.sh
./scripts/build_workspace.sh
source scripts/setup_ros_env.sh
```

First start the Orbbec camera with registered depth.

Terminal 1:

```bash
cd vlm_camera_service_mock
conda activate gemini-vlm-grasp
./scripts/run_orbbec_registered.sh
```

The important topics are:

```text
/camera/color/image_raw
/camera/depth/image_raw
/camera/color/camera_info
```

Run service server.

Terminal 2:

```bash
cd vlm_camera_service_mock
conda activate gemini-vlm-grasp
source scripts/setup_ros_env.sh
ros2 launch vlm_camera_service_mock mock_camera_services.launch.py show_overlay:=true
```

`show_overlay:=true` starts an OpenCV window. The window shows the live RGB
camera image and keeps the latest received pixel markers visible until new
pixels arrive. If the ChArUco board is visible, it also draws marker corners
and the board coordinate axes.

Call the services.

Terminal 3:

```bash
cd vlm_camera_service_mock
conda activate gemini-vlm-grasp
source scripts/setup_ros_env.sh
ros2 service call /camera/get_image vlm_camera_interfaces/srv/GetImage "{}"
```

`ros2 service call` prints the full `image.data` byte array. That output is
very large because it is the raw RGB frame payload, not a compact image preview.

Call batched deprojection:

```bash
ros2 service call /camera/deproject vlm_camera_interfaces/srv/Deproject "{u: [640, 500], v: [360, 420]}"
```

Expected output:

```text
success: true
frame_id: camera_color_optical_frame
points:
  - x: ...
    y: ...
    z: ...
charuco_pose_available: true
charuco_frame_id: charuco_board
charuco_points:
  - x: ...
    y: ...
    z: ...
```

If `/camera/get_image` returns `RGB image not received yet`, the service bridge
is running but it has not seen any message on `/camera/color/image_raw`. Check:

```bash
ros2 topic list | grep /camera/color/image_raw
ros2 topic hz /camera/color/image_raw
```

When the service bridge is connected correctly, its logs show:

```text
received first CameraInfo: 1280x720, frame=camera_color_optical_frame
received first RGB image: 1280x720, encoding=rgb8, frame=camera_color_optical_frame
received first depth image: 1280x720, encoding=16UC1, frame=camera_color_optical_frame
```

## What Is Real Now

- Image comes from `/camera/color/image_raw`.
- Depth comes from `/camera/depth/image_raw`.
- Intrinsics come from `/camera/color/camera_info`.
- Depth is computed as the median valid value in a small window around each
  requested pixel.
- ChArUco pose is estimated from RGB + `CameraInfo` when the configured board is
  visible.

Validated center-pixel example:

```bash
ros2 service call /camera/deproject vlm_camera_interfaces/srv/Deproject "{u: [640], v: [360]}"
```

Example response:

```text
success: true
points:
- x: 0.000902426953193153
  y: -0.0011065340193397674
  z: 0.545
frame_id: camera_color_optical_frame
```

If no ChArUco board is visible, `charuco_pose_available` is false and
`charuco_points` is empty. Camera-frame `points` are still returned.

## What Is Still Missing

- Pixel coordinates are supplied manually or by a future VLM/planner.
- No tf2 lookup yet.
- Returned XYZ points are in the camera optical frame, not `base_link`.

## Launch Arguments

```bash
ros2 launch vlm_camera_service_mock mock_camera_services.launch.py \
  color_topic:=/camera/color/image_raw \
  depth_topic:=/camera/depth/image_raw \
  camera_info_topic:=/camera/color/camera_info \
  pixel_debug_topic:=/camera/debug_pixels \
  show_overlay:=true \
  charuco_enabled:=true \
  charuco_dictionary:=DICT_6X6_250 \
  charuco_squares_x:=5 \
  charuco_squares_y:=7 \
  charuco_square_length_m:=0.03 \
  charuco_marker_length_m:=0.015 \
  charuco_axis_length_m:=0.08 \
  window_size:=7 \
  min_depth_m:=0.15 \
  max_depth_m:=3.0
```

## ArUco Board Discussion

ChArUco is now used as an optional local reference frame when the board is
visible in the RGB image. The configured dictionary, square count, square
length, and marker length must match the real printed board.

See [docs/CHARUCO.md](docs/CHARUCO.md) for board generation and launch
parameters.

For robot-frame XYZ we need:

```text
pixel u,v
  + aligned depth image
  + CameraInfo intrinsics
  + tf2 camera_color_optical_frame -> base_link
```

An ArUco board can help calibrate or validate the camera-to-base transform:

- estimate board pose in camera frame,
- compare with known board pose in robot/base frame,
- derive or validate extrinsics.

So: use real RGB/depth for camera-frame XYZ, use ChArUco for board-frame XYZ,
then add tf2 later for robot/base-frame XYZ.
