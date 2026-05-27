"""
Launch file: dry_run.launch.py

Starts all nodes including the terminal interface.
Note: for interactive use, prefer the two-terminal approach:
  Terminal 1: ros2 launch robo_reason_real dry_run_services.launch.py
  Terminal 2: ros2 run robo_reason_real task_interface_node
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('use_mock_llm', default_value='true'),
        DeclareLaunchArgument('reasoning_method', default_value='fhp'),
        DeclareLaunchArgument('model_name', default_value='groq/llama4-scout-17b'),
        DeclareLaunchArgument('temperature', default_value='0.1'),

        Node(
            package='robo_reason_real',
            executable='llm_planner_node',
            name='llm_planner_node',
            output='screen',
            parameters=[{
                'use_mock_llm': LaunchConfiguration('use_mock_llm'),
                'reasoning_method': LaunchConfiguration('reasoning_method'),
                'model_name': LaunchConfiguration('model_name'),
                'temperature': LaunchConfiguration('temperature'),
            }]
        ),

        Node(
            package='robo_reason_real',
            executable='plan_manager_node',
            name='plan_manager_node',
            output='screen',
        ),

        Node(
            package='robo_reason_real',
            executable='fake_skill_executor_node',
            name='fake_skill_executor_node',
            output='screen',
        ),

        Node(
            package='robo_reason_real',
            executable='task_interface_node',
            name='task_interface_node',
            output='screen',
        ),
    ])
