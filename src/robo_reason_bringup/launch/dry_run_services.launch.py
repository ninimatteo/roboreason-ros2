"""
Launch file: dry_run_services.launch.py

Starts the three background service/action nodes:
  - llm_planner_node   (exposes /plan_task)
  - plan_manager_node  (exposes /execute_plan, uses /execute_skill)
  - fake_skill_executor_node  (exposes /execute_skill action server)

The task_interface_node (terminal I/O) is NOT started here.
Run it separately with:
  ros2 run robo_reason_task_interface task_interface_node

Usage:
  # Mock mode (no API key needed):
  ros2 launch robo_reason_bringup dry_run_services.launch.py

  # Real LLM mode (GROQ_API_KEY must be set):
  ros2 launch robo_reason_bringup dry_run_services.launch.py use_mock_llm:=false reasoning_method:=fhp
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'use_mock_llm',
            default_value='true',
            description='Use deterministic mock planner (no API key required)'
        ),
        DeclareLaunchArgument(
            'reasoning_method',
            default_value='fhp',
            description='Reasoning method: fhp, ffhp, react, cot_sc, tot, always_act, self_refine'
        ),
        DeclareLaunchArgument(
            'model_name',
            default_value='groq/llama4-scout-17b',
            description='LLM model name (e.g. groq/llama4-scout-17b, groq/llama3.3-70b)'
        ),
        DeclareLaunchArgument(
            'temperature',
            default_value='0.1',
            description='LLM temperature (0.0 = deterministic)'
        ),

        Node(
            package='robo_reason_planner',
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
            package='robo_reason_manager',
            executable='plan_manager_node',
            name='plan_manager_node',
            output='screen',
        ),

        Node(
            package='robo_reason_executor',
            executable='fake_skill_executor_node',
            name='fake_skill_executor_node',
            output='screen',
        ),
    ])
