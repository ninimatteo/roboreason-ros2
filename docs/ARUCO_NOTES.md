# ArUco Notes

ArUco is not needed for the mock service.

For real XYZ, the main runtime pipeline should be:

```text
pixel u,v
  -> aligned depth image
  -> CameraInfo intrinsics
  -> 3D point in camera_color_optical_frame
  -> tf2 transform into base_link
```

ArUco can help with calibration and validation:

1. Put an ArUco board at a known pose relative to the robot/base frame.
2. Detect board corners in RGB.
3. Estimate board pose in camera frame.
4. Compare camera-estimated pose with known robot/base pose.
5. Validate or solve camera-to-base extrinsics.

Suggested later package:

```text
camera_extrinsics_calibration/
```

But for planner/VLM integration, keep the camera service contract independent of
the calibration method.
