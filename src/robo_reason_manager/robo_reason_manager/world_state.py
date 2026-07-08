"""World state tracker for the virtual scene."""
import json
import copy


class WorldState:
    """Maintains and updates the virtual state of the robot workspace."""

    def __init__(self, scene_json: str):
        self.state = json.loads(scene_json)
        if 'robot' not in self.state:
            self.state['robot'] = {'holding': None}

    def copy(self) -> 'WorldState':
        """Return a deep copy of this state (for plan validation simulation)."""
        new_ws = WorldState.__new__(WorldState)
        new_ws.state = copy.deepcopy(self.state)
        return new_ws

    def to_json(self) -> str:
        return json.dumps(self.state, indent=2)

    def robot_holding(self):
        """Return the id of the held object, or None."""
        return self.state.get('robot', {}).get('holding')

    def validate_workspace_position(self, position: list) -> bool:
        """Check whether a [x, y, z] position is within workspace limits."""
        limits = self.state.get('workspace', {}).get('limits', {})
        if not limits:
            return True
        x_lim = limits.get('x', [-10, 10])
        y_lim = limits.get('y', [-10, 10])
        z_lim = limits.get('z', [-10, 10])
        x, y, z = position[0], position[1], position[2]
        return (min(x_lim) <= x <= max(x_lim) and
                min(y_lim) <= y <= max(y_lim) and
                min(z_lim) <= z <= max(z_lim))

    def apply_skill_result(self, skill_name: str, args: dict):
        """Update world state after a skill executes successfully."""
        skill = skill_name.lower()

        if skill == 'pick':
            self.state['robot']['holding'] = 'object'

        elif skill == 'release':
            self.state['robot']['holding'] = None

        # approach, move_home, wait do not change the logical world state
