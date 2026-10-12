"""
ROS interface names (topics, services, actions) for the RoboReason stack.

The names live in interfaces.yaml next to this file, so they can be edited
without touching code. Which profile applies (real robot or simulation) comes
from settings.PROFILE, i.e. the ROBOREASON_PROFILE env var.

  from robo_reason_bringup.interfaces import names
  self.create_client(PlanTask, names.plan_task)
"""

from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from robo_reason_bringup.config import settings

DEFAULT_FILE = Path(__file__).with_name('interfaces.yaml')


@dataclass(frozen=True)
class Interfaces:
    profile: str
    # Namespace the whole stack runs in for this profile ('' = root).
    namespace: str

    # Owned by the robot driver / simulator — differ per profile.
    joint_states: str
    trajectory_action: str
    gripper_io: str
    # Owned by the camera driver / simulator — differ per profile.
    color: str
    depth: str
    camera_info: str

    # Owned by RoboReason's own nodes — same in every profile.
    plan_task: str
    cancel_planning: str
    execute_plan: str
    cancel_execution: str
    execute_skill: str
    world_state: str
    execution_log: str
    terminal_logs: str
    get_image: str
    deproject: str
    recalibrate: str
    pixel_debug: str
    charuco_axis: str
    calibration_status: str
    reset_simulation: str

    @property
    def execute_skill_status(self) -> str:
        """Status topic of the execute_skill action server.

        Published once per server, so its publisher count equals the number
        of running skill executors (the GUI uses this as a liveness check).
        """
        return f'{self.execute_skill}/_action/status'


def load_interfaces(profile: str = None, path: Path = None) -> Interfaces:
    """Read the names for one profile from the YAML file."""
    profile = profile or settings.PROFILE
    path = Path(path or settings.INTERFACES_FILE or DEFAULT_FILE)
    data = yaml.safe_load(path.read_text())

    profiles = data['profiles']
    if profile not in profiles:
        raise ValueError(
            f"Unknown profile '{profile}' in {path}; available: {', '.join(profiles)}"
        )
    selected = profiles[profile]
    values = {
        'profile': profile,
        'namespace': selected.get('namespace') or '',
        **selected['robot'],
        **selected['camera'],
        **data['internal'],
    }
    missing = [f.name for f in fields(Interfaces) if f.name not in values]
    if missing:
        raise KeyError(f"{path} (profile '{profile}') is missing: {', '.join(missing)}")
    return Interfaces(**{f.name: values[f.name] for f in fields(Interfaces)})


names = load_interfaces()
