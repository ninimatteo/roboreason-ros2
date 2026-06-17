"""
VLMPlannerNode — exposes /plan_task service for vision-based planning.

Captures an RGB frame from /camera/get_image, runs EmbodiedAgent with a VLM
client (pixel-coordinate outputs), then batch-deprojects pixel coords to world
[x, y, z] via /camera/deproject before returning the plan.

The scene_json field from the PlanTask request is ignored — the camera provides
all perception.

ROS2 parameters:
  reasoning_method  (str,   default 'fhp')                    — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str,   default 'groq/llama4-scout-17b')
  temperature       (float, default 0.1)
  tmp_dir           (str,   default '/root/ws/src/vlm_frames') — where to save captured frames
"""

import copy
import json
import time
import traceback
import uuid
from pathlib import Path

import dotenv
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import Deproject, GetImage, PlanTask
from robo_reason_planner.agent_runner import run_plan_loop
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

        self.declare_parameter('reasoning_method', settings.REASONING_METHOD)
        self.declare_parameter('model_name', settings.MODEL_NAME)
        self.declare_parameter('temperature', settings.TEMPERATURE)
        self.declare_parameter('tmp_dir', settings.TMP_DIR)

        dotenv.load_dotenv()

        self._reasoning_method = self.get_parameter('reasoning_method').value
        self._model_name = self.get_parameter('model_name').value
        self._temperature = self.get_parameter('temperature').value
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

        self.get_logger().info(
            f'[VLMPlannerNode] Ready — {self._reasoning_method}, {self._model_name}'
        )

    # ── /plan_task callback ────────────────────────────────────────────────────

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        self.get_logger().info(f'[VLMPlannerNode] Received: "{user_command}"')

        try:
            plan_data = self._vlm_plan(user_command)
            response.success = True
            response.plan_json = json.dumps(plan_data)
            self.get_logger().info('[VLMPlannerNode] Generated VLM plan.')
        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[VLMPlannerNode] Planning error:\n{tb}')
            response.success = False
            response.error_message = tb

        return response

    # ── VLM plan ───────────────────────────────────────────────────────────────

    def _vlm_plan(self, user_command: str) -> dict:
        # 1. Capture RGB frame.
        img_resp = self._call_get_image()
        if not img_resp.success:
            raise RuntimeError(f'GetImage failed: {img_resp.error_message}')

        # 2. Save frame to disk.
        task_dir = self._tmp_root / uuid.uuid4().hex[:8]
        task_dir.mkdir(parents=True, exist_ok=True)
        image_paths = self._save_frame(img_resp.image, task_dir, index=0)
        self.get_logger().info(f'[VLMPlannerNode] Saved frame → {image_paths[0]}')

        # 3. Run VLM agent — returns actions with pixel [h, w] coordinates.
        agent = EmbodiedAgent(
            reasoning_mode=self._reasoning_method,
            client_parameters={
                'model_name': self._model_name,
                'temperature': self._temperature,
            },
            client_type='vlm',
        )

        pixel_steps = run_plan_loop(agent, {
            'user_request': user_command,
            'image': image_paths[-1],
        })

        # 4. Batch-deproject pixel coords → world [x, y, z].
        plan_steps = self._deproject_plan(pixel_steps)

        self.get_logger().info(
            f'[VLMPlannerNode] Plan done — "{user_command}", steps: {len(plan_steps)}'
        )
        for s in plan_steps:
            self.get_logger().info(
                f'[VLMPlannerNode]   Step {s["step"]}: {s.get("action_name", "?")}'
            )

        return {
            'task_summary': user_command,
            'reasoning_method': self._reasoning_method,
            'model': self._model_name,
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
        """Replace pixel [h, w] fields with deprojected [x, y, z] world coords.

        The VLM outputs pixel coordinates as [h, w] where:
          h = row index (y-axis, top→bottom)  → camera v
          w = column index (x-axis, left→right) → camera u

        Collects all pixel-coordinate fields, issues a single batched Deproject
        call, then substitutes the results back in place.
        """
        PIXEL_FIELDS = ('target_position', 'release_position')

        pending = []
        for i, step in enumerate(pixel_steps):
            for field in PIXEL_FIELDS:
                val = step.get(field)
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    pending.append((i, field, val))

        if not pending:
            return pixel_steps

        req = Deproject.Request()
        req.u = [int(p[2][1]) for p in pending]   # w (column) → u
        req.v = [int(p[2][0]) for p in pending]   # h (row)    → v

        if not self._deproject_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/camera/deproject service not available (timeout 5 s)')
        future = self._deproject_client.call_async(req)
        dep = self._wait_for_future(future, '/camera/deproject')

        if not dep.success:
            raise RuntimeError(f'Deproject failed: {dep.error_message}')

        plan_steps = copy.deepcopy(pixel_steps)
        for k, (i, field, _) in enumerate(pending):
            pt = dep.points[k]
            plan_steps[i][field] = [pt.x, pt.y, pt.z]

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
