"""
Launch file: gui_stack.launch.py

Composed stack used by the web GUI (B1 orchestration). Unlike the hand-written
dry_run / real_robot launch files, this one exposes three independent mock axes
so the operator can mix real and simulated pieces:

  mode         LLM | VLM            — which planner to run
  use_mock_llm true | false         — mock the *reasoning* (hardcoded plan, no API)
  mock_robot   true | false         — fake skill executor vs the real UR5 driver
  mock_camera  true | false         — PNG mock camera vs the real camera services
                                       (VLM mode only; ignored in LLM mode)

The task_interface CLI node is never launched here — the GUI replaces it.

Example:
  ros2 launch robo_reason_bringup gui_stack.launch.py \\
    mode:=VLM mock_robot:=true mock_camera:=false
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, LaunchConfigurationEquals, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description():
    mode = LaunchConfiguration('mode')
    use_mock_llm = LaunchConfiguration('use_mock_llm')
    mock_robot = LaunchConfiguration('mock_robot')
    mock_camera = LaunchConfiguration('mock_camera')
    reasoning_method = LaunchConfiguration('reasoning_method')
    model_name = LaunchConfiguration('model_name')
    temperature = LaunchConfiguration('temperature')
    robot_ip = LaunchConfiguration('robot_ip')
    images_dir = LaunchConfiguration('images_dir')

    # Camera nodes only exist in VLM mode; combine that with the mock_camera flag.
    vlm_mock_camera = PythonExpression(
        ["'", mode, "' == 'VLM' and '", mock_camera, "' == 'true'"]
    )
    vlm_real_camera = PythonExpression(
        ["'", mode, "' == 'VLM' and '", mock_camera, "' == 'false'"]
    )

    camera_services_launch = os.path.join(
        get_package_share_directory('vlm_camera_service'),
        'launch', 'camera_services.launch.py',
    )

    return LaunchDescription([
        DeclareLaunchArgument('mode', default_value='LLM'),
        DeclareLaunchArgument('use_mock_llm', default_value='true'),
        DeclareLaunchArgument('mock_robot', default_value='true'),
        DeclareLaunchArgument('mock_camera', default_value='true'),
        DeclareLaunchArgument('reasoning_method', default_value='fhp'),
        DeclareLaunchArgument('model_name', default_value='groq/llama4-scout-17b'),
        DeclareLaunchArgument('temperature', default_value='0.1'),
        DeclareLaunchArgument('robot_ip', default_value='192.168.2.60'),
        DeclareLaunchArgument('images_dir', default_value='/root/ws/src/mock_frames'),

        # ── Planner ─────────────────────────────────────────────────────────
        Node(
            package='robo_reason_planner',
            executable='llm_planner_node',
            name='llm_planner_node',
            output='screen',
            condition=LaunchConfigurationEquals('mode', 'LLM'),
            parameters=[{
                'use_mock_llm': use_mock_llm,
                'reasoning_method': reasoning_method,
                'model_name': model_name,
                'temperature': temperature,
            }],
        ),
        Node(
            package='robo_reason_planner',
            executable='vlm_planner_node',
            name='vlm_planner_node',
            output='screen',
            condition=LaunchConfigurationEquals('mode', 'VLM'),
            parameters=[{
                'reasoning_method': reasoning_method,
                'model_name': model_name,
                'temperature': temperature,
            }],
        ),

        # ── Plan manager ────────────────────────────────────────────────────
        Node(
            package='robo_reason_manager',
            executable='plan_manager_node',
            name='plan_manager_node',
            output='screen',
            parameters=[{'mode': mode}],
        ),

        # ── Skill executor: fake (mock robot) or real UR5 ───────────────────
        Node(
            package='robo_reason_executor',
            executable='fake_skill_executor_node',
            name='fake_skill_executor_node',
            output='screen',
            condition=IfCondition(mock_robot),
        ),
        Node(
            package='robo_reason_executor',
            executable='ur5_skill_executor_node',
            name='ur5_skill_executor_node',
            output='screen',
            condition=UnlessCondition(mock_robot),
            parameters=[{
                'robot_ip': robot_ip,
                'home_joints': [-1.9, -1.5708, -1.5708, -1.5708, 1.5708, 0.0],
            }],
        ),

        # ── Camera (VLM only): PNG mock or real camera services ─────────────
        Node(
            package='vlm_camera_service',
            executable='mock_camera_service_node',
            name='mock_camera_service_node',
            output='screen',
            condition=IfCondition(vlm_mock_camera),
            parameters=[{'images_dir': images_dir}],
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(camera_services_launch),
            condition=IfCondition(vlm_real_camera),
        ),
    ])
