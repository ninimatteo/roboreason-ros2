"""
Launch file: vlm_dry_run.launch.py

Starts the full VLM pipeline without any real hardware:
  - mock_camera_service_node  (serves PNG files, fakes deproject)
  - vlm_planner_node          (VLM agent, connects to mock camera)
  - plan_manager_node         (validates plan, no-ops skill calls in dry-run)
  - fake_skill_executor_node  (logs actions, no robot movement)
  - task_interface_node       (interactive CLI — optionally in a separate terminal)

Preparation:
  1. Place at least one .png image in the images_dir folder
     (default: /root/ws/src/mock_frames)
  2. Export your VLM API key, e.g.:
       export GROQ_API_KEY=gsk_...
  3. Launch:
       ros2 launch robo_reason_bringup vlm_dry_run.launch.py

Two-terminal usage (recommended):
  Terminal 1: ros2 launch robo_reason_bringup vlm_dry_run.launch.py
  Terminal 2: ros2 run robo_reason_task_interface task_interface_node

Parameters:
  reasoning_method        fhp | ffhp | react | cot_sc | tot | always_act | self_refine (default: fhp)
  model_name              groq/qwen3.6-27b | nebius/qwen3-2.5-70b | etc. (default: groq/qwen3.6-27b)
  temperature             LLM temperature (default: 0.1)
  images_dir              Path to folder with .png mock frames (default: /root/ws/src/mock_frames)
  include_task_interface  Launch the CLI node in this process (default: false)
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('reasoning_method', default_value='fhp'),
        DeclareLaunchArgument('model_name', default_value='groq/qwen3.6-27b'),
        DeclareLaunchArgument('temperature', default_value='0.1'),
        DeclareLaunchArgument('images_dir', default_value='/root/ws/src/mock_frames'),
        DeclareLaunchArgument('include_task_interface', default_value='false'),

        Node(
            package='vlm_camera_service',
            executable='mock_camera_service_node',
            name='mock_camera_service_node',
            output='screen',
            parameters=[{
                'images_dir': LaunchConfiguration('images_dir'),
            }],
        ),

        Node(
            package='robo_reason_planner',
            executable='vlm_planner_node',
            name='vlm_planner_node',
            output='screen',
            parameters=[{
                'reasoning_method': LaunchConfiguration('reasoning_method'),
                'model_name': LaunchConfiguration('model_name'),
                'temperature': LaunchConfiguration('temperature'),
            }],
        ),

        Node(
            package='robo_reason_manager',
            executable='plan_manager_node',
            name='plan_manager_node',
            output='screen',
            parameters=[{'mode': 'VLM'}],
        ),

        Node(
            package='robo_reason_executor',
            executable='fake_skill_executor_node',
            name='fake_skill_executor_node',
            output='screen',
        ),

        Node(
            package='robo_reason_task_interface',
            executable='task_interface_node',
            name='task_interface_node',
            output='screen',
            condition=IfCondition(LaunchConfiguration('include_task_interface')),
        ),
    ])
