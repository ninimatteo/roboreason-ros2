# ChArUco Board

This project can use a ChArUco board as a local reference frame.

The implementation follows the modern OpenCV ArUco/ChArUco API:

```python
dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
board = cv2.aruco.CharucoBoard((squares_x, squares_y), square_length_m, marker_length_m, dictionary)
```

`cv2.aruco` is required. The code supports both common OpenCV APIs:

```text
OpenCV 4.5.x: cv2.aruco.CharucoBoard_create(...)
OpenCV 4.13+: cv2.aruco.CharucoBoard((...), ...)
OpenCV 4.5.x: cv2.aruco.DetectorParameters_create()
OpenCV 4.13+: cv2.aruco.DetectorParameters()
```

If ChArUco support is unavailable or pose estimation fails, the nodes keep
running: camera-frame XYZ and pixel overlay still work, while
`charuco_pose_available` remains false.

## Generate A Board

```bash
cd vlm_camera_service_mock
conda activate gemini-vlm-grasp
./scripts/generate_charuco_board.py
```

Default output:

```text
~/Downloads/charuco_board_5x7.png
```

Default board parameters:

```text
dictionary=DICT_6X6_250
squares_x=5
squares_y=7
square_length_m=0.03
marker_length_m=0.015
```

Print the board without scaling if possible. If the physical printed square size
is not exactly `0.03 m`, pass the real measured size to the launch file.

## Launch With Matching Parameters

```bash
ros2 launch vlm_camera_service_mock mock_camera_services.launch.py \
  show_overlay:=true \
  charuco_enabled:=true \
  charuco_dictionary:=DICT_6X6_250 \
  charuco_squares_x:=5 \
  charuco_squares_y:=7 \
  charuco_square_length_m:=0.03 \
  charuco_marker_length_m:=0.015
```

The square and marker lengths must match the physical printed board. If they do
not, the ChArUco XYZ coordinates will have the wrong scale.

## Frames

`/camera/deproject` returns:

```text
points
```

These are XYZ coordinates in the camera optical frame, usually:

```text
camera_color_optical_frame
```

If the board is visible, the same response also returns:

```text
charuco_pose_available: true
charuco_points
charuco_frame_id: charuco_board
```

These are the same 3D points expressed in the ChArUco board frame.

The board pose is estimated from RGB and `CameraInfo`. OpenCV estimates the
board-to-camera transform, and the service converts camera points into board
coordinates with:

```text
P_board = R_board_to_camera^T * (P_camera - t_board_to_camera)
```

## Overlay

With `show_overlay:=true`, the OpenCV window shows:

- live RGB image,
- latest requested VLM/debug pixels,
- detected ChArUco markers and corners,
- ChArUco coordinate axes.

Pixel markers persist until the next `/camera/deproject` request.
