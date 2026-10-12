"""Tests for the Gazebo world generator (world_builder.py)."""
import json
import xml.etree.ElementTree as ET

import numpy as np

from robo_reason_bringup.interfaces import load_interfaces
from robo_reason_sim.world_builder import (
    bridge_config, build_world_sdf, camera_optical_pose, default_scene_path,
    graspable_objects, load_sim_config, rpy_to_matrix,
)

SCENE = json.loads(default_scene_path().read_text())
CFG = load_sim_config()


def test_downward_camera_optical_frame_matches_the_rendered_image():
    """Looking straight down, image right is +x and image down is -y (checked on a real render)."""
    *_, roll, pitch, yaw = camera_optical_pose(CFG)
    R = rpy_to_matrix(roll, pitch, yaw)
    np.testing.assert_allclose(R[:, 0], [1, 0, 0], atol=1e-4)    # optical x -> world +x
    np.testing.assert_allclose(R[:, 1], [0, -1, 0], atol=1e-4)   # optical y -> world -y
    np.testing.assert_allclose(R[:, 2], [0, 0, -1], atol=1e-4)   # optical z -> down


def test_world_contains_the_scene_and_nothing_twice():
    world = ET.fromstring(build_world_sdf(SCENE, CFG, 'sim')).find('world')
    models = [m.get('name') for m in world.findall('model')]
    assert len(models) == len(set(models))
    assert {'table', 'camera', *graspable_objects(SCENE)} <= set(models)
    # The tray is raised and gets a box; the flat 'table' zone does not.
    assert 'zone_brown_top' in models and 'zone_table' not in models


def test_objects_rest_on_the_table_at_their_scene_xy():
    world = ET.fromstring(build_world_sdf(SCENE, CFG, 'sim')).find('world')
    surface_z = SCENE['workspace']['table']['surface_z']
    for name, obj in graspable_objects(SCENE).items():
        model = next(m for m in world.findall('model') if m.get('name') == name)
        x, y, z = (float(v) for v in model.find('pose').text.split()[:3])
        assert (x, y) == (obj['position'][0], obj['position'][1])
        assert abs(z - (surface_z + obj['size'][2] / 2)) < 1e-9


def test_bridges_publish_the_camera_where_the_sim_profile_expects_it():
    names = load_interfaces('sim')
    cubes = sorted(graspable_objects(SCENE))
    entries = bridge_config(CFG, names, 'sim', cubes)
    ros_topics = {e['ros_topic_name'] for e in entries}
    assert {'/sim/camera/color/image_raw', '/sim/camera/depth/image_raw',
            '/sim/camera/color/camera_info', '/sim/ground_truth/poses'} <= ros_topics
    assert len(entries) == 4 + 2 * len(cubes)
