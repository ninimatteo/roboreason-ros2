"""
agent_node: the closed loop VLM agent (mode AGENT).

Takes the place of both the planner and the plan manager, behind the same
service names, so the GUI flow is unchanged:

  plan_task        the model looks at the scene and submits a draft plan
                   (high level steps, no coordinates); the GUI shows it
  execute_plan     the same conversation continues: the model measures with
                   the depth camera, acts one skill sequence at a time on
                   execute_skill, sees a fresh image after each action, and
                   repairs what went wrong until it calls finish
  cancel_execution emergency stop: cancel the skill in flight, stop the
                   loop, send the arm home
  cancel_planning  stop button while the draft is being written

Every task writes a DebugRun folder (mode AGENT) with the full transcript and
every image the model saw.

ROS2 parameters (read per request, so the GUI can retune them live):
  model_name, temperature, reasoning_effort
  max_model_calls, max_duration_s, keep_images
  coords   'auto' | 'pixel' | 'norm1000' (auto: norm1000 for Qwen models)
"""

import json
import math
import threading
import time
import traceback
import uuid
from pathlib import Path

import dotenv
import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tf2_ros import Buffer, TransformListener

from robo_reason_bringup.config import settings
from robo_reason_bringup.interfaces import names
from robo_reason_interfaces.action import ExecuteSkill
from robo_reason_interfaces.srv import CancelExecution, Deproject, ExecutePlan, GetImage, PlanTask
from robo_reason_planner.agent.geometry import GraspGeometry
from robo_reason_planner.agent.session import AgentCancelled, AgentSession
from robo_reason_planner.agent.tool_chat import ToolChatClient
from robo_reason_planner.agent.tools import Toolbox, ToolboxConfig
from robo_reason_planner.debug_recorder import DebugRun, fetch_terminal_logs
from robo_reason_planner.planning_cancel import PlanningCancellation, PlanningCancelled

SKILL_TIMEOUT_S = 60.0


