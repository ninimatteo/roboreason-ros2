import asyncio
import csv
import io
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from control_msgs.action import FollowJointTrajectory
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from ur_msgs.srv import SetIO

from std_srvs.srv import Trigger

from robo_reason_bringup.config import settings
from robo_reason_interfaces.msg import PixelArray
from robo_reason_interfaces.srv import CancelExecution, ExecutePlan, GetImage, PlanTask

# Topic the plan manager publishes per-step execution progress on.
EXECUTION_LOG_TOPIC = settings.EXECUTION_LOG_TOPIC

# The /execute_skill action server publishes this status topic exactly once per
# server, so its publisher count == number of skill-executor servers. >1 means a
# duplicate executor is on the graph (e.g. an orphan from a previous stack),
# which makes every skill run twice and corrupts shared robot state.
EXECUTE_SKILL_STATUS_TOPIC = settings.EXECUTE_SKILL_STATUS_TOPIC

# Planner node names per mode — targets for live parameter updates.
PLANNER_NODE_BY_MODE = settings.PLANNER_NODE_BY_MODE

# use_mock_llm only exists on the LLM planner.
SET_PARAM_TIMEOUT_S = settings.SET_PARAM_TIMEOUT_S

# Endpoints that indicate the UR5cb / gripper are reachable.
TRAJ_ACTION = settings.TRAJ_ACTION
GRIPPER_IO_SERVICE = settings.GRIPPER_IO_SERVICE
JOINT_STATES_TOPIC = settings.JOINT_STATES_TOPIC

# Unified camera frame grab — exposed by both the mock and real camera nodes.
CAMERA_GET_IMAGE_SERVICE = settings.GET_IMAGE_SERVICE
CAMERA_TIMEOUT_S = settings.CAMERA_TIMEOUT_S

# Debug pixel overlay — published by camera_services_node on every Deproject call.
PIXEL_DEBUG_TOPIC = settings.PIXEL_DEBUG_TOPIC
# ChArUco axis keypoints — 4 projected pixel coords (origin, X, Y, Z) at ~2 Hz.
CHARUCO_AXIS_TOPIC = settings.CHARUCO_AXIS_TOPIC
# Service to force ChArUco re-calibration without restarting the camera node.
CAMERA_RECALIBRATE_SERVICE = settings.RECALIBRATE_SERVICE
# Whether the camera→base_link transform is currently locked, published at ~2 Hz.
CALIBRATION_STATUS_TOPIC = settings.CALIBRATION_STATUS_TOPIC

# A /joint_states message older than this (seconds) is considered stale.
JOINT_STATES_TIMEOUT_S = settings.JOINT_STATES_TIMEOUT_S

# Service-call budgets (planning may involve a slow LLM/VLM round-trip).
PLAN_TIMEOUT_S = settings.PLAN_TIMEOUT_S
EXECUTE_TIMEOUT_S = settings.EXECUTE_TIMEOUT_S
CANCEL_TIMEOUT_S = settings.CANCEL_TIMEOUT_S

# task_id -> (label, sub_tasks_required) for the GUI's inline benchmark
# annotation form (see record_benchmark_annotation). Must stay in sync with
# the standalone benchmark/benchmark_annotate.py's copy of this same table
# and with benchmark/PLAN.md §1 — there's no shared import between this
# ROS2 package and that plain script, so it's duplicated deliberately
# rather than reached for across a fragile relative-path import.
BENCHMARK_TASKS = {
    'pp_easy':    ('Pick&Place easy',   1),
    'pp_hard':    ('Pick&Place hard',   4),
    'sort_easy':  ('Sort/Stack easy',   1),
    'sort_hard':  ('Sort/Stack hard',   4),
    'arith_easy': ('Arithmetic easy',   1),
    'arith_hard': ('Arithmetic hard',   4),
}
BENCHMARK_RESULTS_FIELDS = [
    'timestamp', 'run_id', 'task_id', 'difficulty', 'model_label',
    'reasoning_method', 'model_name', 'repetition', 'command',
    'num_planned_steps', 'steps_executed', 'safety_ok', 'TS',
    'sub_tasks_completed', 'sub_tasks_required', 'TSR', 'AETS', 'notes',
]


