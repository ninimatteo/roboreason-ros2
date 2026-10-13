"""UR5 skill set definition for RoboReason ROS2.

Replaces the CoppeliaSim-era Navigate/Pick/Place set with the real-robot
approach/pick/release primitives defined in the KickOff meeting.
"""


class UR5Skills:
    """Skills available to the UR5cb robot arm with OnRobot RG2 gripper."""

    # What each skill does and which of its parameters the system recomputes
    # (ROBOAI-20). Parameters the executor never read (come_back,
    # grasp_axis) were dropped: they promised behaviour that did not happen.
    skills = """
    Skills:
    - approach: [target_position: list[float], offset: float, approach_direction: str]
      Move the gripper to a hover point near the target, before a pick or a release.
      - target_position: [x, y, z] in robot base frame (meters), the point the next pick or release is aimed at
      - offset: hover distance from that point in meters (default 0.1)
      - approach_direction: 'z' (from above, standard), 'x' or 'y' (from the side)

    - pick: [target_position: list[float], grasp_width: float]
      Open the gripper, move straight down to target_position, close the gripper, then lift straight
      back to the approach hover point.
      - target_position: [x, y, z] in robot base frame (meters): the grasp contact point, i.e. the
        object's `position` in the scene
      - grasp_width: real-world width of the object in meters (its size[0]). The gripper fingers pivot,
        so the executor uses it to pick the right fingertip offset. 0.0 if unknown.

    - release: [release_position: list[float], object_height: float]
      Move straight down to release_position, open the gripper, then lift straight back to the
      approach hover point.
      - release_position: [x, y, z] in robot base frame (meters): the point on the surface where the
        object will rest. The system recomputes z from the scene (a zone's top, the table surface, or
        the top of the object below), so only x and y really depend on you.
      - object_height: height of the held object in meters (its size[2]). The executor raises the
        gripper by this amount so the object's bottom, not the gripper, meets the surface. The system
        replaces it with the measured grasp height when it can; your value is the fallback.

    - move_home: []
      Move the arm to its home (rest) configuration, out of the way.

    - wait: [time: float]
      Pause for `time` seconds (>= 0).

    Parameter notes:
    - Positions are [x, y, z] in the robot base frame (meters). The scene gives the reachable box
      (workspace.limits) and the table surface height (workspace.table.surface_z).
    - Standard pick-and-place: approach -> pick -> approach(target) -> release -> move_home.
      Always approach before a pick and before a release.
    - Several objects released at the same spot of a zone are spread over the zone by the system.
    """

    vlm_skills = """
    Skills:
    - approach: [target_position: list[float], offset: float, approach_direction: str]
      Move the gripper to a hover point near the target, before a pick or a release.
      - target_position: [x, y], center pixel of the object or surface the next pick or release is aimed at
      - offset: hover distance from that point in meters (default 0.1)
      - approach_direction: 'z' (from above, standard), 'x' or 'y' (from the side)

    - pick: [target_position: list[float], grasp_width: float]
      Open the gripper, move straight down to the object, close the gripper, then lift straight back to
      the approach hover point. The system measures the object's height from the depth camera and grasps
      it partway down its body.
      - target_position: [x, y], center pixel of the object to grasp
      - grasp_width: your visual estimate of the object's real-world width in meters (e.g. 0.03 thin
        block, 0.05 cube, 0.08 cup). The gripper fingers pivot, so the executor uses it to pick the right
        fingertip offset. 0.0 if unknown.

    - release: [release_position: list[float], object_height: float]
      Move straight down to the target surface, open the gripper, then lift straight back to the
      approach hover point.
      - release_position: [x, y], center pixel of the target surface, or of the object to stack on
      - object_height: estimated real-world height of the held object in meters (e.g. 0.05 small block,
        0.10 cup). The system replaces it with the height measured from depth when it can; your value is
        the fallback.

    - move_home: []
      Move the arm to its home (rest) configuration, out of the way.

    - wait: [time: float]
      Pause for `time` seconds (>= 0).

    Parameter notes:
    - Positions are pixel coordinates [x, y]: x = column from the left edge, y = row from the top edge.
    - Point at the CENTER of the object or area, not a corner or an edge.
    - Standard pick-and-place: approach -> pick -> approach(target) -> release -> move_home.
      Always approach before a pick and before a release.
    """

    action_example_placeholder = """
    "action_name": "<approach | pick | release | move_home | wait>",
    "target_position": [x, y, z],
    "release_position": [x, y, z],
    "object_height": 0.0,
    "grasp_width": 0.0,
    "offset": 0.1,
    "approach_direction": "<z | x | y>",
    "time": 0.0
    """

    vlm_action_example_placeholder = """
    "action_name": "<approach | pick | release | move_home | wait>",
    "target_position": [x, y],
    "release_position": [x, y],
    "object_height": 0.0,
    "grasp_width": 0.0,
    "offset": 0.1,
    "approach_direction": "<z | x | y>",
    "time": 0.0
    """

    vlm_skills_bbox = """
    Skills:
    - approach: [target_position: list[float], offset: float, approach_direction: str]
      Move the gripper to a hover point near the target, before a pick or a release.
      - target_position: [x_min, y_min, x_max, y_max], tightest pixel box around the object or surface
        the next pick or release is aimed at
      - offset: hover distance in meters (default 0.1)
      - approach_direction: 'z' (from above, standard), 'x' or 'y' (from the side)

    - pick: [target_position: list[float], grasp_width: float]
      Open the gripper, move straight down to the object, close the gripper, then lift straight back to
      the approach hover point. The system measures the object's height from the depth camera.
      - target_position: [x_min, y_min, x_max, y_max], tightest pixel box around the object to grasp
      - grasp_width: visual estimate of the object's real-world width in meters. The system measures it
        from your box when it can; your value is the fallback.

    - release: [release_position: list[float], object_height: float]
      Move straight down to the target surface, open the gripper, then lift straight back to the
      approach hover point.
      - release_position: [x_min, y_min, x_max, y_max], tightest pixel box around the target surface,
        or around the object to stack on
      - object_height: estimated real-world height of the held object in meters. The system replaces it
        with the height measured from depth when it can; your value is the fallback.

    - move_home: []
      Move the arm to its home (rest) configuration, out of the way.

    - wait: [time: float]
      Pause for `time` seconds (>= 0).

    Parameter notes:
    - All positions are boxes [x_min, y_min, x_max, y_max] in pixel coordinates.
    - Draw the tightest box that contains only the target: no background, no neighbouring objects.
    - Standard pick-and-place: approach -> pick -> approach(target) -> release -> move_home.
      Always approach before a pick and before a release.
    """

    vlm_action_example_placeholder_bbox = """
    "action_name": "<approach | pick | release | move_home | wait>",
    "target_position": [x_min, y_min, x_max, y_max],
    "release_position": [x_min, y_min, x_max, y_max],
    "object_height": 0.0,
    "grasp_width": 0.0,
    "offset": 0.1,
    "approach_direction": "<z | x | y>",
    "time": 0.0
    """

    eos_example_placeholder = """
    "action_name": "move_home"
    """

    @staticmethod
    def get_skills(use_vlm: bool = False) -> str:
        if use_vlm:
            return UR5Skills.vlm_skills.strip()
        return UR5Skills.skills.strip()

    @staticmethod
    def get_action_example(use_vlm: bool = False) -> str:
        if use_vlm:
            return UR5Skills.vlm_action_example_placeholder.strip()
        return UR5Skills.action_example_placeholder.strip()

    @staticmethod
    def get_eos_example() -> str:
        return UR5Skills.eos_example_placeholder.strip()

    @staticmethod
    def get_embodiment_data(use_vlm: bool = False, grounding_mode: str = 'point') -> tuple:
        """Return (skills_str, action_placeholder_str) for use in reasoning methods."""
        if use_vlm and grounding_mode == 'bbox':
            return (
                UR5Skills.vlm_skills_bbox.strip(),
                UR5Skills.vlm_action_example_placeholder_bbox.strip(),
            )
        return UR5Skills.get_skills(use_vlm), UR5Skills.get_action_example(use_vlm)