class AgentNode(Node):
    def __init__(self):
        super().__init__('agent_node')
        self.declare_parameter('model_name', settings.AGENT_MODEL_NAME)
        self.declare_parameter('temperature', settings.TEMPERATURE)
        self.declare_parameter('reasoning_effort', '')
        self.declare_parameter('max_model_calls', settings.AGENT_MAX_MODEL_CALLS)
        self.declare_parameter('max_duration_s', settings.AGENT_MAX_DURATION_S)
        self.declare_parameter('keep_images', settings.AGENT_KEEP_IMAGES)
        self.declare_parameter('coords', 'auto')
        self.declare_parameter('image_width', settings.AGENT_IMAGE_WIDTH)
        self.declare_parameter('history_turns', settings.AGENT_HISTORY_TURNS)
        self.declare_parameter('max_input_tokens', settings.AGENT_MAX_INPUT_TOKENS)
        self.declare_parameter('tpm_limit', -1)   # -1: AGENT_TPM_LIMITS for the provider
        dotenv.load_dotenv()

        import cv2
        from cv_bridge import CvBridge
        self._cv2, self._bridge = cv2, CvBridge()

        cb = ReentrantCallbackGroup()
        self.create_service(PlanTask, names.plan_task, self._on_plan, callback_group=cb)
        self.create_service(ExecutePlan, names.execute_plan, self._on_execute, callback_group=cb)
        self.create_service(CancelExecution, names.cancel_execution, self._on_cancel, callback_group=cb)
        self._cancellation = PlanningCancellation(self, cb)
        self._get_image = self.create_client(GetImage, names.get_image, callback_group=cb)
        self._deproject = self.create_client(Deproject, names.deproject, callback_group=cb)
        self._terminal_logs = self.create_client(Trigger, names.terminal_logs, callback_group=cb)
        self._skills = ActionClient(self, ExecuteSkill, names.execute_skill, callback_group=cb)
        self._log_pub = self.create_publisher(String, names.execution_log, 10)
        # Intrinsics and the camera pose, to turn a bbox size in pixels into
        # metres (deproject can't: it snaps every pixel of a flat top face to
        # the face's centroid, see local_plane_centroid in camera_services_node).
        self._camera_info = None
        self.create_subscription(CameraInfo, names.camera_info, self._on_camera_info,
                                 qos_profile_sensor_data, callback_group=cb)
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # One task at a time: the draft and its execution share a session.
        self._session = None
        self._session_id = None
        self._run = None
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._active_goal = None
        self._goal_lock = threading.Lock()

        self.get_logger().info(f"[AgentNode] Ready ({self.get_parameter('model_name').value})")

    # ── services ───────────────────────────────────────────────────────────

    def _on_plan(self, request, response):
        self._close_run(success=False, error='superseded by a new request before execution')
        params = self._params()
        run = DebugRun(mode='AGENT', command=request.user_command, config=params)
        frames = run.dir / 'frames'
        frames.mkdir(exist_ok=True)
        cancelled = self._cancellation.begin()
        try:
            session = self._new_session(request, params, run, frames)
            steps = session.draft_plan(cancelled)
            sid = uuid.uuid4().hex[:8]
            plan = {
                'task_summary': request.user_command,
                'reasoning_method': 'agent',
                'model': params['model_name'],
                'agent_session': sid,
                'plan': [
                    {'step': i + 1, 'action_name': s['action'], 'description': s['description']}
                    for i, s in enumerate(steps)
                ],
            }
            with self._run_lock:
                self._session, self._session_id, self._run = session, sid, run
            run.log(f'Draft plan: {json.dumps(plan["plan"])}')
            session.save_transcript(run.dir / 'transcript.json')
            response.success = True
            response.plan_json = json.dumps(plan)
        except PlanningCancelled as exc:
            response.success, response.error_message = False, str(exc)
            run.finish(success=False, error=str(exc))
        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[AgentNode] draft failed:\n{tb}')
            response.success, response.error_message = False, tb
            run.finish(success=False, error=tb)
        finally:
            self._cancellation.end(cancelled)
        return response

    def _on_execute(self, request, response):
        try:
            sid = json.loads(request.plan_json).get('agent_session')
        except (json.JSONDecodeError, AttributeError):
            sid = None
        with self._run_lock:
            session, run = self._session, self._run
            if session is None or sid != self._session_id:
                response.success = False
                response.error_message = 'no draft plan from this agent for that request; plan it again'
                return response
            self._session = self._session_id = self._run = None

        self._stop.clear()
        report = []
        try:
            outcome = session.execute(cancelled=self._stop)
            report = [f'{tool} {json.dumps(args)}: {"ok" if ok else "FAILED " + err}'
                      for tool, args, ok, err in outcome.actions]
            report.append(f'{"SUCCESS" if outcome.success else "NOT ACHIEVED"}: {outcome.summary}')
            report.append(f'{outcome.model_calls} model calls, {outcome.tool_calls} tool calls, '
                          f'{outcome.duration_s} s')
            response.success = outcome.success
            response.error_message = '' if outcome.success else f'agent: {outcome.summary}'
            response.report = '\n'.join(report)
            response.final_state_json = json.dumps({
                'success': outcome.success, 'summary': outcome.summary,
                'model_calls': outcome.model_calls, 'tool_calls': outcome.tool_calls,
                'duration_s': outcome.duration_s,
            })
            self._publish(f'[Agent] finished: {report[-2]}')
            self._finish_run(run, session, outcome.success, error=None if outcome.success else outcome.summary)
        except AgentCancelled as exc:
            response.success, response.error_message = False, 'Execution cancelled by user'
            self._finish_run(run, session, False, error=str(exc))
        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[AgentNode] execution failed:\n{tb}')
            response.success, response.error_message = False, tb.strip().splitlines()[-1]
            self._finish_run(run, session, False, error=tb)
        return response

    def _on_cancel(self, _request, response):
        self.get_logger().warn('[AgentNode] cancel requested')
        self._stop.set()
        with self._goal_lock:
            gh = self._active_goal
        if gh is not None:
            gh.cancel_goal_async()
        time.sleep(0.3)
        ok, err = self._run_skill_raw('move_home', {})
        response.success = ok
        response.message = 'Execution cancelled and robot returned home.' if ok else \
            f'Cancelled, but return-home failed: {err}'
        return response

    # ── session setup ──────────────────────────────────────────────────────

    def _params(self) -> dict:
        p = {name: self.get_parameter(name).value for name in (
            'model_name', 'temperature', 'reasoning_effort', 'max_model_calls',
            'max_duration_s', 'keep_images', 'coords', 'image_width', 'history_turns',
            'max_input_tokens', 'tpm_limit')}
        if p['tpm_limit'] < 0:
            p['tpm_limit'] = settings.AGENT_TPM_LIMITS.get(p['model_name'].split('/', 1)[0], 0)
        if p['coords'] == 'auto':
            p['coords'] = 'norm1000' if 'qwen' in p['model_name'].lower() else 'pixel'
        return p

    def _new_session(self, request, params, run, frames) -> AgentSession:
        scene = json.loads(request.scene_json) if request.scene_json else {}
        workspace = scene.get('workspace', {})
        table_z = float(workspace.get('table', {}).get('surface_z', 0.0))
        limits = {k: tuple(v) for k, v in workspace.get('limits', {}).items()}
        config = ToolboxConfig(
            table_z=table_z,
            limits=limits,
            grasp=GraspGeometry(
                pick_fraction=settings.PICK_GRASP_DEPTH_FRACTION,
                min_object_height=settings.MIN_OBJECT_HEIGHT_M,
                max_descent=max(settings.TCP_OFFSET_Z - settings.TCP_CLAMP_CLEARANCE_M, 0.0),
            ),
            edge_inset_frac=settings.GRASP_WIDTH_EDGE_INSET_FRAC,
            free_spot_margin_m=settings.ZONE_PLACEMENT_MARGIN_M,
            coords=params['coords'],
            send_width=params['image_width'],
        )

        def log(line):
            run.log(line)
            self._publish(line)

        client_params = {'model_name': params['model_name'], 'temperature': params['temperature'],
                         'max_tokens': 4096, 'tpm_limit': params['tpm_limit']}
        if params['reasoning_effort']:
            client_params['reasoning_effort'] = params['reasoning_effort']
        client = ToolChatClient(**client_params)
        toolbox = Toolbox(backend=_RosBackend(self, frames), config=config, log=log)
        return AgentSession(
            client, toolbox, request.user_command,
            max_model_calls=params['max_model_calls'], max_duration_s=params['max_duration_s'],
            keep_images=params['keep_images'], history_turns=params['history_turns'],
            max_input_tokens=params['max_input_tokens'],
            max_request_tokens=settings.AGENT_MAX_REQUEST_TOKENS.get(params['model_name'].split('/', 1)[0], 0),
            log=log,
        )

    def _finish_run(self, run, session, success, error=None):
        session.save_transcript(run.dir / 'transcript.json')
        usage = session.client.usage
        run.log(f'model usage: {usage.calls} calls, {usage.input_tokens} in / {usage.output_tokens} out tokens, '
                f'latencies {usage.per_call}')
        run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs))
        run.finish(success=success, response={'plan': session.toolbox.plan}, error=error)

    def _close_run(self, success, error):
        with self._run_lock:
            session, run = self._session, self._run
            self._session = self._session_id = self._run = None
        if run is not None:
            self._finish_run(run, session, success, error)

    def _on_camera_info(self, msg):
        self._camera_info = msg

    def _publish(self, line):
        self.get_logger().info(line)
        self._log_pub.publish(String(data=line))

    # ── ROS calls used by the backend ──────────────────────────────────────

    def call(self, client, request, what, timeout=10.0):
        if not client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError(f'{what} service not available')
        future = client.call_async(request)
        deadline = time.monotonic() + timeout
        while not future.done():
            if time.monotonic() > deadline:
                raise RuntimeError(f'{what} did not answer within {timeout:.0f} s')
            time.sleep(0.02)
        return future.result()

    def _run_skill_raw(self, name, args, timeout=SKILL_TIMEOUT_S):
        if not self._skills.wait_for_server(timeout_sec=5.0):
            return False, 'skill executor not available'
        goal = ExecuteSkill.Goal(skill_name=name, skill_args_json=json.dumps(args))
        done, holder = threading.Event(), {}

        def on_result(future):
            with self._goal_lock:
                self._active_goal = None
            try:
                res = future.result().result
                holder['r'] = (res.success, res.error_message)
            except Exception as exc:
                holder['r'] = (False, f'result retrieval failed: {exc}')
            done.set()

        def on_goal(future):
            try:
                gh = future.result()
            except Exception as exc:
                holder['r'] = (False, f'goal send failed: {exc}')
                done.set()
                return
            if not gh.accepted:
                holder['r'] = (False, 'goal rejected by the executor')
                done.set()
                return
            with self._goal_lock:
                self._active_goal = gh
            gh.get_result_async().add_done_callback(on_result)

        self._skills.send_goal_async(goal).add_done_callback(on_goal)
        if not done.wait(timeout):
            with self._goal_lock:
                gh, self._active_goal = self._active_goal, None
            if gh is not None:
                gh.cancel_goal_async()
            return False, f'timed out after {timeout:.0f} s'
        return holder['r']


