# Testing

## Build

```bash
cd vlm_camera_service_mock
conda activate gemini-vlm-grasp
source scripts/setup_ros_env.sh
./scripts/build_workspace.sh
source scripts/setup_ros_env.sh
```

## Start Camera

Start the Orbbec driver first, with depth registered/aligned to RGB.

Expected topics:

```bash
ros2 topic list | grep camera
```

Important topics:

```text
/camera/color/image_raw
/camera/depth/image_raw
/camera/color/camera_info
```

## Start Service Server

```bash
ros2 launch vlm_camera_service_mock mock_camera_services.launch.py show_overlay:=true
```

With `show_overlay:=true`, an OpenCV window opens and shows the live RGB image.
Pixel markers appear after a `/camera/deproject` call and stay visible until the
next pixel array arrives. If the configured ChArUco board is visible, the viewer
also draws the board markers, corners, and coordinate axes.

## Call GetImage

```bash
ros2 service call /camera/get_image vlm_camera_interfaces/srv/GetImage "{}"
```

Expected:

```text
success: true
frame_id: camera_color_optical_frame
image.width: 1280
image.height: 720
image.encoding: rgb8
```

`ros2 service call` also prints the full `image.data` array. This is expected:
it is the raw RGB payload of the image message.

If `success` is false, the node has not received an RGB frame yet.

The service server should log:

```text
received first CameraInfo: 1280x720, frame=camera_color_optical_frame
received first RGB image: 1280x720, encoding=rgb8, frame=camera_color_optical_frame
received first depth image: 1280x720, encoding=16UC1, frame=camera_color_optical_frame
```

## Call Deproject

```bash
ros2 service call /camera/deproject vlm_camera_interfaces/srv/Deproject "{u: [640, 500], v: [360, 420]}"
```

Expected:

```text
success: true
frame_id: camera_color_optical_frame
points: same length as request
charuco_pose_available: true or false
charuco_points: same length as request only when charuco_pose_available is true
```

The same requested pixels are also published to:

```text
/camera/debug_pixels
```

The requested pixels can be fake/manual. The returned Z comes from the real
depth topic.

If `charuco_pose_available` is true, `charuco_points` contains the same points
expressed in the ChArUco board frame.

Validated example from the center pixel:

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

When a ChArUco board is visible and the launch parameters match the real board,
the same response also includes `charuco_points` in `charuco_board`.

If `success` is false, common causes are:

- depth image not received yet,
- camera info not received yet,
- pixel outside the image,
- no valid depth in the window around that pixel,
- depth outside `min_depth_m` / `max_depth_m`.

## Unit Tests

```bash
cd ros2_ws/src/vlm_camera_service_mock
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
```
