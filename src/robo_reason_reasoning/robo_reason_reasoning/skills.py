"""UR5 skill set definition for RoboReason ROS2.

Replaces the CoppeliaSim-era Navigate/Pick/Place set with the real-robot
approach/pick/release primitives defined in the KickOff meeting.
"""


class UR5Skills:
    """Skills available to the UR5cb robot arm with Robotiq 2F gripper."""

    skills = """
    Skills:
    - approach: [target_position: list[float], offset: float, approach_direction: str]
      Move the end-effector to a safe hovering position near the target before grasping or releasing.
      - target_position: [x, y, z] in robot base frame (meters)
      - offset: approach distance in meters (default: 0.1 m = 10 cm above)
      - approach_direction: 'z' (from above — standard), 'x' (from front), 'y' (from side)

    - pick: [target_position: list[float], grasp_axis: str, come_back: bool]
      Move the end-effector to the object and close the gripper to grasp it.
      - target_position: [x, y, z] in robot base frame (meters) — actual contact position
      - grasp_axis: final approach axis for closing: 'z' (top-down), 'x', 'y'
      - come_back: if true, return to the pre-grasp approach position after picking

    - release: [release_position: list[float], come_back: bool]
      Move the end-effector to release_position and open the gripper to deposit the object.
      - release_position: [x, y, z] in robot base frame (meters)
      - come_back: if true, return to previous position after releasing

    - move_home: []
      Move the robot arm to its home (rest) joint configuration. No parameters needed.

    - wait: [time: float]
      Pause execution for the given duration.
      - time: duration in seconds (must be >= 0)

    Parameter notes:
    - All positions are in the robot base frame (meters): [x, y, z]
    - x: lateral distance (positive = left, negative = right; range: -0.35 – 0.35 m)
    - y: distance along the table away from the robot base (reachable range: -0.15 – -0.85 m)
    - z: height above the table surface (z = 0.01 is the table surface)
    - Standard pick-and-place sequence: approach → pick → approach(target_zone) → release → move_home
    - Always call approach before pick and before release for safety clearance.
    """

    vlm_skills = """
    Skills:
    - approach: [target_position: list[float], offset: float, approach_direction: str]
      Move the end-effector to a safe hovering position near the target before grasping or releasing.
      - target_position: [h, w] in the image frame (top left corner)
      - offset: approach distance in meters (default: 0.1 m = 10 cm above)
      - approach_direction: 'z' (from above — standard), 'x' (from front), 'y' (from side)

    - pick: [target_position: list[float], grasp_axis: str, come_back: bool]
      Move the end-effector to the object and close the gripper to grasp it.
      - target_position: [h, w] in the image frame (top left corner)
      - grasp_axis: final approach axis for closing: 'z' (top-down), 'x', 'y'
      - come_back: if true, return to the pre-grasp approach position after picking

    - release: [release_position: list[float], come_back: bool]
      Move the end-effector to release_position and open the gripper to deposit the object.
      - release_position: [h, w] in the image frame (top left corner)
      - come_back: if true, return to previous position after releasing

    - move_home: []
      Move the robot arm to its home (rest) joint configuration. No parameters needed.

    - wait: [time: float]
      Pause execution for the given duration.
      - time: duration in seconds (must be >= 0)

    Parameter notes:
    - All positions are in the image frame (pixels): [h, w]
    - h: height pixel coordinate from top to bottom
    - w: width pixel coordinate from left to right
    - Standard pick-and-place sequence: approach → pick → approach(target_zone) → release → move_home
    - Always call approach before pick and before release for safety clearance.
    """

    action_example_placeholder = """
    "action_name": "<approach | pick | release | move_home | wait>",
    "target_position": [x, y, z],
    "release_position": [x, y, z],
    "offset": 0.1,
    "approach_direction": "<z | x | y>",
    "grasp_axis": "<z | x | y>",
    "come_back": true,
    "time": 0.0
    """

    vlm_action_example_placeholder = """
    "action_name": "<approach | pick | release | move_home | wait>",
    "target_position": [h, w],
    "release_position": [h, w],
    "offset": 0.1,
    "approach_direction": "<z | x | y>",
    "grasp_axis": "<z | x | y>",
    "come_back": true,
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
    def get_embodiment_data(use_vlm: bool = False) -> tuple:
        """Return (skills_str, action_placeholder_str) for use in reasoning methods."""
        return UR5Skills.get_skills(use_vlm), UR5Skills.get_action_example(use_vlm)
