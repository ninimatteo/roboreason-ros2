"""
Launch file: sim.launch.py

The Gazebo virtual test environment (ROBOAI-33). Starts, all in the namespace
of the 'sim' profile of interfaces.yaml:

  - Gazebo with the world generated from scene_mock.json (table, tray, cubes)
    and a fixed RGB-D camera looking down at the table
  - the UR5, driven through gz_ros2_control by a joint trajectory controller
  - ros_gz_bridge for the camera, the ground-truth poses and the gripper
  - gripper_shim_node, serving the same set_io service as the UR driver

This replaces the UR driver and the camera driver only. The RoboReason stack
itself runs on top of it unchanged, with the sim profile:

  ros2 launch robo_reason_sim sim.launch.py
  ros2 launch robo_reason_bringup gui_stack.launch.py profile:=sim \\
      mode:=VLM mock_robot:=false mock_camera:=false

Arguments:
  gui    true | false   show the Gazebo window (default true)
  scene  path           scene JSON to build the world from (default: the
                        robo_reason_task_interface scene_mock.json)
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace

from robo_reason_bringup.config import settings
from robo_reason_bringup.gazebo_partition import gazebo_partition
from robo_reason_bringup.interfaces import load_interfaces
from robo_reason_sim.world_builder import (
    bridge_config, build_world_sdf, default_scene_path, detachable_joint_plugins,
    graspable_objects, load_sim_config, share_dir,
)

UR_JOINTS = ['shoulder_pan_joint', 'shoulder_lift_joint', 'elbow_joint',
             'wrist_1_joint', 'wrist_2_joint', 'wrist_3_joint']


def _robot_description(cfg, namespace, controllers_file, initial_positions_file, cubes):
    xacro_file = Path(get_package_share_directory('ur_description')) / 'urdf' / 'ur.urdf.xacro'
    urdf = subprocess.check_output([
        'xacro', str(xacro_file),
        f"ur_type:={cfg['robot']['ur_type']}", 'name:=ur', 'sim_ignition:=true',
        'safety_limits:=true',
        f'simulation_controllers:={controllers_file}',
        f'initial_positions_file:={initial_positions_file}',
    ], text=True)
    # Put the controller manager inside the sim namespace...
    plugin_tag = 'name="ign_ros2_control::IgnitionROS2ControlPlugin">'
    assert plugin_tag in urdf, 'ur_description no longer declares the ign_ros2_control plugin'
    urdf = urdf.replace(plugin_tag, plugin_tag + f'<ros><namespace>/{namespace}</namespace></ros>')
    # ...and let the gripper shim attach cubes to the wrist.
    return urdf.replace('</robot>', detachable_joint_plugins(cubes, namespace) + '\n</robot>')


def launch_setup(context):
    gui = LaunchConfiguration('gui').perform(context).lower() == 'true'
    scene_path = Path(LaunchConfiguration('scene').perform(context) or default_scene_path())

    names = load_interfaces('sim')
    namespace = names.namespace
    cfg = load_sim_config()
    scene = json.loads(scene_path.read_text())
    cubes = sorted(graspable_objects(scene))

    out = Path(tempfile.mkdtemp(prefix='roboreason_sim_'))
    world_file = out / 'world.sdf'
    world_file.write_text(build_world_sdf(scene, cfg, namespace))
    bridge_file = out / 'bridge.yaml'
    bridge_file.write_text(yaml.safe_dump(bridge_config(cfg, names, namespace, cubes)))
    # The arm starts at the executor's home pose rather than ur_description's.
    initial_file = out / 'initial_positions.yaml'
    initial_file.write_text(yaml.safe_dump(dict(zip(UR_JOINTS, settings.HOME_JOINTS))))

    robot_description = _robot_description(
        cfg, namespace, share_dir() / 'config' / 'ur_controllers.yaml', initial_file, cubes,
    )
    controller_manager = f'/{namespace}/controller_manager'
    # Inside the namespace, keep TF there too, so a real stack's TF can't mix in.
    tf_remaps = [('/tf', 'tf'), ('/tf_static', 'tf_static')]

    gz_args = f"-r -v 3 {'' if gui else '-s --headless-rendering '}{world_file}"
    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory('ros_gz_sim'), 'launch', 'gz_sim.launch.py')
        ),
        launch_arguments={'gz_args': gz_args}.items(),
    )

    def spawner(controller):
        return Node(
            package='controller_manager', executable='spawner', output='screen',
            arguments=[controller, '-c', controller_manager,
                       '--controller-manager-timeout', '120'],
        )

    sim_nodes = GroupAction([
        PushRosNamespace(namespace),
        Node(
            package='robot_state_publisher', executable='robot_state_publisher', output='log',
            parameters=[{'robot_description': robot_description, 'use_sim_time': True}],
            remappings=tf_remaps,
        ),
        Node(
            package='ros_gz_sim', executable='create', output='screen',
            arguments=['-string', robot_description, '-name', 'ur', '-allow_renaming', 'true',
                       '-Y', str(cfg['robot']['spawn_yaw'])],
        ),
        spawner('joint_state_broadcaster'),
        spawner('joint_trajectory_controller'),
        Node(
            package='ros_gz_bridge', executable='parameter_bridge', name='sim_bridge',
            output='screen', parameters=[{'config_file': str(bridge_file)}],
        ),
        Node(package='robo_reason_sim', executable='gripper_shim_node', output='screen'),
    ])

    # Gazebo resolves the UR meshes' package:// URIs through its resource path.
    share_root = str(Path(get_package_share_directory('ur_description')).parent)
    resource_path = os.pathsep.join(
        p for p in (share_root, os.environ.get('IGN_GAZEBO_RESOURCE_PATH', '')) if p
    )
    return [
        SetEnvironmentVariable('ROBOREASON_PROFILE', 'sim'),
        SetEnvironmentVariable('IGN_GAZEBO_RESOURCE_PATH', resource_path),
        # Gazebo's own transport, isolated per ROS domain unless set explicitly.
        SetEnvironmentVariable('IGN_PARTITION', gazebo_partition()),
        gazebo,
        Node(
            package='ros_gz_bridge', executable='parameter_bridge', name='sim_clock_bridge',
            arguments=['/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock'], output='log',
        ),
        sim_nodes,
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('gui', default_value='true'),
        DeclareLaunchArgument('scene', default_value=''),
        OpaqueFunction(function=launch_setup),
    ])
