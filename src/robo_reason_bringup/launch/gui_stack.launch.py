"""
Launch file: gui_stack.launch.py

Composed stack used by the web GUI (B1 orchestration). Unlike the hand-written
dry_run / real_robot launch files, this one exposes three independent mock axes
so the operator can mix real and simulated pieces:

  mode         LLM | VLM | VLM_LLM | AGENT — which planner to run (VLM_LLM: VLM
                                       scene grounding, then LLM planning against
                                       it; AGENT: closed loop agent_node, which
                                       also replaces the plan manager)
  use_mock_llm true | false         — mock the *reasoning* (hardcoded plan, no API)
  mock_robot   true | false         — fake skill executor vs the real UR5 driver
  mock_camera  true | false         — PNG mock camera vs the real camera services
                                       (VLM/VLM_LLM only; ignored in LLM mode)

The task_interface CLI node is never launched here — the GUI replaces it.

Example:
  ros2 launch robo_reason_bringup gui_stack.launch.py \\
    mode:=VLM mock_robot:=true mock_camera:=false
"""

import json
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.conditions import (
    IfCondition, LaunchConfigurationEquals, LaunchConfigurationNotEquals, UnlessCondition,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, PushRosNamespace, SetRemap

from robo_reason_bringup.config import settings
from robo_reason_bringup.interfaces import load_interfaces


def _in_profile_scope(context, actions):
    """Run the stack under the selected profile: its ROS namespace, and
    ROBOREASON_PROFILE exported so every node reads the same names.

    The namespace is what lets a real and a simulated stack run side by side
    (see interfaces.yaml).
    """
    profile = LaunchConfiguration('profile').perform(context)
    namespace = load_interfaces(profile).namespace
    scope = [SetEnvironmentVariable('ROBOREASON_PROFILE', profile)]
    if namespace:
        # TF is global by default; keep it in the namespace like everything else.
        scope += [PushRosNamespace(namespace),
                  SetRemap('/tf', 'tf'), SetRemap('/tf_static', 'tf_static')]
    if profile == 'sim' and not settings.FIXED_CAMERA_POSE:
        # The simulated camera's pose is known exactly: no ChArUco calibration.
        from robo_reason_sim.world_builder import camera_optical_pose, load_sim_config
        pose = camera_optical_pose(load_sim_config())
        scope.append(SetEnvironmentVariable('ROBOREASON_FIXED_CAMERA_POSE', json.dumps(pose)))
    return [GroupAction(scope + actions)]


WRIST_CAMERA_FRAME = 'wrist_camera_optical_frame'


def _wrist_cameras(context):
    """Camera services for the wrist camera (ROBOAI-47), per settings.CAMERAS.

    'fixed+wrist': a second camera_services_node, RGB only, whose topics and
    services carry a wrist/ prefix (wrist/camera/get_image, ...): its pose
    comes from TF at every request and pixels are deprojected onto the table
    plane. It stays in the stack's namespace, so it reads the stack's TF. 'wrist': the wrist camera (with depth) is the primary one,
    under the usual names. On the real robot the camera's mount is published
    as a static TF from tool0 and, for 'fixed+wrist', the RGB frames come
    from wrist_rgb_camera_node (plain V4L2); in simulation Gazebo has both.
    """
    if 'wrist' not in settings.CAMERAS:
        return []
    import json as _json
    from robo_reason_bringup.scenes import scene_path
    profile = LaunchConfiguration('profile').perform(context)
    names = load_interfaces(profile)
    alone = settings.CAMERAS == 'wrist'
    params = {'pose_tf_frame': WRIST_CAMERA_FRAME, 'charuco_enabled': False}
    if not alone:
        surface_z = _json.loads(scene_path().read_text())['workspace']['table']['surface_z']
        params['plane_z'] = float(surface_z)
        for param, name in (('color_topic', names.color), ('depth_topic', names.depth),
                            ('camera_info_topic', names.camera_info),
                            ('get_image_service', names.get_image), ('deproject_service', names.deproject),
                            ('pixel_debug_topic', names.pixel_debug), ('charuco_axis_topic', names.charuco_axis),
                            ('calibration_status_topic', names.calibration_status),
                            ('recalibrate_service', names.recalibrate)):
            params[param] = f'wrist/{name}'
    actions = [Node(package='vlm_camera_service', executable='camera_services_node',
                    name='camera_services_node' if alone else 'wrist_camera_services_node',
                    output='screen', parameters=[params])]
    if profile != 'sim':
        x, y, z, roll, pitch, yaw = settings.WRIST_CAMERA_MOUNT
        static = 'static_transform_publisher'
        actions += [
            Node(package='tf2_ros', executable=static, name='wrist_camera_mount',
                 arguments=['--x', str(x), '--y', str(y), '--z', str(z), '--roll', str(roll),
                            '--pitch', str(pitch), '--yaw', str(yaw),
                            '--frame-id', 'tool0', '--child-frame-id', 'wrist_camera_link']),
            Node(package='tf2_ros', executable=static, name='wrist_camera_optical',
                 arguments=['--roll', '-1.5708', '--yaw', '-1.5708',
                            '--frame-id', 'wrist_camera_link', '--child-frame-id', WRIST_CAMERA_FRAME]),
        ]
        if not alone:
            actions.append(Node(
                package='vlm_camera_service', executable='wrist_rgb_camera_node', output='screen',
                parameters=[{'device': settings.WRIST_CAMERA_DEVICE, 'horizontal_fov': settings.WRIST_CAMERA_HFOV,
                             'intrinsics': list(settings.WRIST_CAMERA_INTRINSICS) or [0.0],
                             'frame_id': WRIST_CAMERA_FRAME, 'color_topic': f'wrist/{names.color}',
                             'camera_info_topic': f'wrist/{names.camera_info}'}]))
    return actions


def generate_launch_description():
    mode = LaunchConfiguration('mode')
    use_mock_llm = LaunchConfiguration('use_mock_llm')
    mock_robot = LaunchConfiguration('mock_robot')
    mock_camera = LaunchConfiguration('mock_camera')
    reasoning_method = LaunchConfiguration('reasoning_method')
    model_name = LaunchConfiguration('model_name')
    temperature = LaunchConfiguration('temperature')
    vlm_model_name = LaunchConfiguration('vlm_model_name')
    vlm_temperature = LaunchConfiguration('vlm_temperature')
    grounding_mode = LaunchConfiguration('grounding_mode')
    reasoning_effort = LaunchConfiguration('reasoning_effort')
    robot_ip = LaunchConfiguration('robot_ip')
    images_dir = LaunchConfiguration('images_dir')

    # Camera nodes exist in the VLM, VLM_LLM and AGENT modes; combine that with mock_camera.
    vlm_mock_camera = PythonExpression([
        "'", mode, "' in ('VLM', 'VLM_LLM', 'AGENT') and '", mock_camera, "' == 'true'",
    ])
    vlm_real_camera = PythonExpression([
        "'", mode, "' in ('VLM', 'VLM_LLM', 'AGENT') and '", mock_camera, "' == 'false'",
    ])

    camera_services_launch = os.path.join(
        get_package_share_directory('vlm_camera_service'),
        'launch', 'camera_services.launch.py',
    )

    actions = [
        DeclareLaunchArgument('mode', default_value='LLM'),
        DeclareLaunchArgument('use_mock_llm', default_value='true'),
        DeclareLaunchArgument('mock_robot', default_value='true'),
        DeclareLaunchArgument('mock_camera', default_value='true'),
        DeclareLaunchArgument('reasoning_method', default_value='fhp'),
        DeclareLaunchArgument('model_name', default_value='groq/qwen3.6-27b'),
        DeclareLaunchArgument('temperature', default_value='0.1'),
        DeclareLaunchArgument('vlm_model_name', default_value='groq/qwen3.6-27b'),
        DeclareLaunchArgument('vlm_temperature', default_value='0.1'),
        DeclareLaunchArgument('grounding_mode', default_value='point'),
        DeclareLaunchArgument('reasoning_effort', default_value=''),
        DeclareLaunchArgument('robot_ip', default_value='192.168.2.60'),
        DeclareLaunchArgument('images_dir', default_value='/root/ws/src/mock_frames'),

        # The AGENT segments objects from raw depth itself (detect_objects);
        # the camera's own snapping onto object top planes inflates every
        # object by the snapping radius there, so it is off in that mode.
        SetEnvironmentVariable('ROBOREASON_LOCAL_PLANE_CENTROID_ENABLED', 'false',
                               condition=LaunchConfigurationEquals('mode', 'AGENT')),

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
                'grounding_mode': grounding_mode,
                'reasoning_effort': reasoning_effort,
            }],
        ),
        Node(
            package='robo_reason_planner',
            executable='vlm_llm_planner_node',
            name='vlm_llm_planner_node',
            output='screen',
            condition=LaunchConfigurationEquals('mode', 'VLM_LLM'),
            parameters=[{
                'reasoning_method': reasoning_method,
                'model_name': model_name,
                'temperature': temperature,
                'vlm_model_name': vlm_model_name,
                'vlm_temperature': vlm_temperature,
                'reasoning_effort': reasoning_effort,
            }],
        ),

        # Closed loop agent: plans AND executes, so it replaces the plan
        # manager too (it serves execute_plan and cancel_execution itself).
        Node(
            package='robo_reason_planner',
            executable='agent_node',
            name='agent_node',
            output='screen',
            condition=LaunchConfigurationEquals('mode', 'AGENT'),
            parameters=[{
                'model_name': model_name,
                'temperature': temperature,
                'reasoning_effort': reasoning_effort,
            }],
        ),

        # ── Plan manager ────────────────────────────────────────────────────
        Node(
            package='robo_reason_manager',
            executable='plan_manager_node',
            name='plan_manager_node',
            output='screen',
            parameters=[{'mode': mode}],
            condition=LaunchConfigurationNotEquals('mode', 'AGENT'),
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
            # with CAMERAS=wrist the wrist camera is the primary one instead
            condition=IfCondition(PythonExpression([
                vlm_real_camera, f" and '{settings.CAMERAS}' != 'wrist'"])),
        ),
        OpaqueFunction(function=_wrist_cameras, condition=IfCondition(vlm_real_camera)),
    ]

    declared = [a for a in actions if isinstance(a, DeclareLaunchArgument)]
    stack = [a for a in actions if not isinstance(a, DeclareLaunchArgument)]
    return LaunchDescription(declared + [
        DeclareLaunchArgument('profile', default_value=settings.PROFILE,
                              description='real | sim, see interfaces.yaml'),
        OpaqueFunction(function=_in_profile_scope, args=[stack]),
    ])
