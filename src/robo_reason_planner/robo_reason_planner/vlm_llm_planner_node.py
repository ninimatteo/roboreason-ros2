"""
VLMLLMPlannerNode — exposes /plan_task service for the VLM->LLM hybrid pipeline.

Two-stage planning:
  1. Capture an RGB frame from /camera/get_image and call a VLM once with a
     scene-description prompt (SceneGrounder) to detect objects/targets as
     pixel centers. Batch-deproject those pixel centers to world [x, y, z]
     via /camera/deproject (same pattern as vlm_planner_node's action-pixel
     deprojection), then assemble a new scene_mock.json-shaped JSON file
     (frame/units/robot/workspace copied from the request's scene_json;
     objects/targets replaced with the VLM detections). This file is written
     to a new path under tmp_dir — the real scene_mock.json is never touched.
  2. Run the standard LLM planning pipeline (EmbodiedAgent(client_type='llm'))
     grounded on that generated scene JSON, exactly like llm_planner_node.

ROS2 parameters:
  reasoning_method  (str,   default 'fhp')                    — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str,   default settings.MODEL_NAME)       — LLM planning model
  temperature       (float, default settings.TEMPERATURE)      — LLM planning temperature
  vlm_model_name    (str,   default settings.VLM_MODEL_NAME)   — vision-capable model for scene grounding
  vlm_temperature   (float, default settings.VLM_TEMPERATURE)  — scene-grounding temperature
  reasoning_effort  (str,   default settings.VLM_REASONING_EFFORT) — Groq-only Qwen3 <think>
                                                                  control for the scene-grounding
                                                                  call (e.g. 'none'); empty string
                                                                  omits the param entirely
  tmp_dir           (str,   default settings.TMP_DIR)          — where to save frames + generated scene JSON
"""

import copy
import json
import re
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
from robo_reason_reasoning.scene_grounder import SceneGrounder

_SLUG_RE = re.compile(r'[^a-z0-9]+')


def _slugify(label: str, fallback: str) -> str:
    slug = _SLUG_RE.sub('_', (label or '').strip().lower()).strip('_')
    return slug or fallback