class GuiBridgeNode(Node):
    """ROS2 bridge between the web GUI and the RoboReason stack.

    - Phase 1: read-only robot-connectivity probes (cached on a timer).
    - Phase 2: run a command through /plan_task then /execute_plan.
    - Phase 3: stream /execution_log lines to the GUI over a WebSocket so the
      plan card updates step-by-step while the robot operates.

    Service calls originate from the FastAPI worker thread; the node is spun by
    a MultiThreadedExecutor on a separate thread, so call_async futures resolve
    there and we wait on them with a threading.Event.
    """

    def __init__(self):
        super().__init__('gui_bridge_node')

        # --- connectivity probes ---
        self._traj_client = ActionClient(self, FollowJointTrajectory, TRAJ_ACTION)
        self._io_client = self.create_client(SetIO, GRIPPER_IO_SERVICE)
        self.create_subscription(
            JointState, JOINT_STATES_TOPIC, self._on_joint_states,
            qos_profile_sensor_data,
        )
        self._last_joint_states = 0.0  # time.monotonic() of last message
        self._probes = {
            'trajectory_server': False,
            'joint_states': False,
            'gripper_io': False,
        }
        self.create_timer(1.0, self._refresh_probes)

        # --- pendant / reverse-interface connection (wired in by server_node) ---
        # The connectivity probes only tell us the controllers are up; they go
        # green before the teach pendant accepts control on the reverse
        # interface. These callables (the UR driver supervisor) let the LED stay
        # amber until the pendant is actually connected on a GUI-owned driver.
        self._pendant_connected = None
        self._driver_active = None

        # --- camera frame grab (request the latest RGB frame on demand) ---
        self._camera_client = self.create_client(GetImage, CAMERA_GET_IMAGE_SERVICE)

        # --- debug pixel overlay (VLM target positions from Deproject calls) ---
        self._latest_debug_pixels: list = []
        self._debug_pixels_lock = threading.Lock()
        self.create_subscription(PixelArray, PIXEL_DEBUG_TOPIC, self._on_debug_pixels, 10)

        # --- ChArUco axis overlay (board coordinate frame, ~2 Hz) ---
        self._latest_charuco_axis: list = []
        self._charuco_axis_lock = threading.Lock()
        self.create_subscription(PixelArray, CHARUCO_AXIS_TOPIC, self._on_charuco_axis, 10)

        # --- ChArUco calibration-lock status (drives the GUI LED, ~2 Hz) ---
        self._calibrated = False
        self._calibration_seen = False
        self._calibration_lock = threading.Lock()
        self.create_subscription(
            Bool, CALIBRATION_STATUS_TOPIC, self._on_calibration_status, 10
        )

        # --- ChArUco recalibration service client ---
        self._recalibrate_client = self.create_client(Trigger, CAMERA_RECALIBRATE_SERVICE)

        # --- planning / execution ---
        self._plan_client = self.create_client(PlanTask, '/plan_task')
        self._exec_client = self.create_client(ExecutePlan, '/execute_plan')
        self._cancel_client = self.create_client(CancelExecution, '/cancel_execution')
        self._command_lock = threading.Lock()
        self._scene_json = self._load_scene()
        # Set by plan_command() to the DebugRun folder its /plan_task call
        # just wrote, so execute_command() can attach the execution outcome
        # to the same run for benchmark logging (see _record_execution_outcome).
        self._last_plan_run_id = None
        self._last_plan_is_benchmark = False

        # --- live execution log -> WebSocket fan-out ---
        # The subscription callback runs on the executor thread; WebSocket queues
        # live on the asyncio loop, so we hand lines over with call_soon_threadsafe.
        self.create_subscription(
            String, EXECUTION_LOG_TOPIC, self._on_execution_log, 10,
        )
        self._loop = None  # asyncio loop, set once the server is up
        self._log_subscribers = set()  # set[asyncio.Queue]

        # --- live config (SetParameters on the planner) ---
        # One SetParameters client per target node, created lazily and cached.
        self._param_clients = {}

        # --- terminal-log snapshots (camera/robot/stack subprocess output) ---
        # The GUI-owned supervisors each keep an in-memory ring buffer of their
        # subprocess's stdout/stderr; this service lets a planner node pull a
        # snapshot into its own per-run debug folder (see debug_recorder.py).
        # Populated via set_log_sources() once server_node.py constructs the
        # supervisors, so it's None (-> empty logs) until then.
        self._log_sources = {'stack': None, 'camera': None, 'robot': None}
        self.create_service(
            Trigger, '/gui/get_terminal_logs', self._handle_get_terminal_logs
        )

        self.get_logger().info('[GuiBridgeNode] started')

    # ------------------------------------------------------------ log fan-out
    def set_event_loop(self, loop):
        """Register the asyncio loop the FastAPI server runs on."""
        self._loop = loop

    def add_log_subscriber(self) -> 'asyncio.Queue':
        """Register a queue that receives /execution_log lines (call from loop)."""
        queue = asyncio.Queue()
        self._log_subscribers.add(queue)
        return queue

    def remove_log_subscriber(self, queue) -> None:
        self._log_subscribers.discard(queue)

    def _on_execution_log(self, msg):
        if self._loop is None:
            return
        for queue in list(self._log_subscribers):
            self._loop.call_soon_threadsafe(queue.put_nowait, msg.data)

    # ------------------------------------------------------------------ scene
    def _load_scene(self):
        try:
            pkg_share = get_package_share_directory('robo_reason_task_interface')
            scene_path = os.path.join(pkg_share, 'config', 'scene_mock.json')
            with open(scene_path, 'r') as f:
                self.get_logger().info(f'[GuiBridgeNode] scene loaded from {scene_path}')
                return f.read()
        except Exception as exc:
            self.get_logger().warn(f'[GuiBridgeNode] could not load scene: {exc}')
            return None

    # ------------------------------------------------------------------ probes
    def _on_joint_states(self, _msg):
        self._last_joint_states = time.monotonic()

    def _refresh_probes(self):
        self._probes['trajectory_server'] = self._traj_client.server_is_ready()
        self._probes['gripper_io'] = self._io_client.service_is_ready()
        self._probes['joint_states'] = (
            (time.monotonic() - self._last_joint_states) < JOINT_STATES_TIMEOUT_S
        )

    def set_connection_sources(self, pendant_connected, driver_active):
        """Wire in the UR driver supervisor's pendant/connection callables.

        ``pendant_connected()`` is True once the driver has seen the
        reverse-interface marker; ``driver_active()`` is True while the GUI is
        supervising a real driver. Both are used only to gate the LED so it
        reflects the pendant, not just the controllers coming up (request #3).
        """
        self._pendant_connected = pendant_connected
        self._driver_active = driver_active

    def set_log_sources(self, stack=None, camera=None, driver=None) -> None:
        """Register the GUI-owned supervisors so /gui/get_terminal_logs can
        snapshot their live subprocess-log ring buffers on request."""
        self._log_sources = {'stack': stack, 'camera': camera, 'robot': driver}

    def _handle_get_terminal_logs(self, _request, response):
        """Snapshot camera/robot/stack subprocess logs for a planner's
        per-run debug folder (see debug_recorder.DebugRun.save_terminal_logs)."""
        logs = {}
        for key, supervisor in self._log_sources.items():
            if supervisor is None:
                logs[key] = []
                continue
            try:
                logs[key] = list(supervisor.status().get('logs', []))
            except Exception as exc:
                logs[key] = [f'[gui] failed to read {key} logs: {exc}']
        response.success = True
        response.message = json.dumps(logs)
        return response

    def _pendant_state(self) -> dict:
        """Whether a real driver is supervised and its pendant is connected."""
        supervised = bool(self._driver_active and self._driver_active())
        connected = bool(self._pendant_connected and self._pendant_connected())
        return {'supervised': supervised, 'connected': connected}

    def _robot_status(self) -> dict:
        probes = dict(self._probes)
        pendant = self._pendant_state()
        healthy = sum(1 for ok in probes.values() if ok)
        if healthy == len(probes):
            # Controllers are all up. On a GUI-owned real driver, stay amber
            # until the teach pendant connects on the reverse interface —
            # otherwise the robot won't actually move despite a green LED.
            if pendant['supervised'] and not pendant['connected']:
                level = 'amber'
            else:
                level = 'green'
        elif healthy > 0:
            level = 'amber'
        else:
            level = 'red'
        return {'level': level, 'probes': probes, 'pendant': pendant}

    # ------------------------------------------------------------------ camera
    def _on_debug_pixels(self, msg: PixelArray) -> None:
        if len(msg.u) != len(msg.v):
            return
        with self._debug_pixels_lock:
            self._latest_debug_pixels = list(zip(msg.u, msg.v))

    def _on_charuco_axis(self, msg: PixelArray) -> None:
        if len(msg.u) != len(msg.v):
            return
        with self._charuco_axis_lock:
            # 4 points = [origin, X-end, Y-end, Z-end]; 0 = board not visible.
            self._latest_charuco_axis = list(zip(msg.u, msg.v))

    def _on_calibration_status(self, msg: Bool) -> None:
        with self._calibration_lock:
            self._calibrated = bool(msg.data)
            self._calibration_seen = True

    def calibration_status(self) -> dict:
        """Snapshot of ChArUco calibration lock state for the GUI LED.

        ``seen`` is False until the first status message arrives (e.g. the
        camera node isn't running yet) so the GUI can distinguish "not
        calibrated" from "unknown".
        """
        with self._calibration_lock:
            return {'calibrated': self._calibrated, 'seen': self._calibration_seen}

    def camera_available(self) -> bool:
        """True when a camera node is exposing /camera/get_image."""
        return self._camera_client.service_is_ready()

    def recalibrate_camera(self) -> dict:
        """Call /camera/recalibrate to force a fresh ChArUco calibration."""
        try:
            resp = self._call(self._recalibrate_client, Trigger.Request(), 5.0)
            return {'ok': resp.success, 'message': resp.message}
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    def grab_camera_jpeg(self):
        """Grab the latest frame via /camera/get_image; return JPEG bytes or None."""
        if not self._camera_client.service_is_ready():
            return None
        try:
            resp = self._call(self._camera_client, GetImage.Request(), CAMERA_TIMEOUT_S)
        except Exception as exc:
            self.get_logger().warn(f'[GuiBridgeNode] camera grab failed: {exc}')
            return None
        if resp is None or not resp.success:
            return None
        with self._debug_pixels_lock:
            pixels = list(self._latest_debug_pixels)
        with self._charuco_axis_lock:
            charuco_axis = list(self._latest_charuco_axis)
        return self._image_to_jpeg(resp.image, pixels, charuco_axis)

    @staticmethod
    def _image_to_jpeg(img, pixels=None, charuco_axis=None):
        """Encode a sensor_msgs/Image to JPEG with Pillow (no numpy/cv2 here).

        pixels       — list of (u, v) tuples: VLM target markers drawn as cyan
                       bounding box + red crosshair + yellow index label.
        charuco_axis — list of exactly 4 (u, v) tuples: [origin, X-end, Y-end,
                       Z-end], drawn as white origin circle + R/G/B axis lines.
                       Empty list means board not visible; skipped silently.
        """
        from PIL import Image as PILImage, ImageDraw

        enc = (img.encoding or '').lower()
        size = (img.width, img.height)
        data = bytes(img.data)
        try:
            if enc in ('rgb8', 'bgr8'):
                pil = PILImage.frombytes('RGB', size, data)
                if enc == 'bgr8':
                    b, g, r = pil.split()
                    pil = PILImage.merge('RGB', (r, g, b))
            elif enc in ('rgba8', 'bgra8'):
                pil = PILImage.frombytes('RGBA', size, data)
                if enc == 'bgra8':
                    b, g, r, a = pil.split()
                    pil = PILImage.merge('RGBA', (r, g, b, a))
                pil = pil.convert('RGB')
            elif enc in ('mono8', '8uc1'):
                pil = PILImage.frombytes('L', size, data)
            else:
                return None
        except (ValueError, OSError):
            return None

        if pixels or (charuco_axis and len(charuco_axis) == 4):
            pil = pil.convert('RGB')
            draw = ImageDraw.Draw(pil)
            w, h = pil.size

            # --- ChArUco coordinate axes ---
            if charuco_axis and len(charuco_axis) == 4:
                o  = (int(charuco_axis[0][0]), int(charuco_axis[0][1]))
                xp = (int(charuco_axis[1][0]), int(charuco_axis[1][1]))
                yp = (int(charuco_axis[2][0]), int(charuco_axis[2][1]))
                zp = (int(charuco_axis[3][0]), int(charuco_axis[3][1]))
                draw.line([o, xp], fill='red',   width=3)
                draw.line([o, yp], fill='lime',  width=3)
                draw.line([o, zp], fill='blue',  width=3)
                draw.text(xp, 'X', fill='red')
                draw.text(yp, 'Y', fill='lime')
                draw.text(zp, 'Z', fill='blue')
                # White filled origin circle with black border
                r = 6
                draw.ellipse(
                    [(o[0] - r, o[1] - r), (o[0] + r, o[1] + r)],
                    fill='white', outline='black', width=2,
                )

            # --- VLM target pixel markers ---
            if pixels:
                marker = 14
                radius = marker // 2
                for index, (u, v) in enumerate(pixels, start=1):
                    u, v = int(u), int(v)
                    if u < 0 or v < 0 or u >= w or v >= h:
                        continue
                    x0 = max(0, u - radius)
                    y0 = max(0, v - radius)
                    x1 = min(w - 1, u + radius)
                    y1 = min(h - 1, v + radius)
                    draw.rectangle([(x0, y0), (x1, y1)], outline='cyan', width=2)
                    draw.line([(u - radius, v), (u + radius, v)], fill='red', width=2)
                    draw.line([(u, v - radius), (u, v + radius)], fill='red', width=2)
                    draw.text(
                        (min(w - 10, x1 + 4), max(0, y0 - 14)),
                        str(index), fill='yellow',
                    )

        buf = io.BytesIO()
        pil.save(buf, format='JPEG', quality=80)
        return buf.getvalue()

    def robot_ready(self) -> bool:
        """True once the UR driver has brought up motion + joint feedback.

        Used by the UR driver supervisor as its readiness gate: the trajectory
        action server and a fresh /joint_states stream mean the controllers are
        up. Gripper I/O is intentionally excluded — it can lag and isn't needed
        to call the driver "connected".
        """
        return self._probes['trajectory_server'] and self._probes['joint_states']

    # --------------------------------------------------------------- preflight
    def execute_skill_server_count(self) -> int:
        """Number of /execute_skill action servers currently on the graph."""
        return self.count_publishers(EXECUTE_SKILL_STATUS_TOPIC)

    def preflight(self) -> dict:
        """Check the executor graph before running a plan.

        Fails fast on the duplicate-executor condition that silently doubles
        every skill (see EXECUTE_SKILL_STATUS_TOPIC). A count of 0 just means
        the stack isn't started yet; that's surfaced but not treated as the
        duplicate fault.
        """
        servers = self.execute_skill_server_count()
        if servers > 1:
            message = (
                f'{servers} /execute_skill action servers detected — a duplicate '
                'skill executor is running. Stop the stack (it now reaps orphans) '
                'and start it again before executing.'
            )
            return {'ok': False, 'duplicate': True, 'servers': servers, 'message': message}
        if servers == 0:
            return {
                'ok': False, 'duplicate': False, 'servers': 0,
                'message': 'no /execute_skill action server — start the stack first',
            }
        return {'ok': True, 'duplicate': False, 'servers': 1, 'message': 'ok'}

    def health(self) -> dict:
        """Snapshot of ROS + robot connectivity for the GUI's /api/health."""
        names = self.get_node_names()
        return {
            'ros_ok': True,
            'bridge_node': self.get_name(),
            'node_count': len(names),
            'discovered_nodes': sorted(names),
            'robot': self._robot_status(),
            'calibration': self.calibration_status(),
        }

    # -------------------------------------------------------------- service IO
    def _call(self, client, request, timeout):
        """Call a service from a non-executor thread and wait for the result."""
        if not client.service_is_ready() and not client.wait_for_service(timeout_sec=2.0):
            raise RuntimeError(f'service {client.srv_name} is unavailable')
        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        if not done.wait(timeout):
            raise RuntimeError(f'service {client.srv_name} timed out after {timeout}s')
        return future.result()

    def plan_command(self, user_command: str, is_benchmark: bool = False) -> dict:
        """Plan a command via /plan_task. Returns the plan without executing it."""
        result = {
            'command': user_command,
            'planned': False,
            'plan': None,
            'plan_json': None,
            'error': None,
        }

        if self._scene_json is None:
            result['error'] = 'scene_mock.json not found (is robo_reason_task_interface built?)'
            return result

        try:
            plan_req = PlanTask.Request()
            plan_req.user_command = user_command
            plan_req.scene_json = self._scene_json
            plan_resp = self._call(self._plan_client, plan_req, PLAN_TIMEOUT_S)
            if not plan_resp.success:
                result['error'] = plan_resp.error_message or 'planning failed'
                return result
            result['planned'] = True
            result['plan_json'] = plan_resp.plan_json
            result['plan'] = json.loads(plan_resp.plan_json)
            result['run_id'] = self._latest_debug_run_id()
            self._last_plan_run_id = result['run_id']
            self._last_plan_is_benchmark = is_benchmark
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        return result

    def _latest_debug_run_id(self):
        """Best-effort: the run_id of the DebugRun folder /plan_task just
        wrote (see debug_recorder.py) — there's no run_id in PlanTask.srv's
        response, so this identifies it by "most recently created folder
        under DEBUG_DIR" instead, which is safe since plan_command() and
        execute_command() are always called back-to-back for one user
        action, never interleaved with another planning call.
        """
        try:
            root = Path(settings.DEBUG_DIR)
            runs = [p for p in root.iterdir() if p.is_dir()]
            if not runs:
                return None
            return max(runs, key=lambda p: p.stat().st_mtime).name
        except OSError:
            return None

    def execute_command(self, plan_json: str, is_benchmark: bool = False) -> dict:
        """Execute a previously-planned plan via /execute_plan.

        While this runs the plan manager publishes /execution_log, which is
        streamed to the GUI over the WebSocket for live per-step feedback.
        """
        result = {
            'executed': False,
            'report': None,
            'final_state': None,
            'error': None,
        }

        if self._scene_json is None:
            result['error'] = 'scene_mock.json not found (is robo_reason_task_interface built?)'
            return result

        # Refuse to execute when a duplicate executor is present — otherwise the
        # plan runs twice and two nodes fight over the real robot (corrupt IK).
        pre = self.preflight()
        if pre['duplicate']:
            result['error'] = pre['message']
            return result

        # Only one plan at a time — the robot can't run two plans at once.
        if not self._command_lock.acquire(blocking=False):
            result['error'] = 'a command is already running'
            return result
        try:
            exec_req = ExecutePlan.Request()
            exec_req.plan_json = plan_json
            exec_req.scene_json = self._scene_json
            exec_resp = self._call(self._exec_client, exec_req, EXECUTE_TIMEOUT_S)
            if not exec_resp.success:
                result['error'] = exec_resp.error_message or 'execution failed'
                return result
            result['executed'] = True
            result['report'] = exec_resp.report
            result['final_state'] = json.loads(exec_resp.final_state_json)
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            self._command_lock.release()
        # is_benchmark is read from this execute call (not the earlier plan
        # call) since it's the authoritative flag for whether this outcome
        # should count as benchmark data — the frontend always sends the
        # same checkbox value on both calls for one command submission
        # (see sendCommand() in app.js), so in practice they always agree.
        self._record_execution_outcome(result, is_benchmark)
        return result

    def _record_execution_outcome(self, result: dict, is_benchmark: bool) -> None:
        """Persist execute_command()'s outcome next to the DebugRun folder
        planning wrote for this command (see _latest_debug_run_id), and
        append one row to DEBUG_DIR/benchmark_summary.csv.

        Written for every execution, tagged with is_benchmark, so ad-hoc
        testing still gets a debug record but scripts/benchmark_annotate.py
        can tell it apart from an actual benchmark trial (the "Benchmark
        trial" checkbox in the GUI) and refuse to log it into
        docs/benchmark_results.csv by accident.

        This only captures what the program can know on its own — how many
        steps actually ran and whether the service call itself failed.
        Whether the run was actually *safe* (no real-world collision) or
        *correct* (objects ended up where intended) still needs a human
        observer; see scripts/benchmark_annotate.py, which reads this same
        run_id and asks for exactly those two judgments before computing
        TS/TSR/AETS. Never raises — this is best-effort logging and must
        not be able to fail a real execute_command() call.
        """
        run_id = self._last_plan_run_id
        if not run_id:
            return
        try:
            num_steps_executed = len(result['report'].splitlines()) if result.get('report') else 0
            outcome = {
                'run_id': run_id,
                'executed': result['executed'],
                'num_steps_executed': num_steps_executed,
                'error': result.get('error'),
                'is_benchmark': is_benchmark,
            }
            run_dir = Path(settings.DEBUG_DIR) / run_id
            (run_dir / 'execution_result.json').write_text(json.dumps(outcome, indent=2))

            csv_path = Path(settings.DEBUG_DIR) / 'benchmark_summary.csv'
            fields = ['run_id', 'executed', 'num_steps_executed', 'error', 'is_benchmark']
            is_new = not csv_path.exists()
            with open(csv_path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                if is_new:
                    writer.writeheader()
                writer.writerow(outcome)
        except Exception as exc:
            self.get_logger().warn(f'[GuiBridgeNode] failed to record execution outcome: {exc}')

    def get_benchmark_tasks(self) -> dict:
        """task_id -> {label, sub_tasks_required}, for the GUI's benchmark
        annotation form dropdown — see BENCHMARK_TASKS / benchmark/PLAN.md §1.
        """
        return {
            task_id: {'label': label, 'sub_tasks_required': required}
            for task_id, (label, required) in BENCHMARK_TASKS.items()
        }

    def _benchmark_results_csv(self) -> Path:
        # This file lives at <repo>/src/robo_reason_gui/robo_reason_gui/
        # bridge_node.py; colcon's --symlink-install (the standard build for
        # this project, see CLAUDE.md) keeps it at that real source path
        # rather than copying it into install/, so this resolves correctly
        # even when imported via the installed package.
        repo_root = Path(__file__).resolve().parents[3]
        return repo_root / 'benchmark' / 'results.csv'

    def _next_benchmark_repetition(self, results_csv: Path, task_id: str, model_label: str) -> int:
        if not results_csv.exists():
            return 1
        with open(results_csv, newline='') as f:
            rows = list(csv.DictReader(f))
        return sum(1 for r in rows if r['task_id'] == task_id and r['model_label'] == model_label) + 1

    def record_benchmark_annotation(self, run_id: str, task_id: str, safety_ok: bool,
                                     sub_tasks_completed: int, notes: str = '') -> dict:
        """Compute TS/TSR/AETS (Favali et al., RO-MAN 2025, Eq. 14-16) for
        `run_id` and append one row to benchmark/results.csv — this is what
        the GUI's inline "Benchmark trial" annotation form calls; the
        standalone benchmark/benchmark_annotate.py script does the same
        thing from a terminal. Keep both in sync if either changes.
        """
        if task_id not in BENCHMARK_TASKS:
            return {'ok': False, 'error': f'Unknown task_id: {task_id}'}

        label, sub_tasks_required = BENCHMARK_TASKS[task_id]
        if not (0 <= sub_tasks_completed <= sub_tasks_required):
            return {'ok': False, 'error': f'sub_tasks_completed must be 0-{sub_tasks_required}'}

        run_dir = Path(settings.DEBUG_DIR) / run_id
        if not run_dir.is_dir():
            return {'ok': False, 'error': f'No such run: {run_id}'}

        def read_json(name, default=None):
            path = run_dir / name
            if not path.exists():
                return default
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                return default

        config = read_json('config.json', {}) or {}
        response = read_json('response.json', {}) or {}
        execution = read_json('execution_result.json', {}) or {}
        command_path = run_dir / 'command.txt'
        command = command_path.read_text().strip() if command_path.exists() else ''

        # config.json has no explicit "mode" field — VLM/VLM_LLM runs are
        # the ones with a grounding_mode key (see vlm_planner_node.py's
        # DebugRun config dict), LLM runs aren't.
        model_label = 'VLM' if 'grounding_mode' in config else 'LLM'
        num_planned_steps = len(response.get('plan', [])) if isinstance(response, dict) else None
        steps_executed = execution.get('num_steps_executed') or 0

        ts = 1 if safety_ok else 0
        tsr = sub_tasks_completed / sub_tasks_required if sub_tasks_required else 0.0
        aets = (
            sub_tasks_completed / (sub_tasks_required * steps_executed)
            if sub_tasks_required and steps_executed else 0.0
        )

        results_csv = self._benchmark_results_csv()
        repetition = self._next_benchmark_repetition(results_csv, task_id, model_label)
        row = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'run_id': run_id,
            'task_id': task_id,
            'difficulty': 'hard' if task_id.endswith('_hard') else 'easy',
            'model_label': model_label,
            'reasoning_method': config.get('reasoning_method', ''),
            'model_name': config.get('model_name', ''),
            'repetition': repetition,
            'command': command,
            'num_planned_steps': num_planned_steps,
            'steps_executed': steps_executed,
            'safety_ok': safety_ok,
            'TS': ts,
            'sub_tasks_completed': sub_tasks_completed,
            'sub_tasks_required': sub_tasks_required,
            'TSR': round(tsr, 4),
            'AETS': round(aets, 4),
            'notes': notes,
        }

        try:
            results_csv.parent.mkdir(parents=True, exist_ok=True)
            is_new = not results_csv.exists()
            with open(results_csv, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=BENCHMARK_RESULTS_FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(row)
        except OSError as exc:
            return {'ok': False, 'error': f'Failed to write {results_csv}: {exc}'}

        return {'ok': True, 'repetition': repetition, 'TS': ts, 'TSR': row['TSR'], 'AETS': row['AETS']}

    def cancel_execution(self) -> dict:
        """Emergency-stop: cancel the in-flight skill, abort the rest of the
        plan, and command the robot home via /cancel_execution.

        Not gated behind _command_lock — this must be callable while
        execute_command() is blocked waiting on /execute_plan.
        """
        try:
            resp = self._call(self._cancel_client, CancelExecution.Request(), CANCEL_TIMEOUT_S)
            return {'ok': resp.success, 'message': resp.message}
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    # ------------------------------------------------------------ live config
    @staticmethod
    def _to_param_value(value) -> ParameterValue:
        """Wrap a python scalar in a rcl_interfaces ParameterValue."""
        if isinstance(value, bool):
            return ParameterValue(type=ParameterType.PARAMETER_BOOL, bool_value=value)
        if isinstance(value, float):
            return ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=value)
        if isinstance(value, int):
            return ParameterValue(type=ParameterType.PARAMETER_INTEGER, integer_value=value)
        return ParameterValue(type=ParameterType.PARAMETER_STRING, string_value=str(value))

    def _param_client(self, node_name: str):
        client = self._param_clients.get(node_name)
        if client is None:
            client = self.create_client(SetParameters, f'/{node_name}/set_parameters')
            self._param_clients[node_name] = client
        return client

    def set_planner_config(self, config: dict) -> dict:
        """Push reasoning/model/temperature (+mock) onto the live planner node.

        Targets the planner for config['mode'] and updates only the parameters
        present in the request, so the running planner retunes on its next plan
        without a relaunch (see B2). Returns per-parameter success.
        """
        mode = (config.get('mode') or 'LLM').upper()
        target = PLANNER_NODE_BY_MODE.get(mode)
        result = {'applied': False, 'target': target, 'mode': mode, 'results': {}, 'error': None}
        if target is None:
            result['error'] = f'unknown mode {mode!r}'
            return result
        if target not in self.get_node_names():
            result['error'] = f'{target} is not running (start the stack first)'
            return result

        # Build the parameter list from the fields the GUI sent. use_mock_llm
        # is LLM-only; skip it for the VLM/VLM_LLM planners which never declare
        # it. vlm_model_name/vlm_temperature only exist on the VLM_LLM planner
        # (independent scene-grounding model, see vlm_llm_planner_node).
        # grounding_mode ('point'/'bbox') only exists on the VLM planner —
        # vlm_planner_node's direct pixel-click/bbox pipeline.
        # reasoning_effort exists on both the VLM planner and the VLM_LLM
        # scene-grounding call — see VLM_REASONING_EFFORT in config.py.
        params = []
        if config.get('reasoning_method') is not None:
            params.append(('reasoning_method', str(config['reasoning_method'])))
        if config.get('model_name') is not None:
            params.append(('model_name', str(config['model_name'])))
        if config.get('temperature') is not None:
            params.append(('temperature', float(config['temperature'])))
        if mode == 'LLM' and config.get('use_mock_llm') is not None:
            params.append(('use_mock_llm', bool(config['use_mock_llm'])))
        if mode == 'VLM_LLM':
            if config.get('vlm_model_name') is not None:
                params.append(('vlm_model_name', str(config['vlm_model_name'])))
            if config.get('vlm_temperature') is not None:
                params.append(('vlm_temperature', float(config['vlm_temperature'])))
        if mode == 'VLM' and config.get('grounding_mode') is not None:
            params.append(('grounding_mode', str(config['grounding_mode'])))
        if mode in ('VLM', 'VLM_LLM') and config.get('reasoning_effort') is not None:
            params.append(('reasoning_effort', str(config['reasoning_effort'])))

        if not params:
            result['error'] = 'no parameters to set'
            return result

        req = SetParameters.Request()
        req.parameters = [
            Parameter(name=name, value=self._to_param_value(value))
            for name, value in params
        ]
        try:
            resp = self._call(self._param_client(target), req, SET_PARAM_TIMEOUT_S)
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
            return result

        all_ok = True
        for (name, _value), outcome in zip(params, resp.results):
            result['results'][name] = {
                'successful': outcome.successful,
                'reason': outcome.reason,
            }
            all_ok = all_ok and outcome.successful
        result['applied'] = all_ok
        return result
