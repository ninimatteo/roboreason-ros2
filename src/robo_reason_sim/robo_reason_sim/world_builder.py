"""
Build the Gazebo world and its ROS bridges from scene_mock.json.

The simulation never keeps its own copy of the scene: tables, zones and
objects are generated from the same scene_mock.json the LLM mode plans on,
so the two can't drift apart.

Frames: the world frame is the UR controller "base" frame, the one the
executor's IK and scene_mock.json use (see robot.spawn_yaw in sim.yaml).
"""

import math
from pathlib import Path

import numpy as np
import yaml
from ament_index_python.packages import get_package_share_directory

# Colours of scene entries, as Gazebo RGB.
COLORS = {
    'red': (0.80, 0.08, 0.08),
    'blue': (0.10, 0.25, 0.85),
    'white': (0.95, 0.95, 0.95),
    # Deep orange: a lighter one renders yellowish and the VLM calls it yellow.
    'orange': (0.95, 0.30, 0.00),
    'brown': (0.45, 0.28, 0.12),
    'green': (0.10, 0.65, 0.20),
    'yellow': (0.95, 0.85, 0.10),
    'black': (0.05, 0.05, 0.05),
}
TABLE_COLOR = (0.82, 0.82, 0.80)
TABLE_THICKNESS_M = 0.04
# The table extends this far behind the robot base, so the base stands on it.
TABLE_BEHIND_BASE_M = 0.10
CUBE_MASS_KG = 0.05
# Zones rising less than this above the table are areas of the table itself.
ZONE_MIN_HEIGHT_M = 0.015


def share_dir() -> Path:
    return Path(get_package_share_directory('robo_reason_sim'))


def load_sim_config(path: Path = None) -> dict:
    return yaml.safe_load(Path(path or share_dir() / 'config' / 'sim.yaml').read_text())


def default_scene_path() -> Path:
    return Path(get_package_share_directory('robo_reason_task_interface')) / 'config' / 'scene_mock.json'


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """R = Rz(yaw) Ry(pitch) Rx(roll): the convention of both SDF and charuco_utils."""
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                              math.sin(pitch), math.cos(yaw), math.sin(yaw))
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def matrix_to_rpy(R: np.ndarray) -> tuple:
    """Inverse of rpy_to_matrix (away from the pitch = +-pi/2 singularity)."""
    pitch = math.asin(-max(-1.0, min(1.0, R[2, 0])))
    if abs(math.cos(pitch)) < 1e-6:
        # Gimbal lock: roll and yaw are coupled, fold everything into yaw.
        return 0.0, pitch, math.atan2(-R[0, 1], R[1, 1])
    return math.atan2(R[2, 1], R[2, 2]), pitch, math.atan2(R[1, 0], R[0, 0])


def camera_optical_pose(cfg: dict) -> list:
    """Pose of the camera *optical* frame in base, as [x, y, z, roll, pitch, yaw].

    Gazebo poses the camera body (looking along +x, image up = +z). The
    deprojection works in the optical frame (z forward, x right, y down), which
    is the body frame rotated by this fixed matrix.
    """
    x, y, z, roll, pitch, yaw = cfg['camera']['pose']
    body_to_optical = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], dtype=float)
    R = rpy_to_matrix(roll, pitch, yaw) @ body_to_optical
    return [x, y, z, *matrix_to_rpy(R)]


def graspable_objects(scene: dict) -> dict:
    return {name: obj for name, obj in scene.get('objects', {}).items() if obj.get('graspable')}


def object_rest_positions(scene: dict) -> dict:
    """Where each graspable object starts: on the table, centred on its scene x, y.

    Used both to build the world and to put the objects back on a reset.
    """
    surface_z = float(scene['workspace']['table']['surface_z'])
    return {
        name: (obj['position'][0], obj['position'][1], surface_z + obj['size'][2] / 2)
        for name, obj in graspable_objects(scene).items()
    }


def _material(rgb) -> str:
    r, g, b = rgb
    return (f'<material><ambient>{r} {g} {b} 1</ambient><diffuse>{r} {g} {b} 1</diffuse>'
            f'<specular>0.1 0.1 0.1 1</specular></material>')


