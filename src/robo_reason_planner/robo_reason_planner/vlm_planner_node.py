"""
VLMPlannerNode — exposes /plan_task service for vision-based planning.

Captures an RGB frame from /camera/get_image, runs EmbodiedAgent with a VLM
client (pixel-coordinate outputs), then batch-deprojects pixel coords to world
[x, y, z] via /camera/deproject before returning the plan.

The scene_json field from the PlanTask request is not used for object
grounding — the camera provides all perception — but its
workspace.table.surface_z (if present) is read as a known reference height so
pick/release z can be depth-compensated (see _deproject_plan).

ROS2 parameters:
  reasoning_method  (str,   default 'fhp')                    — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str,   default 'groq/llama4-scout-17b')
  temperature       (float, default 0.1)
  tmp_dir           (str,   default '/root/ws/src/vlm_frames') — where to save captured frames
  grounding_mode    (str,   default 'point')                  — 'point' ([x, y] click) or 'bbox'
                                                                  ([x_min, y_min, x_max, y_max] box)
  reasoning_effort  (str,   default settings.VLM_REASONING_EFFORT) — Groq-only Qwen3 <think>
                                                                  control (e.g. 'none'); empty
                                                                  string omits the param entirely
"""

import copy
import json
import shutil
import time
import traceback
import uuid
from pathlib import Path

import dotenv
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_srvs.srv import Trigger

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import Deproject, GetImage, PlanTask
from robo_reason_planner.agent_runner import run_plan_loop
from robo_reason_planner.debug_recorder import DebugRun, fetch_terminal_logs
from robo_reason_reasoning.embodied_agent import EmbodiedAgent


