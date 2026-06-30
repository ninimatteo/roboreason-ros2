import asyncio
import io
import json
import os
import threading
import time

from ament_index_python.packages import get_package_share_directory
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from control_msgs.action import FollowJointTrajectory
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from ur_msgs.srv import SetIO

from robo_reason_interfaces.srv import ExecutePlan, GetImage, PlanTask

# Topic the plan manager publishes per-step execution progress on.
EXECUTION_LOG_TOPIC = '/execution_log'

# The /execute_skill action server publishes this status topic exactly once per
# server, so its publisher count == number of skill-executor servers. >1 means a
# duplicate executor is on the graph (e.g. an orphan from a previous stack),
# which makes every skill run twice and corrupts shared robot state.
EXECUTE_SKILL_STATUS_TOPIC = '/execute_skill/_action/status'

# Planner node names per mode — targets for live parameter updates.
PLANNER_NODE_BY_MODE = {'LLM': 'llm_planner_node', 'VLM': 'vlm_planner_node'}

# use_mock_llm only exists on the LLM planner.
SET_PARAM_TIMEOUT_S = 5.0

# Endpoints that indicate the UR5cb / gripper are reachable.
TRAJ_ACTION = '/scaled_joint_trajectory_controller/follow_joint_trajectory'
GRIPPER_IO_SERVICE = '/io_and_status_controller/set_io'
JOINT_STATES_TOPIC = '/joint_states'

# Unified camera frame grab — exposed by both the mock and real camera nodes.
CAMERA_GET_IMAGE_SERVICE = '/camera/get_image'
CAMERA_TIMEOUT_S = 4.0

# A /joint_states message older than this (seconds) is considered stale.
JOINT_STATES_TIMEOUT_S = 2.0

# Service-call budgets (planning may involve a slow LLM/VLM round-trip).
PLAN_TIMEOUT_S = 180.0
EXECUTE_TIMEOUT_S = 300.0


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

        # --- planning / execution ---
        self._plan_client = self.create_client(PlanTask, '/plan_task')
        self._exec_client = self.create_client(ExecutePlan, '/execute_plan')
        self._command_lock = threading.Lock()
        self._scene_json = self._load_scene()

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
    def camera_available(self) -> bool:
        """True when a camera node is exposing /camera/get_image."""
        return self._camera_client.service_is_ready()

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
        return self._image_to_jpeg(resp.image)

    @staticmethod
    def _image_to_jpeg(img):
        """Encode a sensor_msgs/Image to JPEG with Pillow (no numpy/cv2 here)."""
        from PIL import Image as PILImage

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

    def plan_command(self, user_command: str) -> dict:
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
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        return result

    def execute_command(self, plan_json: str) -> dict:
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
        return result

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
        # is LLM-only; skip it for the VLM planner which never declares it.
        params = []
        if config.get('reasoning_method') is not None:
            params.append(('reasoning_method', str(config['reasoning_method'])))
        if config.get('model_name') is not None:
            params.append(('model_name', str(config['model_name'])))
        if config.get('temperature') is not None:
            params.append(('temperature', float(config['temperature'])))
        if mode == 'LLM' and config.get('use_mock_llm') is not None:
            params.append(('use_mock_llm', bool(config['use_mock_llm'])))

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
