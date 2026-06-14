"""
Launch file: real_robot.launch.py

Starts the full RoboReason stack for the real UR5 robot:
  - llm_planner_node   (real LLM via Groq)
  - plan_manager_node
  - ur5_skill_executor_node  (real robot, gripper via digital I/O)
  - task_interface_node  (interactive CLI — run in a separate terminal for clean I/O)

Prerequisites (before launching):
  1. Load ec_with_gripper.urp on the pendant and press Play
  2. Launch the UR5 ROS2 driver in a separate terminal:
       ros2 launch ur_robot_driver ur_control.launch.py \\
         ur_type:=ur5 robot_ip:=192.168.2.60 reverse_ip:=192.168.2.80 \\
         use_fake_hardware:=false \\
         initial_joint_controller:=scaled_joint_trajectory_controller
  3. Export GROQ_API_KEY in the terminal before launching:
       export GROQ_API_KEY=gsk_...

Two-terminal usage (recommended for clean interactive I/O):
  Terminal 1: ros2 launch robo_reason_bringup real_robot.launch.py
  Terminal 2: ros2 run robo_reason_task_interface task_interface_node

Single-terminal usage (all nodes including CLI):
  ros2 launch robo_reason_bringup real_robot.launch.py include_task_interface:=true

Parameters:
  mode                LLM (default) or VLM — planner mode
  robot_ip            UR5 robot IP (default: 192.168.2.60)
  use_mock_llm        Set true for dry-run without API key (default: false, LLM mode only)
  reasoning_method    fhp | ffhp | react | cot_sc | tot | always_act | self_refine (default: fhp)
  model_name          groq/llama4-scout-17b | groq/llama3.3-70b | groq/llama3.1-8b (default: groq/llama4-scout-17b)
  temperature         LLM/VLM temperature, 0.0 = deterministic (default: 0.1)
  tmp_dir             Directory for VLM frame cache (default: /tmp/roboreason_vlm)
  include_task_interface  Launch the CLI node in this process (default: false)
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('mode', default_value='LLM',
                              description="Planner mode: 'LLM' (default) or 'VLM'"),
        DeclareLaunchArgument('robot_ip', default_value='192.168.2.60',
                              description='UR5 robot IP address'),
        DeclareLaunchArgument('use_mock_llm', default_value='false',
                              description='Use mock planner (no API key needed, LLM mode only)'),
        DeclareLaunchArgument('reasoning_method', default_value='fhp',
                              description='Reasoning method: fhp, ffhp, react, cot_sc, tot, always_act, self_refine'),
        DeclareLaunchArgument('model_name', default_value='groq/llama4-scout-17b',
                              description='LLM/VLM model name'),
        DeclareLaunchArgument('temperature', default_value='0.1',
                              description='Temperature (0.0 = deterministic)'),
        DeclareLaunchArgument('tmp_dir', default_value='/root/ws/src/vlm_frames',
                              description='Directory for VLM frame cache (VLM mode only)'),
        DeclareLaunchArgument('include_task_interface', default_value='false',
                              description='Also launch the interactive CLI node in this process'),

        Node(
            package='robo_reason_planner',
            executable='llm_planner_node',
            name='llm_planner_node',
            output='screen',
            parameters=[{
                'mode': LaunchConfiguration('mode'),
                'use_mock_llm': LaunchConfiguration('use_mock_llm'),
                'reasoning_method': LaunchConfiguration('reasoning_method'),
                'model_name': LaunchConfiguration('model_name'),
                'temperature': LaunchConfiguration('temperature'),
                'tmp_dir': LaunchConfiguration('tmp_dir'),
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