class VLMPlannerNode(Node):
    """
    VLM planner node.

    Uses a ReentrantCallbackGroup so that the /plan_task service callback can
    issue GetImage and Deproject client calls without deadlocking on the
    MultiThreadedExecutor.
    """

    def __init__(self):
        super().__init__('vlm_planner_node')

        # reasoning_method / model_name / temperature are read per-request in
        # _vlm_plan so the GUI can retune the planner live without relaunching.
        # tmp_dir is a static path root, so it stays cached.
        self.declare_parameter('reasoning_method', settings.REASONING_METHOD)
        self.declare_parameter('model_name', settings.MODEL_NAME)
        self.declare_parameter('temperature', settings.TEMPERATURE)
        self.declare_parameter('tmp_dir', settings.TMP_DIR)
        self.declare_parameter('grounding_mode', settings.VLM_GROUNDING_MODE)
        self.declare_parameter('reasoning_effort', settings.VLM_REASONING_EFFORT)

        dotenv.load_dotenv()

        self._tmp_root = Path(self.get_parameter('tmp_dir').value)

        try:
            import cv2 as _cv2
            from cv_bridge import CvBridge
            self._bridge = CvBridge()
            self._cv2 = _cv2
        except ImportError as exc:
            raise RuntimeError(
                f'[VLMPlannerNode] VLM mode requires cv_bridge and opencv-python: {exc}'
            ) from exc

        self._cb_group = ReentrantCallbackGroup()

        self._service = self.create_service(
            PlanTask, '/plan_task', self._plan_task_callback,
            callback_group=self._cb_group,
        )
        self._get_image_client = self.create_client(
            GetImage, '/camera/get_image', callback_group=self._cb_group,
        )
        self._deproject_client = self.create_client(
            Deproject, '/camera/deproject', callback_group=self._cb_group,
        )
        self._terminal_logs_client = self.create_client(
            Trigger, '/gui/get_terminal_logs', callback_group=self._cb_group,
        )

        self.get_logger().info(
            f"[VLMPlannerNode] Ready — {self.get_parameter('reasoning_method').value}, "
            f"{self.get_parameter('model_name').value} (params read per request)"
        )

    # ── /plan_task callback ────────────────────────────────────────────────────

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        self.get_logger().info(f'[VLMPlannerNode] Received: "{user_command}"')

        run = DebugRun(mode='VLM', command=user_command, config={
            'reasoning_method': self.get_parameter('reasoning_method').value,
            'model_name': self.get_parameter('model_name').value,
            'temperature': self.get_parameter('temperature').value,
            'grounding_mode': self.get_parameter('grounding_mode').value,
            'reasoning_effort': self.get_parameter('reasoning_effort').value,
        })

        try:
            plan_data = self._vlm_plan(user_command, request.scene_json, run)
            response.success = True
            response.plan_json = json.dumps(plan_data)
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=True, response=plan_data)
            self.get_logger().info('[VLMPlannerNode] Generated VLM plan.')
        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[VLMPlannerNode] Planning error:\n{tb}')
            response.success = False
            response.error_message = tb
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=False, error=tb)

        return response

    # ── VLM plan ───────────────────────────────────────────────────────────────

    def _vlm_plan(self, user_command: str, scene_json: str, run: DebugRun) -> dict:
        # 1. Capture RGB frame.
        img_resp = self._call_get_image()
        if not img_resp.success:
            raise RuntimeError(f'GetImage failed: {img_resp.error_message}')

        # 2. Save frame to disk.
        task_dir = self._tmp_root / uuid.uuid4().hex[:8]
        task_dir.mkdir(parents=True, exist_ok=True)
        image_paths = self._save_frame(img_resp.image, task_dir, index=0)
        self.get_logger().info(f'[VLMPlannerNode] Saved frame → {image_paths[0]}')
        run.log(f'Saved frame -> {image_paths[0]}')
        run.save_raw_frame(image_paths[0])

        # 3. Run VLM agent — returns actions with pixel [x, y] coordinates.
        reasoning_method = self.get_parameter('reasoning_method').value
        model_name = self.get_parameter('model_name').value
        temperature = self.get_parameter('temperature').value
        grounding_mode = self.get_parameter('grounding_mode').value
        reasoning_effort = self.get_parameter('reasoning_effort').value

        client_parameters = {
            'model_name': model_name,
            'temperature': temperature,
        }
        if reasoning_effort:
            # Omitted (not just empty-string) unless explicitly set, so the
            # model's own default behavior is unchanged by default — see
            # VLM_REASONING_EFFORT in config.py.
            client_parameters['reasoning_effort'] = reasoning_effort

        agent = EmbodiedAgent(
            reasoning_mode=reasoning_method,
            client_parameters=client_parameters,
            client_type='vlm',
            grounding_mode=grounding_mode,
        )

        pixel_steps = run_plan_loop(agent, {
            'user_request': user_command,
            'image': image_paths[-1],
        })
        run.log(f'VLM raw pixel steps: {json.dumps(pixel_steps)}')

        # 3b. Save debug image with pixel markers — written before deprojection
        #     so it exists even when the plan fails mid-way or deproject errors.
        debug_path = self._save_debug_frame(image_paths[0], pixel_steps, task_dir)
        if debug_path is not None:
            run.save_debug_image(str(debug_path))
        shutil.rmtree(task_dir, ignore_errors=True)

        # 4. Batch-deproject pixel coords → world [x, y, z], then depth-compensate
        #    pick/release z using the table surface height from scene_json (if any).
        table_surface_z = self._extract_table_surface_z(scene_json)
        plan_steps = self._deproject_plan(pixel_steps, table_surface_z)

        self.get_logger().info(
            f'[VLMPlannerNode] Plan done — "{user_command}", steps: {len(plan_steps)}'
        )
        run.log(f'Plan done — "{user_command}", steps: {len(plan_steps)}')
        for s in plan_steps:
            self.get_logger().info(
                f'[VLMPlannerNode]   Step {s["step"]}: {s.get("action_name", "?")}'
            )
            run.log(f'  Step {s["step"]}: {s.get("action_name", "?")}')

        return {
            'task_summary': user_command,
            'reasoning_method': reasoning_method,
            'model': model_name,
            'plan': plan_steps,
        }

    # ── Camera service helpers ─────────────────────────────────────────────────

    def _wait_for_future(self, future, what: str, timeout_sec: float = 10.0):
        """Block until a client-call future resolves, raising on timeout."""
        deadline = time.monotonic() + timeout_sec
        while not future.done():
            if time.monotonic() > deadline:
                raise RuntimeError(f'{what} did not return within {timeout_sec:.0f} s')
            time.sleep(0.05)
        return future.result()

    def _call_get_image(self):
        """Call /camera/get_image asynchronously, polling until the future resolves."""
        if not self._get_image_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/camera/get_image service not available (timeout 5 s)')
        future = self._get_image_client.call_async(GetImage.Request())
        return self._wait_for_future(future, '/camera/get_image')

    def _save_debug_frame(self, source_path: str, pixel_steps: list, task_dir: Path) -> 'Path | None':
        """Overlay VLM pixel predictions on the raw saved frame and write debug.png.

        Pixel fields are either a [x, y] (col, row) point — the VLM's native
        point-grounding convention — or a [x_min, y_min, x_max, y_max] pixel
        bounding box (bbox grounding mode). We read points directly as
        (u=x, v=y); boxes are drawn as a rectangle plus a center marker.

        Out-of-bounds predictions (e.g. a row/col outside the actual frame
        size — a real failure mode we've seen from the VLM) are clamped to
        the nearest edge and drawn in magenta with a "!" suffix instead of
        being silently skipped, so a mispointing bias is still visible in
        the debug image rather than just producing a deproject error later.
        """
        PIXEL_FIELDS = ('target_position', 'release_position')
        entries = []  # ('point', u, v) or ('box', x0, y0, x1, y1)
        for step in pixel_steps:
            for field in PIXEL_FIELDS:
                val = step.get(field)
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    x, y = val
                    entries.append(('point', int(x), int(y)))  # (u, v) = (x, y)
                elif isinstance(val, (list, tuple)) and len(val) == 4:
                    x_min, y_min, x_max, y_max = val
                    entries.append(('box', int(x_min), int(y_min), int(x_max), int(y_max)))
        if not entries:
            return None
        frame = self._cv2.imread(source_path)
        if frame is None:
            return None
        height, width = frame.shape[:2]
        marker_size = 14
        radius = marker_size // 2
        for index, entry in enumerate(entries, start=1):
            kind = entry[0]
            if kind == 'point':
                _, u, v = entry
                out_of_bounds = u < 0 or v < 0 or u >= width or v >= height
                cu = min(max(u, 0), width - 1)
                cv = min(max(v, 0), height - 1)
                color = (255, 0, 255) if out_of_bounds else (0, 255, 255)
                x0 = max(0, cu - radius)
                y0 = max(0, cv - radius)
                x1 = min(width - 1, cu + radius)
                y1 = min(height - 1, cv + radius)
                self._cv2.rectangle(frame, (x0, y0), (x1, y1), color, 2)
                self._cv2.drawMarker(
                    frame, (cu, cv), (0, 0, 255),
                    markerType=self._cv2.MARKER_CROSS,
                    markerSize=max(marker_size, 10), thickness=2,
                )
                label_anchor = (min(width - 1, x1 + 4), max(12, y0 - 4))
            else:
                _, bx0, by0, bx1, by2 = entry
                out_of_bounds = (
                    bx0 < 0 or by0 < 0 or bx1 >= width or by2 >= height
                    or bx0 >= bx1 or by0 >= by2
                )
                x0 = min(max(bx0, 0), width - 1)
                y0 = min(max(by0, 0), height - 1)
                x1 = min(max(bx1, 0), width - 1)
                y1 = min(max(by2, 0), height - 1)
                color = (255, 0, 255) if out_of_bounds else (0, 255, 255)
                self._cv2.rectangle(frame, (x0, y0), (x1, y1), color, 2)
                cu, cv = (x0 + x1) // 2, (y0 + y1) // 2
                self._cv2.drawMarker(
                    frame, (cu, cv), (0, 0, 255),
                    markerType=self._cv2.MARKER_CROSS,
                    markerSize=max(marker_size, 10), thickness=2,
                )
                label_anchor = (min(width - 1, x1 + 4), max(12, y0 - 4))
            label = f'{index}!' if out_of_bounds else str(index)
            self._cv2.putText(
                frame, label, label_anchor,
                self._cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1,
                self._cv2.LINE_AA,
            )
        debug_path = task_dir / 'debug.png'
        self._cv2.imwrite(str(debug_path), frame)
        self.get_logger().info(f'[VLMPlannerNode] Saved debug frame → {debug_path}')
        return debug_path

    def _save_frame(self, ros_image, task_dir: Path, index: int) -> list:
        """Decode a sensor_msgs/Image and write it to disk. Returns [path_str]."""
        stamp_ns = (
            ros_image.header.stamp.sec * 10 ** 9
            + ros_image.header.stamp.nanosec
        )
        path = task_dir / f'{index:04d}_{stamp_ns}.png'
        cv_img = self._bridge.imgmsg_to_cv2(ros_image, desired_encoding='bgr8')
        self._cv2.imwrite(str(path), cv_img)
        return [str(path)]

    @staticmethod
    def _extract_table_surface_z(scene_json: str):
        """Read workspace.table.surface_z out of the request's scene_json, if any.

        VLM mode doesn't use scene_json for object grounding (the camera
        provides that), but the table height is a fixed physical constant we
        still need as a depth reference — see _deproject_plan.
        """
        try:
            data = json.loads(scene_json) if scene_json else {}
            return float(data['workspace']['table']['surface_z'])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _deproject_plan(self, pixel_steps: list, table_surface_z: 'float | None') -> list:
        """Replace pixel fields with deprojected [x, y, z] world coords, then
        depth-compensate pick/release z for the gripper's grasp geometry.

        The VLM outputs pixel coordinates in one of two grounding modes
        (see settings.VLM_GROUNDING_MODE):
          - 'point': [x, y] — a single center pixel per field.
          - 'bbox' : [x_min, y_min, x_max, y_max] — a pixel bounding box.
        In both cases x = column index → camera u, y = row index → camera v.

        Collects all pixel-coordinate fields (deprojecting a bbox's center
        pixel), issues a single batched Deproject call, then substitutes the
        results back in place. For a pick step's bounding box, two extra
        points (the box's left-edge and right-edge midpoints) are deprojected
        in the same batch and their real-world (base-frame) distance replaces
        the VLM's blind grasp_width guess with a vision-grounded estimate.

        Depth compensation (only when table_surface_z is known): a pick's
        target_position.z from deprojection is the object's raw TOP SURFACE
        point, not a good gripper contact height, and the VLM's per-step
        object_height guess has no access to depth. So for every pick step we
        instead compute a real object_height = table_surface_z -
        top_surface_z, overwrite the step's object_height with it, and lower
        the pick target by a fraction of that height (mid-body grasp) rather
        than closing the gripper right at the top surface — capped by
        TCP_CLAMP_CLEARANCE_M so tall objects are gripped nearer their top
        instead of driving the rigid TCP clamp into the object. The computed
        height is then carried forward to the next release step (replacing
        its guessed object_height) so the TCP is raised by the correct
        amount when placing the same held object.
        """
        PIXEL_FIELDS = ('target_position', 'release_position')

        req_u, req_v = [], []

        def _add_pixel(u, v) -> int:
            req_u.append(int(u))
            req_v.append(int(v))
            return len(req_u) - 1

        # (step_idx, field, center_batch_idx, width_batch_idx_pair_or_None)
        entries = []
        for i, step in enumerate(pixel_steps):
            for field in PIXEL_FIELDS:
                val = step.get(field)
                if not isinstance(val, (list, tuple)):
                    continue
                if len(val) == 2:
                    center_idx = _add_pixel(val[0], val[1])
                    entries.append((i, field, center_idx, None))
                elif len(val) == 4:
                    x_min, y_min, x_max, y_max = val
                    cx, cy = (x_min + x_max) / 2.0, (y_min + y_max) / 2.0
                    center_idx = _add_pixel(cx, cy)
                    width_pair = None
                    if field == 'target_position' and step.get('action_name') == 'pick':
                        left_idx = _add_pixel(x_min, cy)
                        right_idx = _add_pixel(x_max, cy)
                        width_pair = (left_idx, right_idx)
                    entries.append((i, field, center_idx, width_pair))

        if not entries:
            return pixel_steps

        req = Deproject.Request()
        req.u = req_u
        req.v = req_v

        if not self._deproject_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/camera/deproject service not available (timeout 5 s)')
        future = self._deproject_client.call_async(req)
        dep = self._wait_for_future(future, '/camera/deproject')

        if not dep.success:
            raise RuntimeError(f'Deproject failed: {dep.error_message}')

        plan_steps = copy.deepcopy(pixel_steps)
        for i, field, center_idx, width_pair in entries:
            pt = dep.points[center_idx]
            plan_steps[i][field] = [pt.x, pt.y, pt.z]
            if width_pair is not None:
                left_idx, right_idx = width_pair
                lp, rp = dep.points[left_idx], dep.points[right_idx]
                real_width = ((lp.x - rp.x) ** 2 + (lp.y - rp.y) ** 2) ** 0.5
                plan_steps[i]['grasp_width'] = real_width
                self.get_logger().info(
                    f'[VLMPlannerNode] pick: bbox-derived grasp_width={real_width:.3f} m '
                    f'(overriding VLM estimate)'
                )

        if table_surface_z is not None:
            fraction = settings.PICK_GRASP_DEPTH_FRACTION
            min_height = settings.MIN_OBJECT_HEIGHT_M
            # A tall object can't be grasped past this depth below its top
            # surface without the rigid TCP clamp (not the pivoting fingers)
            # crashing into it on the way down — see TCP_CLAMP_CLEARANCE_M.
            max_descent = max(settings.TCP_OFFSET_Z - settings.TCP_CLAMP_CLEARANCE_M, 0.0)
            held_release_lift = None
            for step in plan_steps:
                action = step.get('action_name')
                if action == 'pick' and isinstance(step.get('target_position'), list):
                    top_z = step['target_position'][2]
                    height = max(top_z - table_surface_z, min_height)
                    step['object_height'] = height
                    descent = min(fraction * height, max_descent)
                    step['target_position'][2] = top_z - descent
                    # The executor's release step lifts the TCP by
                    # object_height, assuming the object was grasped right at
                    # its top. But this is a mid-body (or clamp-capped) grasp
                    # — the contact point sits `descent` below the top, so
                    # only `height - descent` of the object hangs below the
                    # TCP. Passing the full height overshoots the release
                    # height by `descent` (previously ~half the object's
                    # height for typical uncapped objects).
                    held_release_lift = height - descent
                    self.get_logger().info(
                        f'[VLMPlannerNode] pick: depth-computed object_height={height:.3f} m, '
                        f'target z {top_z:.3f} -> {step["target_position"][2]:.3f}'
                    )
                elif action == 'release' and held_release_lift is not None:
                    step['object_height'] = held_release_lift
                    held_release_lift = None

        return plan_steps


def main(args=None):
    rclpy.init(args=args)
    node = VLMPlannerNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