class VLMLLMPlannerNode(Node):
    """
    VLM->LLM hybrid planner node.

    Uses a ReentrantCallbackGroup so that the /plan_task service callback can
    issue GetImage and Deproject client calls without deadlocking on the
    MultiThreadedExecutor (same requirement as vlm_planner_node).
    """

    def __init__(self):
        super().__init__('vlm_llm_planner_node')

        # All planning/grounding knobs are read per-request in _vlm_llm_plan
        # so the GUI can retune live without relaunching. tmp_dir is a static
        # path root, so it stays cached.
        self.declare_parameter('reasoning_method', settings.REASONING_METHOD)
        self.declare_parameter('model_name', settings.MODEL_NAME)
        self.declare_parameter('temperature', settings.TEMPERATURE)
        self.declare_parameter('vlm_model_name', settings.VLM_MODEL_NAME)
        self.declare_parameter('vlm_temperature', settings.VLM_TEMPERATURE)
        self.declare_parameter('reasoning_effort', settings.VLM_REASONING_EFFORT)
        self.declare_parameter('tmp_dir', settings.TMP_DIR)

        dotenv.load_dotenv()

        self._tmp_root = Path(self.get_parameter('tmp_dir').value)

        try:
            import cv2 as _cv2
            from cv_bridge import CvBridge
            self._bridge = CvBridge()
            self._cv2 = _cv2
        except ImportError as exc:
            raise RuntimeError(
                f'[VLMLLMPlannerNode] VLM_LLM mode requires cv_bridge and opencv-python: {exc}'
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
            f"[VLMLLMPlannerNode] Ready — grounding: {self.get_parameter('vlm_model_name').value}, "
            f"planning: {self.get_parameter('reasoning_method').value}/"
            f"{self.get_parameter('model_name').value} (params read per request)"
        )

    # ── /plan_task callback ────────────────────────────────────────────────────

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        scene_json = request.scene_json
        self.get_logger().info(f'[VLMLLMPlannerNode] Received: "{user_command}"')

        run = DebugRun(mode='VLM_LLM', command=user_command, config={
            'reasoning_method': self.get_parameter('reasoning_method').value,
            'model_name': self.get_parameter('model_name').value,
            'temperature': self.get_parameter('temperature').value,
            'vlm_model_name': self.get_parameter('vlm_model_name').value,
            'vlm_temperature': self.get_parameter('vlm_temperature').value,
            'reasoning_effort': self.get_parameter('reasoning_effort').value,
        })

        try:
            plan_data = self._vlm_llm_plan(user_command, scene_json, run)
            response.success = True
            response.plan_json = json.dumps(plan_data)
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=True, response=plan_data)
            self.get_logger().info('[VLMLLMPlannerNode] Generated VLM+LLM plan.')
        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[VLMLLMPlannerNode] Planning error:\n{tb}')
            response.success = False
            response.error_message = tb
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=False, error=tb)

        return response

    # ── VLM+LLM plan ───────────────────────────────────────────────────────────

    def _vlm_llm_plan(self, user_command: str, scene_json: str, run: DebugRun) -> dict:
        # 1. Capture RGB frame.
        img_resp = self._call_get_image()
        if not img_resp.success:
            raise RuntimeError(f'GetImage failed: {img_resp.error_message}')

        task_dir = self._tmp_root / uuid.uuid4().hex[:8]
        task_dir.mkdir(parents=True, exist_ok=True)
        image_path = self._save_frame(img_resp.image, task_dir)
        self.get_logger().info(f'[VLMLLMPlannerNode] Saved frame → {image_path}')
        run.log(f'Saved frame -> {image_path}')
        run.save_raw_frame(image_path)

        # 2. VLM scene-grounding call — detect objects/targets as pixel centers.
        vlm_model_name = self.get_parameter('vlm_model_name').value
        vlm_temperature = self.get_parameter('vlm_temperature').value
        reasoning_effort = self.get_parameter('reasoning_effort').value
        grounding_client_parameters = {
            'model_name': vlm_model_name,
            'temperature': vlm_temperature,
        }
        if reasoning_effort:
            # Omitted unless explicitly set — see VLM_REASONING_EFFORT in config.py.
            grounding_client_parameters['reasoning_effort'] = reasoning_effort
        grounder = SceneGrounder(client_parameters=grounding_client_parameters)
        detected = grounder.ground_scene(image_path)
        self.get_logger().info(
            f'[VLMLLMPlannerNode] Detected {len(detected.objects)} object(s), '
            f'{len(detected.targets)} target(s).'
        )
        run.log(
            f'VLM detected {len(detected.objects)} objects, {len(detected.targets)} targets: '
            f'{detected.model_dump_json()}'
        )

        # 2b. Debug overlay of detected pixel centers — written before deprojection
        #     so it exists even if deprojection fails.
        debug_path = self._save_debug_frame(image_path, detected, task_dir)
        if debug_path is not None:
            run.save_debug_image(str(debug_path))
        shutil.rmtree(task_dir, ignore_errors=True)

        # 3. Batch-deproject pixel centers → world [x, y, z] and assemble the
        #    generated scene JSON (workspace/robot copied unmodified from
        #    scene_json; objects/targets replaced with the detections).
        generated_scene = self._build_generated_scene(scene_json, detected)

        generated_scene_path = task_dir / 'generated_scene.json'
        generated_scene_path.write_text(json.dumps(generated_scene, indent=2))
        self.get_logger().info(f'[VLMLLMPlannerNode] Saved generated scene → {generated_scene_path}')
        run.log(f'Saved generated scene -> {generated_scene_path}')
        run.save_generated_scene(str(generated_scene_path))

        # 4. Standard LLM planning, grounded on the generated scene JSON.
        reasoning_method = self.get_parameter('reasoning_method').value
        model_name = self.get_parameter('model_name').value
        temperature = self.get_parameter('temperature').value

        agent = EmbodiedAgent(
            reasoning_mode=reasoning_method,
            client_parameters={
                'model_name': model_name,
                'temperature': temperature,
            },
            client_type='llm',
        )

        observation = {
            'user_request': user_command,
            'environment_map': json.dumps(generated_scene),
        }
        plan_steps = run_plan_loop(agent, observation)

        self.get_logger().info(
            f'[VLMLLMPlannerNode] Plan done — "{user_command}", steps: {len(plan_steps)}'
        )
        run.log(f'Plan done — "{user_command}", steps: {len(plan_steps)}')
        for s in plan_steps:
            self.get_logger().info(
                f'[VLMLLMPlannerNode]   Step {s["step"]}: {s.get("action_name", "?")}'
            )
            run.log(f'  Step {s["step"]}: {s.get("action_name", "?")}')

        return {
            'task_summary': user_command,
            'reasoning_method': reasoning_method,
            'model': model_name,
            'vlm_model': vlm_model_name,
            'generated_scene_path': str(generated_scene_path),
            'plan': plan_steps,
        }

    # ── Scene assembly ──────────────────────────────────────────────────────────

    def _build_generated_scene(self, scene_json: str, detected) -> dict:
        """Merge VLM-detected objects/targets (deprojected to world xyz) into a
        scene_mock.json-shaped dict, keeping frame/units/robot/workspace as-is.

        Depth-informed grasp height: the LLM planning prompt sets
        `target_position.z = object.position.z` verbatim for pick actions (see
        fhp_ffhp_prompts.py), so position.z here must already BE a good pick
        contact height — not the raw deprojected top-surface point. The VLM's
        size[2] (height) is also just a blind visual guess with no depth
        grounding. When workspace.table.surface_z is known we replace both:
        real height = surface_z - top_surface_z, and position.z descends a
        fraction of that height below the top surface (mid-body grasp),
        capped by TCP_CLAMP_CLEARANCE_M so tall objects are gripped nearer
        their top instead of driving the rigid TCP clamp into the object.

        Targets get the same depth-informed treatment for an analogous
        reason: the LLM stacking formula (release_position.z =
        target.position.z + target.size[2] — see fhp_ffhp_prompts.py)
        expects position.z to be a *base* reference and size[2] the height
        to reach the top surface, matching scene_mock.json's convention.
        The VLM's size[2] guess for a target is not depth-grounded, so
        using it verbatim while also using the (already-correct, real)
        deprojected top_z as position.z would double-count the target's
        height. Instead we derive the real height from depth (surface_z -
        top_z, no MIN_OBJECT_HEIGHT_M floor — a flat zone marked directly on
        the table should read ~0 height, not be lifted) and set
        position.z = top_z - height, so position.z + size[2] reconstructs
        the true measured top surface.
        """
        try:
            base = json.loads(scene_json) if scene_json else {}
        except json.JSONDecodeError:
            base = {}
        base.setdefault('frame', 'base_link')
        base.setdefault('units', 'meters')

        table_surface_z = None
        try:
            table_surface_z = float(base['workspace']['table']['surface_z'])
        except (KeyError, TypeError, ValueError):
            pass

        pixel_centers = [obj.pixel_center for obj in detected.objects]
        pixel_centers += [tgt.pixel_center for tgt in detected.targets]
        points = self._deproject_points(pixel_centers)

        n_objects = len(detected.objects)
        object_points = points[:n_objects]
        target_points = points[n_objects:]

        fraction = settings.PICK_GRASP_DEPTH_FRACTION
        min_height = settings.MIN_OBJECT_HEIGHT_M
        # A tall object can't be grasped past this depth below its top
        # surface without the rigid TCP clamp (not the pivoting fingers)
        # crashing into it on the way down — see TCP_CLAMP_CLEARANCE_M.
        max_descent = max(settings.TCP_OFFSET_Z - settings.TCP_CLAMP_CLEARANCE_M, 0.0)

        objects = {}
        used_keys = set()
        for obj, pt in zip(detected.objects, object_points):
            key = self._unique_key(obj.label, used_keys, 'object')
            top_z = pt.z
            size = list(obj.size)
            position_z = top_z
            if table_surface_z is not None:
                height = max(top_z - table_surface_z, min_height)
                descent = min(fraction * height, max_descent)
                # size[2] here becomes the LLM's `object_height` on the
                # paired release action verbatim (fhp_ffhp_prompts.py: "set
                # object_height to size[2] of the held object"), which the
                # executor adds to the release z. Since the grasp is
                # mid-body/clamp-capped (`descent` below the top, not at the
                # top), only `height - descent` of the object hangs below
                # the TCP — passing the full height overshoots the release
                # height by `descent`.
                size[2] = height - descent
                position_z = top_z - descent
            objects[key] = {
                'type': obj.type,
                'color': obj.color,
                'position': [pt.x, pt.y, position_z],
                'size': size,
                'state': obj.state,
                'graspable': obj.graspable,
            }

        targets = {}
        for tgt, pt in zip(detected.targets, target_points):
            key = self._unique_key(tgt.label, used_keys, 'target')
            top_z = pt.z
            size = list(tgt.size)
            position_z = top_z
            if table_surface_z is not None:
                height = max(top_z - table_surface_z, 0.0)
                size[2] = height
                position_z = top_z - height
            targets[key] = {
                'type': tgt.type,
                'color': tgt.color,
                'label': tgt.label,
                'position': [pt.x, pt.y, position_z],
                'size': size,
            }

        base['objects'] = objects
        base['targets'] = targets
        return base

    @staticmethod
    def _unique_key(label: str, used_keys: set, fallback_prefix: str) -> str:
        key = _slugify(label, f'{fallback_prefix}_{len(used_keys)}')
        original = key
        suffix = 1
        while key in used_keys:
            key = f'{original}_{suffix}'
            suffix += 1
        used_keys.add(key)
        return key

    def _deproject_points(self, pixel_centers: list) -> list:
        """Batch-deproject a list of [x, y] pixel centers → world points.

        Note: the VLM scene-grounding prompt asks for [x, y] (not [h, w])
        because Qwen-family vision models consistently emit pixel points in
        native (x, y) grounding order regardless of what the prompt asks for
        — see scene_description_prompts.py.
        """
        if not pixel_centers:
            return []

        req = Deproject.Request()
        req.u = [int(p[0]) for p in pixel_centers]   # x (column) → u
        req.v = [int(p[1]) for p in pixel_centers]   # y (row)    → v

        if not self._deproject_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/camera/deproject service not available (timeout 5 s)')
        future = self._deproject_client.call_async(req)
        dep = self._wait_for_future(future, '/camera/deproject')

        if not dep.success:
            raise RuntimeError(f'Deproject failed: {dep.error_message}')

        return list(dep.points)

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

    def _save_frame(self, ros_image, task_dir: Path) -> str:
        """Decode a sensor_msgs/Image and write it to disk. Returns the path."""
        stamp_ns = (
            ros_image.header.stamp.sec * 10 ** 9
            + ros_image.header.stamp.nanosec
        )
        path = task_dir / f'0000_{stamp_ns}.png'
        cv_img = self._bridge.imgmsg_to_cv2(ros_image, desired_encoding='bgr8')
        self._cv2.imwrite(str(path), cv_img)
        return str(path)

    def _save_debug_frame(self, source_path: str, detected, task_dir: Path) -> 'Path | None':
        """Overlay detected object/target pixel centers on the raw frame.

        pixel_center fields are [x, y] (col, row); drawn directly as (u=x,
        v=y). Objects are drawn in cyan, targets in yellow, each labeled with
        its detected name.
        """
        entries = [(obj.label, obj.pixel_center, (0, 255, 255)) for obj in detected.objects]
        entries += [(tgt.label, tgt.pixel_center, (0, 255, 0)) for tgt in detected.targets]
        if not entries:
            return None

        frame = self._cv2.imread(source_path)
        if frame is None:
            return None
        height, width = frame.shape[:2]
        marker_size = 14
        radius = marker_size // 2

        for label, (x, y), color in entries:
            u, v = int(x), int(y)
            cu = min(max(u, 0), width - 1)
            cv = min(max(v, 0), height - 1)
            self._cv2.drawMarker(
                frame, (cu, cv), (0, 0, 255),
                markerType=self._cv2.MARKER_CROSS,
                markerSize=max(marker_size, 10), thickness=2,
            )
            self._cv2.putText(
                frame, label,
                (min(width - 1, cu + radius + 4), max(12, cv - 4)),
                self._cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1,
                self._cv2.LINE_AA,
            )

        debug_path = task_dir / 'debug.png'
        self._cv2.imwrite(str(debug_path), frame)
        self.get_logger().info(f'[VLMLLMPlannerNode] Saved debug frame → {debug_path}')
        return debug_path


def main(args=None):
    rclpy.init(args=args)
    node = VLMLLMPlannerNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