def _box_model(name, center, size, rgb, static=True, mass=None) -> str:
    sx, sy, sz = size
    inertial = ''
    if mass is not None:
        ixx = mass / 12 * (sy ** 2 + sz ** 2)
        iyy = mass / 12 * (sx ** 2 + sz ** 2)
        izz = mass / 12 * (sx ** 2 + sy ** 2)
        inertial = (f'<inertial><mass>{mass}</mass><inertia><ixx>{ixx}</ixx><iyy>{iyy}</iyy>'
                    f'<izz>{izz}</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia></inertial>')
    geometry = f'<geometry><box><size>{sx} {sy} {sz}</size></box></geometry>'
    friction = '<surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface>'
    return f"""
    <model name="{name}">
      <static>{'true' if static else 'false'}</static>
      <pose>{center[0]} {center[1]} {center[2]} 0 0 0</pose>
      <link name="link">{inertial}
        <collision name="collision">{geometry}{friction}</collision>
        <visual name="visual">{geometry}{_material(rgb)}</visual>
      </link>
    </model>"""


def build_world_sdf(scene: dict, cfg: dict, namespace: str) -> str:
    table = scene['workspace']['table']
    limits = scene['workspace']['limits']
    surface_z = float(table['surface_z'])

    models = []
    # Table slab, top face at surface_z.
    tx = limits['x']
    ty = [limits['y'][0], TABLE_BEHIND_BASE_M]
    models.append(_box_model(
        'table',
        ((tx[0] + tx[1]) / 2, (ty[0] + ty[1]) / 2, surface_z - TABLE_THICKNESS_M / 2),
        (tx[1] - tx[0], ty[1] - ty[0], TABLE_THICKNESS_M),
        TABLE_COLOR,
    ))
    # Raised placement zones (the tray): a box from the table up to the zone top.
    for name, target in scene.get('targets', {}).items():
        b = target['bounds']
        top = float(b['z'][1])
        if top <= surface_z + ZONE_MIN_HEIGHT_M:
            continue  # flush with the table (e.g. the 'table' zone): nothing to build
        models.append(_box_model(
            f'zone_{name}',
            ((b['x'][0] + b['x'][1]) / 2, (b['y'][0] + b['y'][1]) / 2, (surface_z + top) / 2),
            (b['x'][1] - b['x'][0], b['y'][1] - b['y'][0], top - surface_z),
            COLORS.get(target.get('color'), TABLE_COLOR),
        ))
    # Objects rest on the table, centred on their scene x, y.
    rest = object_rest_positions(scene)
    for name, obj in graspable_objects(scene).items():
        size = obj['size']
        models.append(_box_model(
            name, rest[name], size,
            COLORS.get(obj.get('color'), (0.5, 0.5, 0.5)), static=False, mass=CUBE_MASS_KG,
        ))

    cam = cfg['camera']
    cx, cy, cz, cr, cp, cyaw = cam['pose']
    # Explicit intrinsics: Fortress' rgbd_camera otherwise publishes the
    # default 320x240 ones in camera_info whatever the image size, which
    # breaks deprojection.
    fx = cam['width'] / 2 / math.tan(cam['horizontal_fov'] / 2)
    camera = f"""
    <model name="camera">
      <static>true</static>
      <pose>{cx} {cy} {cz} {cr} {cp} {cyaw}</pose>
      <link name="link">
        <sensor name="rgbd" type="rgbd_camera">
          <topic>{camera_gz_topic(namespace)}</topic>
          <always_on>true</always_on>
          <update_rate>{cam['update_rate']}</update_rate>
          <camera>
            <horizontal_fov>{cam['horizontal_fov']}</horizontal_fov>
            <image><width>{cam['width']}</width><height>{cam['height']}</height><format>R8G8B8</format></image>
            <clip><near>{cam['near']}</near><far>{cam['far']}</far></clip>
            <lens><intrinsics>
              <fx>{fx}</fx><fy>{fx}</fy><cx>{cam['width'] / 2}</cx><cy>{cam['height'] / 2}</cy><s>0</s>
            </intrinsics></lens>
          </camera>
        </sensor>
      </link>
    </model>"""

    ground_z = surface_z - float(table.get('height', 0.76))
    return f"""<?xml version="1.0"?>
<sdf version="1.8">
  <world name="{cfg['world_name']}">
    <physics name="1ms" type="ignored">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>
    <plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
    <plugin filename="ignition-gazebo-user-commands-system" name="ignition::gazebo::systems::UserCommands"/>
    <plugin filename="ignition-gazebo-scene-broadcaster-system" name="ignition::gazebo::systems::SceneBroadcaster"/>
    <plugin filename="ignition-gazebo-sensors-system" name="ignition::gazebo::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>
    <scene><ambient>0.6 0.6 0.6 1</ambient><background>0.7 0.7 0.7 1</background></scene>
    <light type="directional" name="sun">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 3 0 0 0</pose>
      <diffuse>0.9 0.9 0.9 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.3 0.2 -1</direction>
    </light>
    <model name="ground">
      <static>true</static>
      <pose>0 0 {ground_z} 0 0 0</pose>
      <link name="link">
        <collision name="collision"><geometry><plane><normal>0 0 1</normal><size>10 10</size></plane></geometry></collision>
        <visual name="visual"><geometry><plane><normal>0 0 1</normal><size>10 10</size></plane></geometry>{_material((0.4, 0.4, 0.4))}</visual>
      </link>
    </model>{''.join(models)}{camera}
  </world>
</sdf>
"""


