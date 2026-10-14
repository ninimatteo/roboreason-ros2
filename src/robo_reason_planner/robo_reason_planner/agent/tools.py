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
  backend.segment(image_path, names) -> [bool mask HxW, ...]   (segmenter 'sam' only)
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
# Positions never pass through the model: perception tools store what they
# measured under a short id, actions take the id. Copying numbers was a
# failure mode of its own (signs dropped, digits shifted).
_REF = {'type': 'string', 'description': 'id from a perception result'}
DETECT_OBJECTS = _fn(
    'detect_objects',
    'Find everything standing on the table with the depth camera, trays and boxes included: one id '
    'each (o1...), drawn on the image that comes back, with colour, size, what it stands on and '
    'which image border cuts it off. "table" is the table. Use the ids with pick, place and '
    'find_free_spot.',
    {'names': {'type': 'array', 'items': {'type': 'string'},
               'description': 'optional: kinds of thing the command names (cube, tray...), helps '
                              'separate touching ones'}})

LOOK = _fn('look', 'Fresh camera image (attached after the result).')
# With an RGB camera on the wrist (CAMERAS=fixed+wrist, ROBOAI-47) look can
# show its close view too; ids and bboxes always refer to the fixed camera.
LOOK_WRIST = _fn(
    'look', 'Fresh camera image (attached after the result). camera "wrist": close view from the '
    'gripper, to check a grasp or a placement; bboxes and ids always refer to the fixed camera.',
    {'camera': {'type': 'string', 'enum': ['fixed', 'wrist']}})

