"""The tools the agent can call, and what each one does.

Three kinds:
  perception  look, inspect_object, inspect_area, find_free_spot
              (deterministic geometry over the depth camera, see geometry.py)
  action      pick, place, move_home: each runs a short fixed sequence of
              executor skills and reports success or the executor's error
  bookkeeping submit_plan, update_plan, report_progress, finish

The backend (ROS in agent_node, a fake in the tests) provides the camera,
the deprojection and the skill executor:

  backend.capture() -> (image_path, width, height)
  backend.deproject([(u, v), ...]) -> [[x, y, z] or None, ...]
  backend.run_skill(name, args: dict) -> (ok: bool, error: str)
  backend.metres_per_pixel([x, y, z]) -> (along_u, along_v) or None
"""

import json
import math
from dataclasses import dataclass, field

from robo_reason_planner.agent import geometry

_BBOX = {
    'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4,
    'description': 'Tight bounding box [x_min, y_min, x_max, y_max] in image pixels '
                   '(x = column from the left, y = row from the top) of the latest image.',
}
_XYZ = {
    'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3,
    'description': '[x, y, z] in metres, robot base frame. Copy it from a perception tool result.',
}
_PLAN_STEPS = {
    'type': 'array',
    'items': {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'description': 'pick, place, move_home, check, ...'},
            'description': {'type': 'string', 'description': 'what this step does, e.g. "pick the red cube"'},
        },
        'required': ['action', 'description'],
    },
}


def _fn(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties or {}, 'required': required or []},
    }}


SUBMIT_PLAN = _fn(
    'submit_plan',
    'Submit your draft plan for the task: the ordered high level steps you expect to take. '
    'It is shown to the operator before anything moves. You can revise it later.',
    {'steps': _PLAN_STEPS, 'notes': {'type': 'string', 'description': 'assumptions or risks'}},
    ['steps'],
)

EXECUTION_TOOLS = [
    _fn('look', 'Take a fresh camera image of the workspace. It is attached right after the tool result.'),
    _fn('inspect_object',
        'Measure an object with the depth camera. Returns its top centre, height, width, '
        'the suggested grasp_point to pass to pick, and held_height_below_grasp_m to pass to place. '
        "looks_empty=true means nothing is standing there (useful to check if an object is still in place).",
        {'bbox': _BBOX}, ['bbox']),
    _fn('inspect_area',
        'Measure a placement area (tray, zone, table region) with the depth camera: '
        'its surface height and x/y extent.',
        {'bbox': _BBOX}, ['bbox']),
    _fn('find_free_spot',
        'Find free positions inside a placement area for objects of the given footprint, avoiding '
        'objects already there, the area edges and each other. Returns positions (on the surface, '
        'ready for place) and whether there is enough room. Use count > 1 to place several objects, '
        "and arrangement='line' to get them on a straight line, evenly spaced.",
        {'bbox': _BBOX,
         'footprint_m': {'type': 'number', 'description': 'object width in metres (from inspect_object)'},
         'count': {'type': 'integer', 'description': 'how many positions (default 1)'},
         'arrangement': {'type': 'string', 'enum': ['any', 'line'], 'description': 'default any'}},
        ['bbox', 'footprint_m']),
    _fn('pick',
        'Grasp an object: hover above grasp_point, go down, close the gripper, lift back up. '
        'Opening the gripper first drops anything already held. The gripper has no sensor: '
        'check with an image or inspect_object whether the object really came along.',
        {'grasp_point': _XYZ,
         'grasp_width': {'type': 'number', 'description': 'object width in metres (from inspect_object)'}},
        ['grasp_point', 'grasp_width']),
    _fn('place',
        'Put the held object down: hover above position, go down, open the gripper, lift back up. '
        'position is the surface point where the object will stand (from find_free_spot or inspect_area).',
        {'position': _XYZ,
         'held_height': {'type': 'number',
                         'description': 'held_height_below_grasp_m of the held object, from inspect_object'}},
        ['position', 'held_height']),
    _fn('move_home', 'Move the arm to its home pose, mostly out of the camera view.'),
    _fn('update_plan', 'Replace the remaining plan when reality differs from what you expected.',
        {'steps': _PLAN_STEPS, 'reason': {'type': 'string'}}, ['steps', 'reason']),
    _fn('report_progress', 'Mark a plan step as done or failed, so the operator can follow.',
        {'step': {'type': 'integer', 'description': '1-based index in the current plan'},
         'status': {'type': 'string', 'enum': ['done', 'failed', 'retrying']},
         'note': {'type': 'string'}},
        ['step', 'status']),
    _fn('finish', 'End the task. Call it once the goal is verified, or when it cannot be achieved.',
        {'success': {'type': 'boolean'}, 'summary': {'type': 'string'}},
        ['success', 'summary']),
]

