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
    charuco_frame_id = LaunchConfiguration("charuco_frame_id")
    window_size = LaunchConfiguration("window_size")
    min_depth_m = LaunchConfiguration("min_depth_m")
    max_depth_m = LaunchConfiguration("max_depth_m")

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
            DeclareLaunchArgument("charuco_squares_x", default_value="5"),
            DeclareLaunchArgument("charuco_squares_y", default_value="7"),
            DeclareLaunchArgument("charuco_square_length_m", default_value="0.03"),
            DeclareLaunchArgument("charuco_marker_length_m", default_value="0.015"),
            DeclareLaunchArgument("charuco_axis_length_m", default_value="0.08"),
            DeclareLaunchArgument("charuco_min_corners", default_value="4"),
            DeclareLaunchArgument("charuco_frame_id", default_value="charuco_board"),
            DeclareLaunchArgument("window_size", default_value="7"),
            DeclareLaunchArgument("min_depth_m", default_value="0.15"),
            DeclareLaunchArgument("max_depth_m", default_value="3.0"),
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
                        "charuco_frame_id": charuco_frame_id,
                        "window_size": window_size,
                        "min_depth_m": min_depth_m,
                        "max_depth_m": max_depth_m,
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
                    }
                ],
            ),
        ]
    )