def detachable_joint_plugins(cube_names, namespace: str) -> str:
    """URDF <gazebo> blocks letting the shim attach/detach each cube to the wrist.

    tool0 and flange are merged into wrist_3_link when Gazebo converts the
    URDF (fixed-joint lumping), so wrist_3_link is the link to attach to.
    """
    blocks = []
    for cube in cube_names:
        blocks.append(f"""
  <gazebo>
    <plugin filename="ignition-gazebo-detachable-joint-system" name="ignition::gazebo::systems::DetachableJoint">
      <parent_link>wrist_3_link</parent_link>
      <child_model>{cube}</child_model>
      <child_link>link</child_link>
      <attach_topic>{gripper_topic(namespace, cube, 'attach')}</attach_topic>
      <detach_topic>{gripper_topic(namespace, cube, 'detach')}</detach_topic>
    </plugin>
  </gazebo>""")
    return ''.join(blocks)


def camera_gz_topic(namespace: str) -> str:
    """Gazebo topic prefix of the RGB-D sensor (image, depth_image, camera_info)."""
    return f'/{namespace}/camera'


def gripper_topic(namespace: str, cube: str, action: str) -> str:
    return f'/{namespace}/gripper/{cube}/{action}'


def bridge_config(cfg: dict, names, namespace: str, cube_names) -> list:
    """ros_gz_bridge entries: the camera and ground-truth poses out, gripper commands in."""
    def ros(name):
        return f'/{namespace}/{name}'

    cam = camera_gz_topic(namespace)
    entries = [
        (ros(names.color), f'{cam}/image', 'sensor_msgs/msg/Image', 'ignition.msgs.Image', 'GZ_TO_ROS'),
        (ros(names.depth), f'{cam}/depth_image', 'sensor_msgs/msg/Image', 'ignition.msgs.Image', 'GZ_TO_ROS'),
        (ros(names.camera_info), f'{cam}/camera_info', 'sensor_msgs/msg/CameraInfo',
         'ignition.msgs.CameraInfo', 'GZ_TO_ROS'),
        (ros('ground_truth/poses'), f"/world/{cfg['world_name']}/dynamic_pose/info",
         'tf2_msgs/msg/TFMessage', 'ignition.msgs.Pose_V', 'GZ_TO_ROS'),
    ]
    for cube in cube_names:
        for action in ('attach', 'detach'):
            topic = gripper_topic(namespace, cube, action)
            entries.append((topic, topic, 'std_msgs/msg/Empty', 'ignition.msgs.Empty', 'ROS_TO_GZ'))
    return [
        {'ros_topic_name': r, 'gz_topic_name': g, 'ros_type_name': rt,
         'gz_type_name': gt, 'direction': d}
        for r, g, rt, gt, d in entries
    ]