EXECUTION_TOOLS = [
    DETECT_OBJECTS,
    LOOK,
    _fn('inspect_object',
        'Depth measurement of an object. Returns an id (o1, o2...) for pick, plus height_m, width_m, '
        'looks_empty.',
        {'bbox': _BBOX}, ['bbox']),
    _fn('inspect_area', 'Depth measurement of a placement area. Returns an id (a1...) for place, '
        'plus surface_z and x/y extent.',
        {'bbox': _BBOX}, ['bbox']),
    _fn('find_free_spot',
        'Free spots on top of something (the table, a tray, an area) for objects like `object`, clear '
        "of other objects, edges and each other. Returns ids (s1...) for place. count>1 for several, "
        "arrangement='line' for an evenly spaced row.",
        {'area': {'type': 'string', 'description': 'id of what to put them on, or give bbox'},
         'bbox': _BBOX, 'object': _REF, 'count': {'type': 'integer'},
         'arrangement': {'type': 'string', 'enum': ['any', 'line']}},
        ['object']),
    _fn('pick', 'Grasp a measured object: hover, descend, close gripper, lift. Opens first: drops '
        'anything held.',
        {'object': _REF}, ['object']),
    _fn('place', 'Put the held object on target: a spot (s), an area (a), or any object id: on top of '
        'it, at a free place when it is wide (a tray, the table).',
        {'target': _REF}, ['target']),
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
    # inspect_object re-centres a rough bbox on the nearest raised object in
    # the depth around it (weak grounding models miss by 15 to 30 px).
    snap_to_object: bool = True
    snap_step_px: float = 4.0
    snap_min_margin_px: float = 30.0
    # detect_objects: depth grid step over the whole image, smallest blob kept.
    detect_step_px: float = 8.0
    detect_min_samples: int = 4
    # 'depth' segments the depth grid (geometry.detect_blobs); 'sam' takes SAM 2
    # masks from the perception service and keeps those standing above the
    # table (agent/masks.py, ROBOAI-61).
    segmenter: str = 'depth'
    sam_min_area_px: int = 200
    sam_max_samples: int = 400        # depth samples per mask
    refine_step_px: float = 2.0
    # Widest object the gripper can take (RG2: 0.11 m fully open). Anything
    # wider is still a thing in the scene, only not pickable: placing on it
    # looks for a free place on its top.
    max_grasp_width_m: float = 0.10
    # After pick and place: arm home, detect, and check the outcome.
    verify_actions: bool = True
    verify_radius_m: float = 0.03
    robot_base_radius_m: float = 0.15
    # An RGB camera on the wrist next to the fixed one: look(camera='wrist').
    wrist_camera: bool = False
    # The wrist camera is the only one (ROBOAI-48): every image is taken from
    # the survey pose, and measurements need the arm back there, since the
    # depth and the camera pose are read when the pixels are deprojected.
    survey: bool = False


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
    refs: dict = field(default_factory=dict)     # id -> what a perception tool measured
    held: dict = None                            # measurement of the object picked last
    # ([[x0, x1], [y0, y1]], top_z) of everything found by the last detect:
    # what an object measured there stands on
    surfaces: list = field(default_factory=list)
    at_survey: bool = False                      # arm still where the last image was taken (survey mode)
    detect_names: list = field(default_factory=list)   # names of the last detect_objects, reused by the checks

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

    def execution_tools(self) -> list:
        return [LOOK_WRIST if t is LOOK and self.config.wrist_camera else t for t in EXECUTION_TOOLS]

    def capture(self) -> str:
        """Grab a frame; returns the path of the image to show the model."""
        self._to_survey()
        path, w, h = self.backend.capture()
        self.full_size = self.image_size = (w, h)
        self.last_full = path
        path, self.image_size = self._downscale(path, w, h)
        self.images.append(path)
        self.last_sent = path
        return path

    def _to_survey(self):
        if self.config.survey and not self.at_survey:
            ok, error = self.backend.run_skill('survey', {})
            if not ok:
                raise RuntimeError(f'survey pose not reached: {error}')
            self.at_survey = True

    def _deproject(self, pixels):
        self._to_survey()
        return self.backend.deproject(pixels)

    def _metres_per_pixel(self, point):
        self._to_survey()
        return self.backend.metres_per_pixel(point)

    def _downscale(self, path, w, h):
        sw = self.config.send_width
        if sw and w > sw:
            from PIL import Image
            sh = round(h * sw / w)
            small = str(path).rsplit('.', 1)[0] + f'_{sw}.png'
            Image.open(path).resize((sw, sh), Image.LANCZOS).save(small)
            return small, (sw, sh)
        return path, (w, h)

    # ── perception ─────────────────────────────────────────────────────────

    _pending_hold = None
    last_full = None
    last_sent = None

    def _tool_look(self, camera='fixed'):
        if camera == 'wrist':
            if not self.config.wrist_camera:
                return ToolResult('ERROR: there is no wrist camera in this setup; look without camera.')
            # the fixed camera's frame stays the one bboxes and ids refer to
            path, w, h = self.backend.capture(camera='wrist')
            path, _ = self._downscale(path, w, h)
            self.images.append(path)
            return ToolResult('Wrist camera image attached below.', image_path=path)
        path = self.capture()
        return ToolResult('New image attached below.', image_path=path)

    def _tool_detect_objects(self, names=None):
        self.detect_names = [str(n) for n in names or []]
        found = self._detect()
        if len(found) <= 1:
            return ToolResult('Nothing found standing on the table.')
        return ToolResult(json.dumps(found), image_path=self._marked(found))

    def _detect(self) -> list:
        """Everything standing on the table, stored under new ids, the table
        first. No split into objects and areas (ROBOAI-55): a tray is a thing
        like a cube, only too wide to pick, and each thing says what it
        stands on, so a stack or a cube on a tray is measured from its own
        support."""
        fw, fh = self.full_size
        step = self.config.detect_step_px
        cols, rows = int((fw - step) // step) + 1, int((fh - step) // step) + 1
        pixels = [(step / 2 + c * step, step / 2 + r * step) for r in range(rows) for c in range(cols)]
        pts = self._deproject(pixels)
        sam = self.config.segmenter == 'sam'
        if sam:
            blobs = self._sam_blobs(pts)
            step = 0      # mask boxes are tight already
        else:
            blobs = geometry.detect_blobs([(u, v, p) for (u, v), p in zip(pixels, pts)], cols, rows)
        image = self._full_image()
        sx, sy = self.image_size[0] / fw, self.image_size[1] / fh

        def shown(box):
            return [round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)]

        found = []
        # The table itself: "in a line on the table" is then a find_free_spot
        # on it (the room floor beyond the edge, objects and the robot's reach
        # are excluded there), with no box to draw.
        table = self._table_area(pixels, pts)
        if table is not None:
            self.refs['table'] = table
            found.append({'id': 'table', 'bbox': shown(table['bbox_full'])})
        blobs = [b for b in sorted(blobs, key=lambda b: (b['bbox_px'][0], b['bbox_px'][1]))
                 if b['samples'] >= self.config.detect_min_samples
                 # the robot's own base stands at the origin of its frame
                 and math.hypot(b['top_center'][0], b['top_center'][1]) >= self.config.robot_base_radius_m]
        self.surfaces = [(b['xy_range'], b['top_center'][2]) for b in blobs]
        belows = []
        for i, b in enumerate(blobs):
            below = geometry.support_index(b['top_center'], b['top_center'][2],
                                           self.surfaces[:i] + self.surfaces[i + 1:])
            belows.append(below + 1 if below is not None and below >= i else below)
        ids, entries = [], []
        for i, (b, below) in enumerate(zip(blobs, belows)):
            # what stands on this thing: its box holds those too (ROBOAI-56)
            on_top = [blobs[j]['bbox_px'] for j, k in enumerate(belows) if k == i]
            support_z = self.surfaces[below][1] if below is not None else self.config.table_z
            x0, y0, x1, y1 = b['bbox_px']
            box = [x0 - step / 2, y0 - step / 2, x1 + step / 2, y1 + step / 2]
            (xa, xb), (ya, yb) = b['xy_range']
            wide = b['narrow_m'] > self.config.max_grasp_width_m
            m = None
            if not wide and not on_top:
                if sam:
                    # the mask is the outline already: a depth refine would
                    # join it again to a neighbour of the same height
                    m = self._measure_top(b, support_z)
                else:
                    box = self._refine(box, top_z=b['top_center'][2]) or box
                    m = self._measure(box, support_z=support_z)
            if m is None:
                # too wide to pick, or something stands on it and its box would
                # measure that too: the blob's own top surface
                top = b['top_center']
                m = {'top_center': top, 'height_m': round(top[2] - support_z, 4),
                     'width_m': round(xb - xa, 4), 'depth_m': round(yb - ya, 4),
                     'narrow_m': b['narrow_m'], 'looks_empty': False}
            cut = geometry.cut_off_sides(b['bbox_px'], fw, fh, step or self.config.detect_step_px)
            ref = self._store('o', self._entity(m, box, cut_off=cut))
            wide = not self.refs[ref]['pickable']
            entry = {'id': ref, 'color': _blob_colour(image, b) if image is not None else None,
                     'bbox': shown(box), 'height_m': m['height_m'], 'width_m': m.get('width_m'),
                     'depth_m': m.get('depth_m')}
            if wide:
                entry['pickable'] = False
            if cut:
                entry['cut_off'] = cut      # seen only in part: size and centre are not the real ones
            ids.append(ref)
            entries.append((entry, below))
        for entry, below in entries:
            entry['stands_on'] = ids[below] if below is not None else 'table'
            found.append(entry)
        return found

    def _sam_blobs(self, grid_pts) -> list:
        """detect_blobs entries from the SAM masks of the last frame
        (agent/masks.py). The table height is the dominant one of the grid."""
        from robo_reason_planner.agent import masks as sam_masks
        valid = [p for p in grid_pts if p is not None]
        if not valid:
            return []
        floor = geometry._dominant_height(valid, 0.01)
        pieces = [piece for m in self.backend.segment(self.last_full, self.detect_names)
                  for piece in sam_masks.split_pieces(m, self.config.sam_min_area_px)]
        samples = [sam_masks.sample_pixels(m, self.config.sam_max_samples) for m in pieces]
        flat = [px for s in samples for px in s]
        pts = iter(self._deproject(flat))
        blobs = []
        for mask, px in zip(pieces, samples):
            b = sam_masks.blob(mask, [next(pts) for _ in px], floor)
            if b is not None:
                blobs.append(b)
        return sam_masks.drop_covered(blobs)

    def _measure_top(self, blob, support_z):
        """Measurement of a SAM thing from the depth samples of its top."""
        m = geometry.object_measurement(blob['top_center'], {}, support_z, self.config.grasp)
        (xa, xb), (ya, yb) = blob['xy_range']
        m.update(width_m=round(xb - xa, 4), depth_m=round(yb - ya, 4), narrow_m=blob['narrow_m'])
        return m

    def _marked(self, found):
        """The image the model last saw, with every id drawn at its thing
        (Set of Mark): the model names what it sees by id, the depth decides
        where each thing is and how big."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            im = Image.open(self.last_sent).convert('RGB')
        except Exception:
            return None
        draw = ImageDraw.Draw(im)
        font = ImageFont.load_default(size=max(11, im.size[0] // 40))
        for f in found:
            if f['id'] == 'table':
                continue
            x0, y0, x1, y1 = f['bbox']
            draw.rectangle([x0, y0, x1, y1], outline=(255, 255, 0), width=1)
            tx, ty = x0, max(y0 - font.size - 2, 0)
            box = draw.textbbox((tx, ty), f['id'], font=font)
            draw.rectangle([box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1], fill=(0, 0, 0))
            draw.text((tx, ty), f['id'], fill=(255, 255, 0), font=font)
        path = str(self.last_sent).rsplit('.', 1)[0] + '_marked.png'
        im.save(path)
        self.images.append(path)
        return path

    def _table_area(self, pixels, pts):
        """The table surface: the dominant height of the depth grid."""
        valid = [(uv, p) for uv, p in zip(pixels, pts) if p is not None]
        if not valid:
            return None
        floor = geometry._dominant_height([p for _, p in valid], 0.01)
        on = [(uv, p) for uv, p in valid if abs(p[2] - floor) <= 0.01]
        if not on:
            return None
        us, vs = [uv[0] for uv, _ in on], [uv[1] for uv, _ in on]
        xs, ys = sorted(p[0] for _, p in on), sorted(p[1] for _, p in on)
        centre = [xs[len(xs) // 2], ys[len(ys) // 2], floor]
        return {'kind': 'object', 'name': 'table', 'position': centre, 'top_center': centre,
                'surface_z': floor, 'bbox_full': [min(us), min(vs), max(us), max(vs)],
                'pickable': False, 'looks_empty': False}

    def _full_image(self):
        try:
            from PIL import Image
            return Image.open(self.last_full).convert('RGB')
        except Exception:
            return None

    def _measure(self, bbox, support_z=None):
        """Measurement of the object inside a full resolution bbox, or None.
        support_z: the height of what it stands on, when already known."""
        px = geometry.bbox_sample_pixels(bbox, self.config.edge_inset_frac)
        order = ['center', 'left', 'right', 'top', 'bottom']
        pts = dict(zip(order, self._deproject([px[k] for k in order])))
        if pts['center'] is None:
            return None
        edges = {k: v for k, v in pts.items() if k != 'center' and v is not None}
        c = pts['center']
        support = (support_z if support_z is not None
                   else geometry.support_height(c[:2], c[2], self.surfaces, self.config.table_z))
        m = geometry.object_measurement(c, edges, support, self.config.grasp)
        # Size from the top surface in metres: the bbox in pixels times the
        # scale overstates it when the camera sees the object off axis (a
        # square top becomes a wider parallelogram).
        step = self.config.refine_step_px
        cols, rows = int((bbox[2] - bbox[0]) // step) + 1, int((bbox[3] - bbox[1]) // step) + 1
        grid = [(bbox[0] + i * step, bbox[1] + j * step) for j in range(rows) for i in range(cols)]
        top = [p for p in self._deproject(grid) if p is not None and abs(p[2] - c[2]) <= 0.01]
        scale = self._metres_per_pixel(c)
        if len(top) >= 4 and scale is not None:
            # the samples' extent, widened by half a step on each side
            m['width_m'] = round(max(p[0] for p in top) - min(p[0] for p in top) + step * scale[0], 4)
            m['depth_m'] = round(max(p[1] for p in top) - min(p[1] for p in top) + step * scale[1], 4)
            m['narrow_m'] = round(geometry.narrowest_width(top) + step * min(scale), 4)
        return m

    def _tool_inspect_object(self, bbox):
        given = self._to_pixels(bbox)
        m = self._measure(given)
        if m is None:
            return ToolResult(f'ERROR: no depth at the centre of bbox {bbox}')
        if not m['looks_empty']:
            # on an object: tighten the outline for its size, then measure again
            tight = self._refine(given, top_z=m['top_center'][2])
            if tight and self._runs_past_window(tight, given):
                # The level top goes on past the search window: two things
                # touching at the same height, one depth segment. The model's
                # box is the only split there is (ROBOAI-55).
                tight = None
            m = (self._measure(tight) if tight else None) or m
            ref = self._store('o', self._entity(m, tight or given))
            return ToolResult(json.dumps({'id': ref, **_public(m)}))
        # The spot itself is empty: say so (that is what a check after a pick
        # asks), and offer the nearest object in case the box just missed it.
        out = {'looks_empty': True, 'top_z': m['top_center'][2]}
        near = self._snap(given) if self.config.snap_to_object else None
        if near is not None:
            pre = self._measure(near)
            near = self._refine(near, top_z=pre['top_center'][2] if pre else None) or near
            nm = self._measure(near)
            if nm is not None and not nm['looks_empty']:
                ref = self._store('o', self._entity(nm, near))
                sx, sy = self.image_size[0] / self.full_size[0], self.image_size[1] / self.full_size[1]
                dist = math.hypot(((near[0] + near[2]) - (given[0] + given[2])) / 2 * sx,
                                  ((near[1] + near[3]) - (given[1] + given[3])) / 2 * sy)
                out['nearest_object'] = {
                    'id': ref, 'distance_px': round(dist),
                    'bbox': [round(near[0] * sx), round(near[1] * sy), round(near[2] * sx), round(near[3] * sy)],
                    **_public(nm)}
        return ToolResult(json.dumps(out))

    def _entity(self, m, box, **extra):
        """What is stored under an object id: the measurement, plus what
        place and find_free_spot need to put something on top of it."""
        # the gripper closes across the narrowest side, not the image axes
        narrow = m.get('narrow_m') or min(m.get('width_m') or 0.0, m.get('depth_m') or 0.0)
        wide = narrow > self.config.max_grasp_width_m
        return {'kind': 'object', **m, 'bbox_full': box, 'surface_z': m['top_center'][2],
                'position': m['top_center'], 'pickable': not wide, **extra}

    def _runs_past_window(self, outline, bbox) -> bool:
        """Whether an outline found by _refine around bbox reaches the edge
        of its search window (not the image border): one object fits in the
        window, a segment that runs past it is more than the object the box
        was drawn on."""
        margin = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2 + 8
        fw, fh = self.full_size
        window = [bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin]
        inside = [window[0] > 0, window[1] > 0, window[2] < fw - 1, window[3] < fh - 1]
        reach = 2 * self.config.refine_step_px
        return any(ok and abs(o - w) <= reach for o, w, ok in zip(outline, window, inside))

    def _refine(self, bbox, top_z=None):
        """Outline of the object at a bbox on a fine grid. The search reaches
        past the box (half its size plus a few pixels) so the table around
        the object is in it: heights are relative to the dominant one. With
        the object's top height known, the outline is that of its top
        surface (geometry.snap_to_object)."""
        margin = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2 + 8
        return self._snap(bbox, step=self.config.refine_step_px, margin=margin, level_z=top_z)

    def _snap(self, bbox, step=None, margin=None, level_z=None):
        """Search the depth around a bbox for the nearest raised object
        (geometry.snap_to_object); None when there is nothing raised. By
        default the search reaches one box size around it; a small margin and
        step instead refine the outline of an object already found."""
        x0, y0, x1, y1 = bbox
        w, h = x1 - x0, y1 - y0
        m = margin if margin is not None else max(w, h, self.config.snap_min_margin_px)
        fw, fh = self.full_size
        sx0, sy0, sx1, sy1 = max(x0 - m, 0), max(y0 - m, 0), min(x1 + m, fw - 1), min(y1 + m, fh - 1)
        step = step or self.config.snap_step_px
        cols, rows = int((sx1 - sx0) // step) + 1, int((sy1 - sy0) // step) + 1
        pixels = [(sx0 + c * step, sy0 + r * step) for r in range(rows) for c in range(cols)]
        pts = self._deproject(pixels)
        found = geometry.snap_to_object(
            [(u, v, p) for (u, v), p in zip(pixels, pts)], cols, rows,
            ((x0 + x1) / 2, (y0 + y1) / 2), self.config.table_z, level_z=level_z)
        if found is None:
            return None
        # the blob's sample extent, widened by half a step on each side
        return [found[0] - step / 2, found[1] - step / 2, found[2] + step / 2, found[3] + step / 2]

    def _tool_inspect_area(self, bbox):
        bbox = self._to_pixels(bbox)
        px = geometry.bbox_sample_pixels(bbox, self.config.edge_inset_frac)
        x0, y0, x1, y1 = bbox
        ix, iy = self.config.edge_inset_frac * (x1 - x0), self.config.edge_inset_frac * (y1 - y0)
        corners_px = [(x0 + ix, y0 + iy), (x1 - ix, y0 + iy), (x0 + ix, y1 - iy), (x1 - ix, y1 - iy)]
        pts = self._deproject([px['center']] + corners_px)
        if pts[0] is None:
            return ToolResult(f'ERROR: no depth at the centre of bbox {bbox}')
        m = geometry.surface_measurement(pts[0], [p for p in pts[1:] if p is not None])
        ref = self._store('a', {'kind': 'area', 'position': m['center'], 'bbox_full': bbox, **m})
        return ToolResult(json.dumps({'id': ref, **{k: v for k, v in m.items() if k != 'center'}}))

    def _tool_find_free_spot(self, object, area=None, bbox=None, count=1, arrangement='any'):
        return self._free_spots(self._ref(object, 'object'), area, bbox, count, arrangement)

    def _free_spots(self, obj, area=None, bbox=None, count=1, arrangement='any'):
        footprint_m = obj.get('width_m') or 0.05
        surface_z = None
        if area is not None:
            measured = self._ref(area)
            if 'bbox_full' not in measured:
                raise ValueError(f'{area} is a {measured["kind"]}: give the id of what to put things on')
            bbox, surface_z = measured['bbox_full'], measured.get('surface_z')
        elif bbox is not None:
            bbox = self._to_pixels(bbox)
        else:
            raise ValueError('give an area id or a bbox')
        x0, y0, x1, y1 = bbox
        n = self.config.free_spot_grid
        pixels = [
            (x0 + (x1 - x0) * (i + 0.5) / n, y0 + (y1 - y0) * (j + 0.5) / n)
            for j in range(n) for i in range(n)
        ]
        spot = geometry.free_spot(
            self._deproject(pixels), float(footprint_m), self.config.free_spot_margin_m,
            count=int(count), arrangement=str(arrangement),
            allowed=lambda q: self._check(q, lift=self.config.approach_offset_m) is None,
            surface_z=surface_z,
        )
        out = {k: v for k, v in spot.items() if k not in ('positions', 'position')}
        if 'positions' in spot:
            out['spots'] = [self._store('s', {'kind': 'spot', 'position': p}) for p in spot['positions']]
        return ToolResult(json.dumps(out))

    # ── actions ────────────────────────────────────────────────────────────

    def _tool_pick(self, object):
        obj = self._ref(object, 'object')
        if obj['looks_empty']:
            return self._refused('pick', object, f'{object} looked empty when measured, nothing to grasp')
        if not obj.get('pickable', True):
            return self._refused('pick', object, f'{object} is wider than the gripper opens '
                                 f'({self.config.max_grasp_width_m} m): it can only be placed on')
        if 'grasp_point' not in obj:
            # measured from its own top only, because something stands on it (ROBOAI-56)
            return self._refused('pick', object, f'something stands on {object}: move that first, '
                                 'then detect again')
        grasp_point = obj['grasp_point']
        err = self._check(grasp_point, lift=self.config.approach_offset_m)
        if err:
            return self._refused('pick', object, err)
        target = [float(v) for v in grasp_point]
        width = max(float(obj.get('width_m') or 0.0), 0.0)
        self._pending_hold = obj
        return self._sequence('pick', {'object': object, 'grasp_point': target, 'grasp_width': width},
                              verify=lambda: self._verify_pick(obj), skills=[
            ('approach', {'target_position': target, 'offset': self.config.approach_offset_m,
                          'approach_direction': 'z'}),
            ('pick', {'target_position': target, 'grasp_width': width, 'grasp_axis': 'z'}),
            ('approach', {'target_position': target, 'offset': self.config.approach_offset_m,
                          'approach_direction': 'z'}),
        ])

    def _tool_place(self, target):
        ref = self._ref(target)
        if self.held is None:
            return self._refused('place', target, 'no object is known to be held: pick one first')
        if ref['kind'] == 'object' and not ref.get('pickable', True):
            # a tray, the table: a free place on its top, not its centre
            spot = json.loads(self._free_spots(self.held, area=target).text)
            if not spot.get('ok'):
                return self._refused('place', target, f'no free place on {target} for the held object: '
                                     f'{json.dumps(spot)}')
            position = self.refs[spot['spots'][0]]['position']
        else:
            # on top of an object: its top surface; spot or area: the surface point
            position = ref['top_center'] if ref['kind'] == 'object' else ref['position']
        held = max(float(self.held['held_height_below_grasp_m']), 0.0)
        err = self._check(position, lift=held + self.config.approach_offset_m)
        if err:
            return self._refused('place', target, err)
        target_xyz = [float(v) for v in position]
        hover_base = [target_xyz[0], target_xyz[1], target_xyz[2] + held]
        return self._sequence('place', {'target': target, 'position': target_xyz, 'held_height': held},
                              verify=lambda: self._verify_place(target_xyz), skills=[
            ('approach', {'target_position': hover_base, 'offset': self.config.approach_offset_m,
                          'approach_direction': 'z'}),
            # The executor raises the TCP by object_height, and retracts to the
            # hover pose after opening.
            ('release', {'release_position': target_xyz, 'object_height': held}),
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

    def _sequence(self, tool, summary_args, skills, verify=None):
        for skill, args in skills:
            ok, error = self.backend.run_skill(skill, args)
            self.at_survey = False
            if skill == 'pick':
                # The executor opens the gripper before closing it, so even a
                # failed pick may have dropped what was held.
                self.gripper = 'closed' if ok else 'unknown'
                self.held = self._pending_hold if ok else None
            elif skill == 'release' and ok:
                self.gripper = 'open'
                self.held = None
            if not ok:
                self.actions.append((tool, summary_args, False, f'{skill}: {error}'))
                self.log(f'[Agent] {tool} {json.dumps(summary_args)} -> FAILED at {skill}: {error}')
                return self._after_action(f'ERROR: {tool} failed during {skill}: {error}')
        if verify is not None and self.config.verify_actions:
            failure, report, image = verify()
            if failure:
                self.actions.append((tool, summary_args, False, f'check: {failure}'))
                self.log(f'[Agent] {tool} {json.dumps(summary_args)} -> FAILED check: {failure}')
                return self._after_action(f'ERROR: {tool} ran but the check failed: {failure} {report}', image)
            self.actions.append((tool, summary_args, True, ''))
            self.log(f'[Agent] {tool} {json.dumps(summary_args)} -> done, checked')
            return self._after_action(f'{tool} completed and checked. {report}', image)
        self.actions.append((tool, summary_args, True, ''))
        self.log(f'[Agent] {tool} {json.dumps(summary_args)} -> done')
        return self._after_action(f'{tool} completed.')

    # The gripper has no sensor, and a weak model may not look: after pick
    # and place the arm steps out of view and the depth camera checks the
    # outcome. Returns (failure or None, report for the model, image path).

    def _verify_pick(self, obj):
        found, image = self._look_from_home()
        spot = obj['top_center']
        if self._object_near(found, spot, self.config.verify_radius_m, level=spot[2]):
            self.held = None
            return ('the object is still at its spot, the grasp missed; nothing is held.',
                    self._objects_now(found), image)
        return None, 'Its spot is empty: the object is held. ' + self._objects_now(found), image

    def _verify_place(self, target_xyz):
        found, image = self._look_from_home()
        if not self._object_near(found, target_xyz, self.config.verify_radius_m + 0.01, above=target_xyz[2]):
            return ('nothing stands at the target; the object was probably dropped on the way. '
                    'Find it in the list below.', self._objects_now(found), image)
        return None, 'An object now stands at the target. ' + self._objects_now(found), image

    def _look_from_home(self):
        if not self.config.survey:   # in survey mode capture() moves the arm itself
            self.backend.run_skill('move_home', {})
        self.capture()
        found = self._detect()
        return found, self._marked(found)

    def _object_near(self, found, xyz, radius, level=None, above=None) -> bool:
        """Something whose top is within radius of xyz in x and y, and at
        the given level (the object still where it was picked) or above the
        given height (something now standing on the place target, not the
        tray or table around it)."""
        for f in found:
            if f['id'] == 'table':
                continue
            c = self.refs[f['id']]['top_center']
            if math.hypot(c[0] - xyz[0], c[1] - xyz[1]) > radius:
                continue
            if level is not None and abs(c[2] - level) > 0.015:
                continue
            if above is not None and c[2] <= above + 0.01:
                continue
            return True
        return False

    @staticmethod
    def _objects_now(found) -> str:
        return 'Objects now (fresh ids): ' + json.dumps(found)

    def _after_action(self, text, image=None):
        text += ' ' + _GRIPPER_STATE[self.gripper]
        if image is None and not self.config.auto_look_after_action:
            return ToolResult(text)
        path = image or self.capture()
        return ToolResult(text + ' A fresh image is attached below.', image_path=path)

    def _store(self, prefix, data) -> str:
        n = sum(1 for k in self.refs if k.startswith(prefix)) + 1
        ref = f'{prefix}{n}'
        self.refs[ref] = data
        return ref

    def _ref(self, ref, kind=None) -> dict:
        data = self.refs.get(str(ref).strip())
        if data is None:
            known = ', '.join(self.refs) or 'none yet'
            raise ValueError(f'unknown id {ref!r} (known: {known}); measure first')
        if kind and data['kind'] != kind:
            raise ValueError(f'{ref} is a {data["kind"]}, expected an {kind} id')
        return data

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


def _blob_colour(image, blob):
    """Median colour of the image inside a blob's mask (SAM) or bbox, as a word."""
    if 'mask' in blob:
        import numpy as np
        data = sorted(map(tuple, np.asarray(image)[blob['mask']].tolist()), key=sum)
    else:
        x0, y0, x1, y1 = blob['bbox_px']
        crop = image.crop((int(x0), int(y0), int(x1) + 1, int(y1) + 1))
        data = sorted(crop.getdata(), key=sum)
    if not data:
        return None
    mid = data[len(data) // 4: 3 * len(data) // 4] or data      # drop shadows and highlights
    rgb = [sorted(c[i] for c in mid)[len(mid) // 2] for i in range(3)]
    return geometry.color_name(rgb)


def _public(m: dict) -> dict:
    """What the model sees of a measurement: no coordinates to copy."""
    keys = ('height_m', 'width_m', 'depth_m', 'looks_empty', 'note')
    out = {k: m[k] for k in keys if k in m}
    out['top_z'] = m['top_center'][2]
    return out


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