ACTION_TOOLS = ('pick', 'place', 'move_home')


@dataclass
class ToolResult:
    text: str
    image_path: str = None
    finished: bool = False
    success: bool = False


@dataclass
class ToolboxConfig:
    table_z: float
    limits: dict                      # {'x': [lo, hi], 'y': [...], 'z': [...]}
    grasp: geometry.GraspGeometry
    edge_inset_frac: float = 0.15
    approach_offset_m: float = 0.10
    # Horizontal distance from the robot base beyond which the top down IK
    # fails (observed: 0.88 m failed in simulation).
    max_reach_m: float = 0.80
    free_spot_margin_m: float = 0.01
    free_spot_grid: int = 14          # samples per side for find_free_spot
    # 'pixel', or 'norm1000' for models that answer in 0..1000 coordinates
    # whatever the image size (the Qwen family).
    coords: str = 'pixel'
    auto_look_after_action: bool = True


@dataclass
class Toolbox:
    backend: object
    config: ToolboxConfig
    log: object = print
    plan: list = field(default_factory=list)
    image_size: tuple = (640, 480)
    images: list = field(default_factory=list)
    actions: list = field(default_factory=list)   # (tool, args, ok, error) of every action
    # What the gripper was last commanded to do. Not a sensor: after a pick it
    # is closed whether or not the object came along.
    gripper: str = 'open'

    # ── entry point ────────────────────────────────────────────────────────

    def run(self, name: str, args: dict) -> ToolResult:
        handler = getattr(self, f'_tool_{name}', None)
        if handler is None:
            return ToolResult(f'ERROR: unknown tool {name!r}')
        try:
            return handler(**args)
        except TypeError as exc:
            return ToolResult(f'ERROR: bad arguments for {name}: {exc}')
        except Exception as exc:   # tool failures go back to the model, not up the stack
            return ToolResult(f'ERROR: {name} failed: {type(exc).__name__}: {exc}')

    def capture(self) -> str:
        path, w, h = self.backend.capture()
        self.image_size = (w, h)
        self.images.append(path)
        return path

    # ── perception ─────────────────────────────────────────────────────────

    def _tool_look(self):
        path = self.capture()
        return ToolResult('New image attached below.', image_path=path)

    def _tool_inspect_object(self, bbox):
        bbox = self._to_pixels(bbox)
        px = geometry.bbox_sample_pixels(bbox, self.config.edge_inset_frac)
        order = ['center', 'left', 'right', 'top', 'bottom']
        pts = dict(zip(order, self.backend.deproject([px[k] for k in order])))
        if pts['center'] is None:
            return ToolResult(f'ERROR: no depth at the centre of bbox {bbox}; is the bbox on the object?')
        edges = {k: v for k, v in pts.items() if k != 'center' and v is not None}
        m = geometry.object_measurement(pts['center'], edges, self.config.table_z, self.config.grasp)
        # Footprint from the bbox size, scaled at the object's distance from the
        # camera: deprojected edges can collapse onto the face centroid.
        scale = self.backend.metres_per_pixel(pts['center'])
        if scale is not None:
            m['width_m'] = round((bbox[2] - bbox[0]) * scale[0], 4)
            m['depth_m'] = round((bbox[3] - bbox[1]) * scale[1], 4)
        return ToolResult(json.dumps(m))

    def _tool_inspect_area(self, bbox):
        bbox = self._to_pixels(bbox)
        px = geometry.bbox_sample_pixels(bbox, self.config.edge_inset_frac)
        x0, y0, x1, y1 = bbox
        ix, iy = self.config.edge_inset_frac * (x1 - x0), self.config.edge_inset_frac * (y1 - y0)
        corners_px = [(x0 + ix, y0 + iy), (x1 - ix, y0 + iy), (x0 + ix, y1 - iy), (x1 - ix, y1 - iy)]
        pts = self.backend.deproject([px['center']] + corners_px)
        if pts[0] is None:
            return ToolResult(f'ERROR: no depth at the centre of bbox {bbox}')
        m = geometry.surface_measurement(pts[0], [p for p in pts[1:] if p is not None])
        return ToolResult(json.dumps(m))

    def _tool_find_free_spot(self, bbox, footprint_m, count=1, arrangement='any'):
        bbox = self._to_pixels(bbox)
        x0, y0, x1, y1 = bbox
        n = self.config.free_spot_grid
        pixels = [
            (x0 + (x1 - x0) * (i + 0.5) / n, y0 + (y1 - y0) * (j + 0.5) / n)
            for j in range(n) for i in range(n)
        ]
        spot = geometry.free_spot(
            self.backend.deproject(pixels), float(footprint_m), self.config.free_spot_margin_m,
            count=int(count), arrangement=str(arrangement),
            allowed=lambda q: self._check(q, lift=self.config.approach_offset_m) is None,
        )
        return ToolResult(json.dumps(spot))

    # ── actions ────────────────────────────────────────────────────────────

    def _tool_pick(self, grasp_point, grasp_width):
        err = self._check(grasp_point, lift=self.config.approach_offset_m)
        if err:
            return self._refused('pick', grasp_point, err)
        target = [float(v) for v in grasp_point]
        width = max(float(grasp_width), 0.0)
        return self._sequence('pick', {'grasp_point': target, 'grasp_width': width}, [
            ('approach', {'target_position': target, 'offset': self.config.approach_offset_m,
                          'approach_direction': 'z'}),
            ('pick', {'target_position': target, 'grasp_width': width, 'grasp_axis': 'z'}),
            ('approach', {'target_position': target, 'offset': self.config.approach_offset_m,
                          'approach_direction': 'z'}),
        ])

    def _tool_place(self, position, held_height):
        held = max(float(held_height), 0.0)
        err = self._check(position, lift=held + self.config.approach_offset_m)
        if err:
            return self._refused('place', position, err)
        target = [float(v) for v in position]
        hover_base = [target[0], target[1], target[2] + held]
        return self._sequence('place', {'position': target, 'held_height': held}, [
            ('approach', {'target_position': hover_base, 'offset': self.config.approach_offset_m,
                          'approach_direction': 'z'}),
            # The executor raises the TCP by object_height, and retracts to the
            # hover pose after opening.
            ('release', {'release_position': target, 'object_height': held}),
        ])

    def _tool_move_home(self):
        return self._sequence('move_home', {}, [('move_home', {})])

    # ── bookkeeping ────────────────────────────────────────────────────────

    def _tool_submit_plan(self, steps, notes=''):
        self.plan = _clean_steps(steps)
        return ToolResult(f'Draft plan recorded ({len(self.plan)} steps).')

    def _tool_update_plan(self, steps, reason=''):
        self.plan = _clean_steps(steps)
        self.log(f'[Plan] revised ({reason}): ' + '; '.join(s['description'] for s in self.plan))
        return ToolResult(f'Plan updated ({len(self.plan)} steps).')

    def _tool_report_progress(self, step, status, note=''):
        tail = '-> OK' if status == 'done' else f'-> {status.upper()}'
        self.log(f'[Step {int(step)}] {note or status} {tail}')
        return ToolResult('noted')

    def _tool_finish(self, success, summary):
        return ToolResult('Task ended.', finished=True, success=bool(success))

    # ── helpers ────────────────────────────────────────────────────────────

    def _sequence(self, tool, summary_args, skills):
        for skill, args in skills:
            ok, error = self.backend.run_skill(skill, args)
            if skill == 'pick':
                # The executor opens the gripper before closing it, so even a
                # failed pick may have dropped what was held.
                self.gripper = 'closed' if ok else 'unknown'
            elif skill == 'release' and ok:
                self.gripper = 'open'
            if not ok:
                self.actions.append((tool, summary_args, False, f'{skill}: {error}'))
                self.log(f'[Agent] {tool} {json.dumps(summary_args)} -> FAILED at {skill}: {error}')
                return self._after_action(f'ERROR: {tool} failed during {skill}: {error}')
        self.actions.append((tool, summary_args, True, ''))
        self.log(f'[Agent] {tool} {json.dumps(summary_args)} -> done')
        return self._after_action(f'{tool} completed.')

    def _after_action(self, text):
        text += ' ' + _GRIPPER_STATE[self.gripper]
        if not self.config.auto_look_after_action:
            return ToolResult(text)
        path = self.capture()
        return ToolResult(text + ' A fresh image is attached below.', image_path=path)

    def _refused(self, tool, position, err):
        self.actions.append((tool, {'position': position}, False, f'refused: {err}'))
        self.log(f'[Agent] {tool} refused: {err}')
        return ToolResult(f'REFUSED, nothing moved: {err}')

    def _check(self, position, lift):
        err = geometry.check_target(position, self.config.table_z, self.config.limits)
        if err:
            return err
        reach = math.hypot(float(position[0]), float(position[1]))
        if reach > self.config.max_reach_m:
            return f'{reach:.2f} m from the robot base, beyond its reach ({self.config.max_reach_m} m)'
        hover_z = float(position[2]) + lift
        z_hi = self.config.limits.get('z', (None, float('inf')))[1]
        if hover_z > z_hi:
            return f'the hover height {hover_z:.3f} would exceed the workspace z limit {z_hi}'
        return None

    def _to_pixels(self, bbox):
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError(f'bbox must be [x_min, y_min, x_max, y_max], got {bbox!r}')
        x0, y0, x1, y1 = (float(v) for v in bbox)
        if self.config.coords == 'norm1000':
            w, h = self.image_size
            x0, x1 = x0 * w / 1000.0, x1 * w / 1000.0
            y0, y1 = y0 * h / 1000.0, y1 * h / 1000.0
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        w, h = self.image_size
        if x1 < 0 or y1 < 0 or x0 > w or y0 > h:
            raise ValueError(f'bbox {bbox} is outside the {w}x{h} image')
        return [max(x0, 0.0), max(y0, 0.0), min(x1, w - 1.0), min(y1, h - 1.0)]


_GRIPPER_STATE = {
    'open': 'Gripper: open, holding nothing.',
    'closed': ('Gripper: closed since the last pick. If the pick worked, the object hangs under the '
               'gripper and moves with the arm; a new pick would open the gripper and drop it.'),
    'unknown': 'Gripper: unknown after a failed pick; anything held may have been dropped.',
}


def _clean_steps(steps):
    out = []
    for s in steps or []:
        if isinstance(s, dict):
            out.append({'action': str(s.get('action', '?')), 'description': str(s.get('description', ''))})
        else:
            out.append({'action': 'step', 'description': str(s)})
    return out
