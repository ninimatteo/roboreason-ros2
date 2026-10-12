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
    'description': 'tight [x_min, y_min, x_max, y_max] in pixels of the latest image',
}
_XYZ = {
    'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3,
    'description': '[x, y, z] metres, copied from a perception result',
}
_PLAN_STEPS = {
    'type': 'array',
    'items': {
        'type': 'object',
        'properties': {
            'action': {'type': 'string'},
            'description': {'type': 'string'},
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

# Descriptions are kept terse on purpose: the schemas go out with every call
# (about 1600 tokens before trimming), and the workflow is explained once in
# the system prompt.
_WIDTH = {'type': 'number', 'description': 'width_m from inspect_object'}
EXECUTION_TOOLS = [
    _fn('look', 'Fresh camera image (attached after the result).'),
    _fn('inspect_object',
        'Depth measurement of an object: top_center, height, width_m, grasp_point (for pick), '
        'held_height_below_grasp_m (for place), looks_empty.',
        {'bbox': _BBOX}, ['bbox']),
    _fn('inspect_area', 'Depth measurement of a placement area: surface_z, x/y extent.',
        {'bbox': _BBOX}, ['bbox']),
    _fn('find_free_spot',
        'Free positions on an area for objects of footprint_m, clear of other objects, edges and '
        "each other. count>1 for several, arrangement='line' for an evenly spaced row.",
        {'bbox': _BBOX, 'footprint_m': _WIDTH,
         'count': {'type': 'integer'},
         'arrangement': {'type': 'string', 'enum': ['any', 'line']}},
        ['bbox', 'footprint_m']),
    _fn('pick', 'Hover, descend, close gripper, lift. Opens first: drops anything held.',
        {'grasp_point': _XYZ, 'grasp_width': _WIDTH}, ['grasp_point', 'grasp_width']),
    _fn('place', 'Hover, descend, open gripper, lift. position: surface point to stand on.',
        {'position': _XYZ,
         'held_height': {'type': 'number', 'description': 'held_height_below_grasp_m'}},
        ['position', 'held_height']),
    _fn('move_home', 'Arm to home pose, mostly out of view.'),
    _fn('update_plan', 'Replace the plan when reality differs.',
        {'steps': _PLAN_STEPS, 'reason': {'type': 'string'}}, ['steps', 'reason']),
    _fn('report_progress', 'Mark a plan step for the operator.',
        {'step': {'type': 'integer'}, 'status': {'type': 'string', 'enum': ['done', 'failed', 'retrying']},
         'note': {'type': 'string'}},
        ['step', 'status']),
    _fn('finish', 'End the task, once verified or impossible.',
        {'success': {'type': 'boolean'}, 'summary': {'type': 'string'}}, ['success', 'summary']),
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
    # whatever the image size (the Qwen family, see _to_pixels).
    coords: str = 'pixel'
    auto_look_after_action: bool = True
    # Images go to the model downscaled to this width (0 = as captured): an
    # image is the largest part of a request (about 1800 tokens at 640x480
    # on Qwen). Measurements still use the full resolution depth.
    send_width: int = 448


@dataclass
class Toolbox:
    backend: object
    config: ToolboxConfig
    log: object = print
    plan: list = field(default_factory=list)
    image_size: tuple = (640, 480)   # as sent to the model
    full_size: tuple = (640, 480)    # as captured (deprojection pixels)
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
        """Grab a frame; returns the path of the image to show the model."""
        path, w, h = self.backend.capture()
        self.full_size = self.image_size = (w, h)
        sw = self.config.send_width
        if sw and w > sw:
            from PIL import Image
            sh = round(h * sw / w)
            small = str(path).rsplit('.', 1)[0] + f'_{sw}.png'
            Image.open(path).resize((sw, sh), Image.LANCZOS).save(small)
            path, self.image_size = small, (sw, sh)
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
            # 0..1000 on both axes relative to the image WIDTH, as if the image
            # were padded to a square: what Qwen3.8 on Groq consistently does
            # (probed 2026-10-13, three objects, y was 0.7 of the height-based
            # value every time).
            w, _ = self.image_size
            x0, x1, y0, y1 = (v * w / 1000.0 for v in (x0, x1, y0, y1))
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        w, h = self.image_size
        if x1 < 0 or y1 < 0 or x0 > w or y0 > h:
            raise ValueError(f'bbox {bbox} is outside the {w}x{h} image')
        # from the image the model saw to the captured resolution
        fx, fy = self.full_size[0] / w, self.full_size[1] / h
        fw, fh = self.full_size
        return [max(x0 * fx, 0.0), max(y0 * fy, 0.0), min(x1 * fx, fw - 1.0), min(y1 * fy, fh - 1.0)]


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
