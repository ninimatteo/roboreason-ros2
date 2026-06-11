"""World state tracker for the virtual scene."""
import json
import copy
import math


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

    def to_dict(self) -> dict:
        return copy.deepcopy(self.state)

    def to_json(self) -> str:
        return json.dumps(self.state, indent=2)

    def get_object(self, object_id: str) -> dict:
        return self.state.get('objects', {}).get(object_id)

    def get_target(self, target_id: str) -> dict:
        return self.state.get('targets', {}).get(target_id)

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

    def find_object_near(self, position: list, tolerance: float = 0.08, xy_only: bool = False) -> str:
        """Return the id of the object closest to position (within tolerance), or None."""
        best_id = None
        best_dist = float('inf')
        for obj_id, obj in self.state.get('objects', {}).items():
            obj_pos = obj.get('position')
            if obj_pos is None:
                continue
            if xy_only:
                dist = math.sqrt((position[0] - obj_pos[0]) ** 2 + (position[1] - obj_pos[1]) ** 2)
            else:
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(position, obj_pos)))
            if dist < tolerance and dist < best_dist:
                best_dist = dist
                best_id = obj_id
        return best_id

    def apply_skill_result(self, skill_name: str, args: dict):
        """Update world state after a skill executes successfully."""
        skill = skill_name.lower()

        if skill == 'pick':
            target_pos = args.get('target_position')
            # Match on XY only: grasp Z is at the bottom of the object, not its centre
            obj_id = self.find_object_near(target_pos, xy_only=True) if target_pos else None
            if obj_id:
                self.state['objects'][obj_id]['state'] = 'held'
                self.state['objects'][obj_id]['position'] = None
                self.state['robot']['holding'] = obj_id

        elif skill == 'release':
            release_pos = args.get('release_position')
            obj_id = self.robot_holding()
            if obj_id and release_pos:
                obj = self.state['objects'][obj_id]
                height = obj.get('size', [0, 0, 0.05])[2]
                self.state['objects'][obj_id]['state'] = 'on_table'
                self.state['objects'][obj_id]['position'] = [
                    release_pos[0],
                    release_pos[1],
                    release_pos[2] + height / 2,
                ]
                self.state['robot']['holding'] = None

        # approach, move_home, wait do not change the logical world state
