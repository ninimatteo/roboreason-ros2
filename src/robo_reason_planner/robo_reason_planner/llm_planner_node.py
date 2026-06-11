"""
llm_planner_node — exposes /plan_task service.

Translates a user command + scene JSON into a discrete skill plan.
  - mode='LLM' (default): uses EmbodiedAgent + Groq LLM, or a deterministic mock.
  - mode='VLM': calls the camera node (GetImage, Deproject) and EmbodiedAgent(client_type='vlm').

ROS2 parameters:
  mode              (str,   default 'LLM')                — 'LLM' or 'VLM'
  use_mock_llm      (bool,  default true)                 — LLM mode only
  reasoning_method  (str,   default 'fhp')                — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str,   default 'groq/llama4-scout-17b')
  temperature       (float, default 0.1)
  tmp_dir           (str,   default '/tmp/roboreason_vlm') — VLM mode: where to save frames
"""

import copy
import json
import time
import uuid
from pathlib import Path

import dotenv
import rclpy
import traceback
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from robo_reason_interfaces.srv import Deproject, GetImage, PlanTask
from robo_reason_planner.command_grounding import check_command_grounding
from robo_reason_reasoning.embodied_agent import EmbodiedAgent


class LLMPlannerNode(Node):

    def __init__(self):
        super().__init__('llm_planner_node')

        self.declare_parameter('mode', 'LLM')
        self.declare_parameter('use_mock_llm', True)
        self.declare_parameter('reasoning_method', 'fhp')
        self.declare_parameter('model_name', 'groq/llama4-scout-17b')
        self.declare_parameter('temperature', 0.1)
        self.declare_parameter('tmp_dir', '/tmp/roboreason_vlm')

        dotenv.load_dotenv()

        self._mode = self.get_parameter('mode').value.upper()
        self._use_mock = self.get_parameter('use_mock_llm').value
        self._reasoning_method = self.get_parameter('reasoning_method').value
        self._model_name = self.get_parameter('model_name').value
        self._temperature = self.get_parameter('temperature').value
        self._tmp_root = Path(self.get_parameter('tmp_dir').value)

        # ReentrantCallbackGroup is required in VLM mode: the /plan_task service
        # callback calls GetImage and Deproject as a client — that would deadlock
        # on a single-threaded executor without it.
        self._cb_group = ReentrantCallbackGroup()

        self._service = self.create_service(
            PlanTask, '/plan_task', self._plan_task_callback,
            callback_group=self._cb_group,
        )

        if self._mode == 'VLM':
            self._setup_vlm()

        if self._mode == 'VLM':
            label = f'VLM ({self._reasoning_method}, {self._model_name})'
        elif self._use_mock:
            label = 'LLM (MOCK)'
        else:
            label = f'LLM ({self._reasoning_method}, {self._model_name})'

        self.get_logger().info(f'[LLMPlanner] Ready — mode: {label}')

    # ── VLM setup ──────────────────────────────────────────────────────────────

    def _setup_vlm(self):
        try:
            import cv2 as _cv2
            from cv_bridge import CvBridge
            self._bridge = CvBridge()
            self._cv2 = _cv2
        except ImportError as exc:
            raise RuntimeError(
                f'[LLMPlanner] VLM mode requires cv_bridge and opencv-python: {exc}'
            ) from exc

        self._get_image_client = self.create_client(
            GetImage, '/camera/get_image', callback_group=self._cb_group,
        )
        self._deproject_client = self.create_client(
            Deproject, '/camera/deproject', callback_group=self._cb_group,
        )

    # ── /plan_task callback ────────────────────────────────────────────────────

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        scene_json = request.scene_json

        self.get_logger().info(f'[LLMPlanner] Received: "{user_command}"')

        # In LLM mode the scene JSON must contain the objects we need.
        # In VLM mode the camera provides perception — scene_json is ignored.
        if self._mode != 'VLM':
            grounded, err = check_command_grounding(user_command, scene_json)
            if not grounded:
                response.success = False
                response.error_message = err
                return response

        try:
            if self._mode == 'VLM':
                plan_data = self._vlm_plan(user_command)
                self.get_logger().info('[LLMPlanner] Generated VLM plan.')
            elif self._use_mock:
                plan_data = self._mock_plan(user_command, scene_json)
                self.get_logger().info('[LLMPlanner] Generated mock plan.')
            else:
                plan_data = self._llm_plan(user_command, scene_json)
                self.get_logger().info('[LLMPlanner] Generated LLM plan.')

            response.success = True
            response.plan_json = json.dumps(plan_data)

        except Exception as exc:
            self.get_logger().error(f'[LLMPlanner] Planning error: {traceback.format_exc()}')
            response.success = False
            response.error_message = str(exc)

        return response

    # ── VLM plan path ──────────────────────────────────────────────────────────

    def _vlm_plan(self, user_command: str) -> dict:
        # 1. Get the current RGB frame (async, polled to avoid deadlock).
        img_resp = self._call_get_image()
        if not img_resp.success:
            raise RuntimeError(f'GetImage failed: {img_resp.error_message}')

        # 2. Save the frame to disk.  One subfolder per task keeps frames grouped.
        #    Filename: <index>_<ros_stamp_ns>.png — sortable, unique.
        #    The list shape is intentional: a future timeseries will just be
        #    more elements in the same list; the agent contract never changes.
        task_id = uuid.uuid4().hex[:8]
        task_dir = self._tmp_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        image_paths = self._save_frame(img_resp.image, task_dir, index=0)

        self.get_logger().info(f'[LLMPlanner] Saved frame → {image_paths[0]}')

        # 3. Run VLM agent — returns actions with pixel [w, h] coordinates.
        agent = EmbodiedAgent(
            reasoning_mode=self._reasoning_method,
            client_parameters={
                'model_name': self._model_name,
                'temperature': self._temperature,
            },
            client_type='vlm',
        )

        pixel_steps = []
        for step_idx in range(25):
            result = agent.step(observation={
                'user_request': user_command,
                'image': image_paths[-1],   # latest frame path; list keeps timeseries infra ready
            })
            action = result.action
            eos = result.end_of_simulation

            if action.action_name.lower() not in ('idle', 'end_of_simulation'):
                try:
                    action_dict = action.model_dump(exclude_none=True)
                except AttributeError:
                    action_dict = action.dict(exclude_none=True)
                action_dict['step'] = step_idx + 1
                pixel_steps.append(action_dict)

            if eos:
                break

        # 4. Batch-deproject all pixel coords → world [x, y, z].
        plan_steps = self._deproject_plan(pixel_steps)

        self.get_logger().info(
            f'[LLMPlanner] VLM plan — "{user_command}", steps: {len(plan_steps)}'
        )
        for s in plan_steps:
            self.get_logger().info(
                f'[LLMPlanner]   Step {s["step"]}: {s.get("action_name", "?")}'
            )

        return {
            'task_summary': user_command,
            'reasoning_method': self._reasoning_method,
            'model': self._model_name,
            'plan': plan_steps,
        }

    # ── camera service helpers ─────────────────────────────────────────────────

    def _call_get_image(self):
        """Call /camera/get_image asynchronously, polling until the future resolves."""
        if not self._get_image_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/camera/get_image service not available (timeout 5 s)')
        future = self._get_image_client.call_async(GetImage.Request())
        while not future.done():
            time.sleep(0.05)
        return future.result()

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

    def _deproject_plan(self, pixel_steps: list) -> list:
        """Replace all pixel [w, h] fields with deprojected [x, y, z] coords.

        Collects every pixel-coordinate field across all steps, issues a single
        batched Deproject call, then substitutes the results back in place.
        """
        PIXEL_FIELDS = ('target_position', 'release_position')

        # pending: list of (step_index, field_name, [w, h])
        pending = []
        for i, step in enumerate(pixel_steps):
            for field in PIXEL_FIELDS:
                val = step.get(field)
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    pending.append((i, field, val))

        if not pending:
            return pixel_steps

        req = Deproject.Request()
        req.u = [int(p[2][0]) for p in pending]
        req.v = [int(p[2][1]) for p in pending]

        if not self._deproject_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/camera/deproject service not available (timeout 5 s)')
        future = self._deproject_client.call_async(req)
        while not future.done():
            time.sleep(0.05)
        dep = future.result()

        if not dep.success:
            raise RuntimeError(f'Deproject failed: {dep.error_message}')

        plan_steps = copy.deepcopy(pixel_steps)
        for k, (i, field, _) in enumerate(pending):
            pt = dep.points[k]
            plan_steps[i][field] = [pt.x, pt.y, pt.z]

        return plan_steps

    # ── LLM plan path ──────────────────────────────────────────────────────────

    def _llm_plan(self, user_command: str, scene_json: str) -> dict:
        agent = EmbodiedAgent(
            reasoning_mode=self._reasoning_method,
            client_parameters={
                'model_name': self._model_name,
                'temperature': self._temperature,
            },
            client_type='llm',
        )

        observation = {
            'user_request': user_command,
            'environment_map': scene_json,
        }
        self.get_logger().info(f'[LLMPlanner] Starting plan generation with\n{observation}')

        plan_steps = []
        for step_idx in range(25):
            result = agent.step(observation=observation)
            action = result.action
            eos = result.end_of_simulation

            if action.action_name.lower() not in ('idle', 'end_of_simulation'):
                try:
                    action_dict = action.model_dump(exclude_none=True)
                except AttributeError:
                    action_dict = action.dict(exclude_none=True)
                action_dict['step'] = step_idx + 1
                plan_steps.append(action_dict)

            if eos:
                break

        self.get_logger().info(
            f'[LLMPlanner] Plan — "{user_command}", '
            f'method: {self._reasoning_method}, model: {self._model_name}, '
            f'steps: {len(plan_steps)}'
        )
        for s in plan_steps:
            self.get_logger().info(
                f'[LLMPlanner]   Step {s["step"]}: {s.get("action_name", "?")}'
            )

        return {
            'task_summary': user_command,
            'reasoning_method': self._reasoning_method,
            'model': self._model_name,
            'plan': plan_steps,
        }

    def _mock_plan(self, user_command: str, scene_json: str) -> dict:
        scene = json.loads(scene_json)
        objects = scene.get('objects', {})
        targets = scene.get('targets', {})

        first_obj = next(
            (obj for obj in objects.values() if obj.get('graspable', False)), None
        )
        first_target = next(iter(targets.values()), None)

        if not first_obj or not first_target:
            return {'task_summary': 'No objects/targets found', 'plan': []}

        obj_pos = first_obj['position']
        tgt_pos = first_target['position']
        obj_height = first_obj.get('size', [0, 0, 0.05])[2]

        plan = [
            {'step': 1, 'action_name': 'approach',
             'target_position': obj_pos, 'offset': 0.1, 'approach_direction': 'z'},
            {'step': 2, 'action_name': 'pick',
             'target_position': obj_pos, 'grasp_axis': 'z', 'come_back': True},
            {'step': 3, 'action_name': 'approach',
             'target_position': [tgt_pos[0], tgt_pos[1], tgt_pos[2] + 0.1],
             'offset': 0.0, 'approach_direction': 'z'},
            {'step': 4, 'action_name': 'release',
             'release_position': [tgt_pos[0], tgt_pos[1], tgt_pos[2] + obj_height / 2],
             'come_back': False},
            {'step': 5, 'action_name': 'move_home'},
        ]

        return {
            'task_summary': f'[MOCK] {user_command}',
            'reasoning_method': 'mock',
            'plan': plan,
        }


def main(args=None):
    rclpy.init(args=args)
    node = LLMPlannerNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
