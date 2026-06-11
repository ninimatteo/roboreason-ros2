# Architecture

This project isolates the service interface proposed for the VLM pipeline.

```text
Planner / VLM test client
  -> /camera/get_image
       <- latest real RGB Image

Planner / VLM test client
  -> /camera/deproject u[], v[]
       <- geometry_msgs/Point[] in camera optical frame
       <- geometry_msgs/Point[] in ChArUco frame, if board pose is available
       -> /camera/debug_pixels

Pixel overlay viewer
  -> /camera/color/image_raw
  -> /camera/color/camera_info
  -> /camera/debug_pixels
       <- OpenCV window with latest pixels and ChArUco axes drawn on RGB
```

## Packages

`vlm_camera_interfaces`

- Defines service contracts.
- `srv/GetImage.srv`
- `srv/Deproject.srv`

`vlm_camera_service_mock`

- Implements the service server.
- Subscribes to real RGB, depth, and camera info topics.
- Does not use tf2 yet.

## Runtime Topics

Defaults:

```text
/camera/color/image_raw
/camera/depth/image_raw
/camera/color/camera_info
/camera/debug_pixels
```

The Orbbec camera should publish registered depth, so the RGB pixel `(u, v)` can
be used on the depth image at the same pixel coordinate.

## Service Contracts

`GetImage.srv`

```text
---
bool success
sensor_msgs/Image image
string frame_id
string error_message
```

`Deproject.srv`

```text
uint32[] u
uint32[] v
---
bool success
geometry_msgs/Point[] points
string frame_id
bool charuco_pose_available
geometry_msgs/Point[] charuco_points
string charuco_frame_id
string error_message
```

`PixelArray.msg`

```text
std_msgs/Header header
uint32[] u
uint32[] v
```

## Real Deprojection

For each requested pixel:

```text
Z = median valid depth around (u, v)
X = (u - cx) * Z / fx
Y = (v - cy) * Z / fy
```

Depth encodings supported:

```text
16UC1 -> millimeters, converted to meters
32FC1 -> meters
```

The result is currently labeled with the camera frame, usually
`camera_color_optical_frame`.

## ChArUco Frame

The bridge optionally estimates the ChArUco board pose from the latest RGB image
and `CameraInfo`. OpenCV returns the board pose as board-to-camera
rotation/translation. Camera points are transformed into the board frame with:

```text
P_board = R_board_to_camera^T * (P_camera - t_board_to_camera)
```

If the board is not visible, camera-frame points are still returned and:

```text
charuco_pose_available: false
charuco_points: []
```

The next step for robot execution is adding a tf2 transform into `base_link`.

## Debug Overlay

When `/camera/deproject` receives pixels, the service bridge publishes those
same pixels on `/camera/debug_pixels`. `pixel_overlay_viewer_node` subscribes to
that topic, `/camera/color/image_raw`, and `/camera/color/camera_info`, then
draws numbered square markers and ChArUco axes on the live RGB image. Markers
persist until the next pixel array arrives.
