"""
Launch file: real_robot.launch.py

Starts the full RoboReason stack for the real UR5 robot.

Mode selection:
  mode=LLM (default) → llm_planner_node + ur5_skill_executor_node
  mode=VLM           → vlm_planner_node + camera_services_node + ur5_skill_executor_node

Prerequisites (before launching):
  1. Load ec_with_gripper.urp on the pendant and press Play
  2. Launch the UR5 ROS2 driver in a separate terminal:
       ros2 launch ur_robot_driver ur_control.launch.py \\
         ur_type:=ur5 robot_ip:=192.168.2.60 reverse_ip:=192.168.2.80 \\
         use_fake_hardware:=false \\
         initial_joint_controller:=scaled_joint_trajectory_controller
  3. Export your API key before launching:
       export GROQ_API_KEY=gsk_...
  (VLM mode only: also launch camera_services.launch.py in a separate terminal)

Two-terminal usage (recommended for clean interactive I/O):
  Terminal 1: ros2 launch robo_reason_bringup real_robot.launch.py
  Terminal 2: ros2 run robo_reason_task_interface task_interface_node

Single-terminal usage:
  ros2 launch robo_reason_bringup real_robot.launch.py include_task_interface:=true

Parameters:
  mode                    LLM (default) or VLM
  robot_ip                UR5 robot IP (default: 192.168.2.60)
  use_mock_llm            Dry-run without API key, LLM mode only (default: false)
  reasoning_method        fhp | ffhp | react | cot_sc | tot | always_act | self_refine (default: fhp)
  model_name              groq/qwen3.6-27b | etc. (default: groq/qwen3.6-27b)
  temperature             LLM/VLM temperature (default: 0.1)
  include_task_interface  Launch the CLI node in this process (default: false)

  home_joints is NOT a launch argument — edit it directly in the executor Node
  parameters block inside this file.
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition, LaunchConfigurationEquals


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('mode', default_value='LLM',
                              description="Planner mode: 'LLM' (default) or 'VLM'"),
        DeclareLaunchArgument('robot_ip', default_value='192.168.2.60',
                              description='UR5 robot IP address'),
        DeclareLaunchArgument('use_mock_llm', default_value='false',
                              description='Use mock planner (no API key needed, LLM mode only)'),
        DeclareLaunchArgument('reasoning_method', default_value='fhp',
                              description='Reasoning method'),
        DeclareLaunchArgument('model_name', default_value='groq/qwen3.6-27b',
                              description='LLM/VLM model name'),
        DeclareLaunchArgument('temperature', default_value='0.1',
                              description='Temperature (0.0 = deterministic)'),
        DeclareLaunchArgument('include_task_interface', default_value='false',
                              description='Also launch the interactive CLI node in this process'),

        # LLM planner — launched when mode=LLM
        Node(
            package='robo_reason_planner',
            executable='llm_planner_node',
            name='llm_planner_node',
            output='screen',
            condition=LaunchConfigurationEquals('mode', 'LLM'),
            parameters=[{
                'use_mock_llm': LaunchConfiguration('use_mock_llm'),
                'reasoning_method': LaunchConfiguration('reasoning_method'),
                'model_name': LaunchConfiguration('model_name'),
                'temperature': LaunchConfiguration('temperature'),
            }],
        ),

        # VLM planner — launched when mode=VLM
        Node(
            package='robo_reason_planner',
            executable='vlm_planner_node',
            name='vlm_planner_node',
            output='screen',
            condition=LaunchConfigurationEquals('mode', 'VLM'),
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
            parameters=[{'mode': LaunchConfiguration('mode')}],
        ),

        Node(
            package='robo_reason_executor',
            executable='ur5_skill_executor_node',
            name='ur5_skill_executor_node',
            output='screen',
            parameters=[{
                'robot_ip': LaunchConfiguration('robot_ip'),
                # Edit these joint angles (radians) to set the robot home position.
                # The EE should face the ChArUco board so the camera can calibrate at startup.
                'home_joints': [-1.9, -1.5708, -1.5708, -1.5708, 1.5708, 0.0],
            }],
        ),

        Node(
            package='robo_reason_task_interface',
            executable='task_interface_node',
            name='task_interface_node',
            output='screen',
            condition=IfCondition(LaunchConfiguration('include_task_interface')),
        ),
    ])
