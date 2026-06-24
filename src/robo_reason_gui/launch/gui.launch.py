from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='robo_reason_gui',
            executable='gui_node',
            name='gui_bridge_node',
            output='screen',
        ),
    ])
