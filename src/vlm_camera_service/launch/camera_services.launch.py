from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    color_topic = LaunchConfiguration("color_topic")
    depth_topic = LaunchConfiguration("depth_topic")
    camera_info_topic = LaunchConfiguration("camera_info_topic")
    pixel_debug_topic = LaunchConfiguration("pixel_debug_topic")
    show_overlay = LaunchConfiguration("show_overlay")
    charuco_enabled = LaunchConfiguration("charuco_enabled")
    charuco_dictionary = LaunchConfiguration("charuco_dictionary")
    charuco_squares_x = LaunchConfiguration("charuco_squares_x")
    charuco_squares_y = LaunchConfiguration("charuco_squares_y")
    charuco_square_length_m = LaunchConfiguration("charuco_square_length_m")
    charuco_marker_length_m = LaunchConfiguration("charuco_marker_length_m")
    charuco_axis_length_m = LaunchConfiguration("charuco_axis_length_m")
    charuco_min_corners = LaunchConfiguration("charuco_min_corners")
    charuco_z_sign = LaunchConfiguration("charuco_z_sign")
    charuco_frame_id = LaunchConfiguration("charuco_frame_id")
    window_size = LaunchConfiguration("window_size")
    min_depth_m = LaunchConfiguration("min_depth_m")
    max_depth_m = LaunchConfiguration("max_depth_m")
    board_in_base_x     = LaunchConfiguration("board_in_base_x")
    board_in_base_y     = LaunchConfiguration("board_in_base_y")
    board_in_base_z     = LaunchConfiguration("board_in_base_z")
    board_in_base_roll  = LaunchConfiguration("board_in_base_roll")
    board_in_base_pitch = LaunchConfiguration("board_in_base_pitch")
    board_in_base_yaw   = LaunchConfiguration("board_in_base_yaw")
    z_offset_m          = LaunchConfiguration("z_offset_m")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "color_topic",
                default_value="/camera/color/image_raw",
            ),
            DeclareLaunchArgument(
                "depth_topic",
                default_value="/camera/depth/image_raw",
            ),
            DeclareLaunchArgument(
                "camera_info_topic",
                default_value="/camera/color/camera_info",
            ),
            DeclareLaunchArgument(
                "pixel_debug_topic",
                default_value="/camera/debug_pixels",
            ),
            DeclareLaunchArgument("show_overlay", default_value="false"),
            DeclareLaunchArgument("charuco_enabled", default_value="true"),
            DeclareLaunchArgument("charuco_dictionary", default_value="DICT_6X6_250"),
            DeclareLaunchArgument("charuco_squares_x", default_value="3"),
            DeclareLaunchArgument("charuco_squares_y", default_value="4"),
            DeclareLaunchArgument("charuco_square_length_m", default_value="0.062"),
            DeclareLaunchArgument("charuco_marker_length_m", default_value="0.031"),
            DeclareLaunchArgument("charuco_axis_length_m", default_value="0.08"),
            DeclareLaunchArgument("charuco_min_corners", default_value="4"),
            # z_sign=-1 flips Z to point out of the board surface (toward the camera).
            # Use +1 if you want Z to point into the board (into the table).
            DeclareLaunchArgument("charuco_z_sign", default_value="1.0"),
            DeclareLaunchArgument("charuco_frame_id", default_value="charuco_board"),
            DeclareLaunchArgument("window_size", default_value="7"),
            DeclareLaunchArgument("min_depth_m", default_value="0.15"),
            DeclareLaunchArgument("max_depth_m", default_value="3.0"),
            # Known pose of the ArUco board in robot base_link frame.
            # Measure this once with a ruler/CAD and set it at launch time.
            # Position in metres, rotation as intrinsic RPY in radians.
            DeclareLaunchArgument("board_in_base_x",     default_value="-0.224"),
            DeclareLaunchArgument("board_in_base_y",     default_value="-0.348"),
            DeclareLaunchArgument("board_in_base_z",     default_value="-0.030"),
            DeclareLaunchArgument("board_in_base_roll",  default_value="3.14159 "),
            DeclareLaunchArgument("board_in_base_pitch", default_value="0.0"),
            DeclareLaunchArgument("board_in_base_yaw",   default_value="1.5708"),
            # Z offset (metres) added to base_link points before returning to the planner.
            # Use a small positive value to lift points above the table surface.
            DeclareLaunchArgument("z_offset_m", default_value="0.01"),
            Node(
                package="vlm_camera_service",
                executable="camera_services_node",
                name="camera_services_node",
                output="screen",
                parameters=[
                    {
                        "color_topic": color_topic,
                        "depth_topic": depth_topic,
                        "camera_info_topic": camera_info_topic,
                        "pixel_debug_topic": pixel_debug_topic,
                        "charuco_enabled": charuco_enabled,
                        "charuco_dictionary": charuco_dictionary,
                        "charuco_squares_x": charuco_squares_x,
                        "charuco_squares_y": charuco_squares_y,
                        "charuco_square_length_m": charuco_square_length_m,
                        "charuco_marker_length_m": charuco_marker_length_m,
                        "charuco_axis_length_m": charuco_axis_length_m,
                        "charuco_min_corners": charuco_min_corners,
                        "charuco_z_sign": charuco_z_sign,
                        "charuco_frame_id": charuco_frame_id,
                        "window_size": window_size,
                        "min_depth_m": min_depth_m,
                        "max_depth_m": max_depth_m,
                        "board_in_base_x":     board_in_base_x,
                        "board_in_base_y":     board_in_base_y,
                        "board_in_base_z":     board_in_base_z,
                        "board_in_base_roll":  board_in_base_roll,
                        "board_in_base_pitch": board_in_base_pitch,
                        "board_in_base_yaw":   board_in_base_yaw,
                        "z_offset_m":          z_offset_m,
                    }
                ],
            ),
            Node(
                package="vlm_camera_service",
                executable="pixel_overlay_viewer_node",
                name="pixel_overlay_viewer_node",
                output="screen",
                condition=IfCondition(show_overlay),
                parameters=[
                    {
                        "color_topic": color_topic,
                        "camera_info_topic": camera_info_topic,
                        "pixel_topic": pixel_debug_topic,
                        "charuco_enabled": charuco_enabled,
                        "charuco_dictionary": charuco_dictionary,
                        "charuco_squares_x": charuco_squares_x,
                        "charuco_squares_y": charuco_squares_y,
                        "charuco_square_length_m": charuco_square_length_m,
                        "charuco_marker_length_m": charuco_marker_length_m,
                        "charuco_axis_length_m": charuco_axis_length_m,
                        "charuco_min_corners": charuco_min_corners,
                        "charuco_z_sign": charuco_z_sign,
                    }
                ],
            ),
        ]
    )