class _RosBackend:
    """Camera, deprojection and executor for the Toolbox, over ROS."""

    def __init__(self, node: AgentNode, frames: Path):
        self.node = node
        self.frames = frames
        self.count = 0

    def capture(self):
        resp = self.node.call(self.node._get_image, GetImage.Request(), names.get_image)
        if not resp.success:
            raise RuntimeError(f'get_image failed: {resp.error_message}')
        img = self.node._bridge.imgmsg_to_cv2(resp.image, desired_encoding='bgr8')
        self.count += 1
        path = self.frames / f'{self.count:03d}.png'
        self.node._cv2.imwrite(str(path), img)
        return str(path), img.shape[1], img.shape[0]

    def deproject(self, pixels):
        """Deproject pixels; a pixel without depth gives None instead of
        failing the whole batch (a partial request: NaN for those pixels)."""
        if not pixels:
            return []
        req = Deproject.Request(u=[int(round(u)) for u, _ in pixels], v=[int(round(v)) for _, v in pixels],
                                partial=True)
        resp = self.node.call(self.node._deproject, req, names.deproject)
        if not resp.success:
            return [None] * len(pixels)
        if resp.frame_id != 'base_link':
            raise RuntimeError(f"deproject returned points in '{resp.frame_id}', expected base_link "
                               '(is the camera calibrated?)')
        return [None if math.isnan(p.z) else [p.x, p.y, p.z] for p in resp.points]

    def metres_per_pixel(self, point):
        """Size of one pixel, in metres, at a base frame point: (along u, along v).
        None when the intrinsics or the camera pose are not known yet."""
        info = self.node._camera_info
        if info is None or not info.header.frame_id or info.k[0] <= 0 or info.k[4] <= 0:
            return None
        tf = None
        # camera_optical_frame: the name camera_services_node falls back to when
        # it publishes a fixed camera pose before the first image (simulation).
        for frame in (info.header.frame_id, 'camera_optical_frame'):
            try:
                tf = self.node._tf_buffer.lookup_transform(frame, 'base_link', Time())
                break
            except Exception:
                continue
        if tf is None:
            return None
        q, t = tf.transform.rotation, tf.transform.translation
        # z of the point in the camera optical frame = distance along the optical axis
        x, y, z = point
        r20 = 2.0 * (q.x * q.z - q.w * q.y)
        r21 = 2.0 * (q.y * q.z + q.w * q.x)
        r22 = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
        depth = r20 * x + r21 * y + r22 * z + t.z
        if depth <= 0:
            return None
        return depth / info.k[0], depth / info.k[4]

    def run_skill(self, name, args):
        if self.node._stop.is_set():
            return False, 'cancelled by the operator'
        return self.node._run_skill_raw(name, args)


def main(args=None):
    rclpy.init(args=args)
    node = AgentNode()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
