"""Subprocess supervisor that lets the GUI own the ROS2 stack (B1).

The GUI launches the whole RoboReason stack as a child ``ros2 launch`` process
so the operator can start/stop/restart it from the browser. A single composed
launch file (gui_stack.launch.py) takes three independent mock axes — mock_llm
(reasoning), mock_robot (arm) and mock_camera (perception) — so real and
simulated pieces can be mixed. Switching mode or any mock axis requires a
relaunch (``restart``); model/reasoning/temperature retune live via parameters
(see bridge_node).

Real-robot UR driver supervision (auto-retry, log parsing) is Phase 5.
"""

import os
import signal
import subprocess
import threading
import time
from collections import deque

from robo_reason_bringup.config import settings

# Node executables (and the launch file itself) that gui_stack.launch.py spawns.
# The GUI is the sole owner of the stack (B1), so before launching we sweep for
# any of these left over from a previous gui_node that died without stop() —
# an orphaned executor would add a second /execute_skill action server and make
# every skill run twice (duplicate-node race → corrupt robot state).
_STACK_PROCESS_PATTERNS = (
    'gui_stack.launch.py',
    'llm_planner_node',
    'vlm_planner_node',
    'vlm_llm_planner_node',
    'plan_manager_node',
    'fake_skill_executor_node',
    'ur5_skill_executor_node',
    'mock_camera_service_node',
)


def _launch_command(mode: str, mock_robot: bool, mock_camera: bool, config: dict) -> list:
    """Build the ``ros2 launch gui_stack.launch.py ...`` argv for the selection."""
    mode = (mode or 'LLM').upper()
    use_mock_llm = 'true' if config.get('use_mock_llm', True) else 'false'
    return [
        'ros2', 'launch', 'robo_reason_bringup', 'gui_stack.launch.py',
        f'mode:={mode}',
        f'use_mock_llm:={use_mock_llm}',
        f"mock_robot:={'true' if mock_robot else 'false'}",
        f"mock_camera:={'true' if mock_camera else 'false'}",
        f"reasoning_method:={config.get('reasoning_method', settings.REASONING_METHOD)}",
        f"model_name:={config.get('model_name', settings.MODEL_NAME)}",
        f"temperature:={config.get('temperature', settings.TEMPERATURE)}",
        # Only used by the VLM_LLM planner's scene-grounding call; harmless
        # (declared-but-unused launch arg) in LLM/VLM mode.
        f"vlm_model_name:={config.get('vlm_model_name', settings.VLM_MODEL_NAME)}",
        f"vlm_temperature:={config.get('vlm_temperature', settings.VLM_TEMPERATURE)}",
    ]


class StackSupervisor:
    """Start/stop/restart the ROS2 stack as a child process group."""

    LOG_CAPACITY = settings.STACK_LOG_CAPACITY
    STOP_GRACE_S = settings.STACK_STOP_GRACE_S
    GRAPH_CLEAR_TIMEOUT_S = settings.STACK_GRAPH_CLEAR_TIMEOUT_S   # wait for orphan executors to leave the graph
    GRAPH_POLL_S = settings.STACK_GRAPH_POLL_S
    FORCE_KILL_GRACE_S = settings.PROCESS_FORCE_KILL_GRACE_S

    def __init__(self, logger=None, executor_count=None):
        self._logger = logger
        # Callable returning the live /execute_skill action-server count (the
        # bridge's execute_skill_server_count). Used as a hard graph guard so we
        # never launch on top of an executor that's still on the graph — two
        # servers make every skill run twice and corrupt robot state (request #1).
        self._executor_count = executor_count
        self._lock = threading.Lock()
        self._proc = None
        self._pgid = None  # process-group id of the running launch
        self._reader = None
        self._logs = deque(maxlen=self.LOG_CAPACITY)
        self._target = None  # {'mode', 'mock', 'command'}

    # ------------------------------------------------------------------ logging
    def _log(self, line: str):
        self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': line})
        if self._logger is not None:
            self._logger.info(f'[stack] {line}')

    def _pump_output(self, proc):
        """Drain the child's combined stdout/stderr into the ring buffer."""
        for raw in iter(proc.stdout.readline, ''):
            self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': raw.rstrip('\n')})
        rc = proc.wait()
        self._log(f'launch process exited (returncode={rc})')

    # ------------------------------------------------------------------ control
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, mode: str, mock_robot: bool, mock_camera: bool, config: dict) -> dict:
        with self._lock:
            if self.is_running():
                return {'ok': False, 'error': 'stack already running (stop or restart it first)'}

            # Never launch on top of leftovers from a previous GUI session.
            reaped = self._reap_stale()

            # Hard graph guard: even after reaping by name, the ROS graph can
            # take a moment to drop a dead executor, and a stray executor we
            # don't recognise (different launch) could still be present. Refuse
            # to launch until the graph shows zero /execute_skill servers, so no
            # button combination can ever bring up a second one (request #1).
            persisted = self._wait_graph_clear()
            if persisted is not None and persisted > 0:
                msg = (
                    f'{persisted} /execute_skill action server(s) still on the '
                    'graph after reaping — refusing to launch a duplicate. Stop '
                    'the stack and any stray executor, then start again.'
                )
                self._log(msg)
                return {'ok': False, 'error': msg, 'reaped': len(reaped)}

            command = _launch_command(mode, mock_robot, mock_camera, config)
            self._log('starting: ' + ' '.join(command))
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    # New session so we can signal the whole launch process tree.
                    start_new_session=True,
                )
            except Exception as exc:
                self._log(f'failed to start: {exc}')
                return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

            self._proc = proc
            try:
                self._pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                self._pgid = None
            self._target = {
                'mode': (mode or 'LLM').upper(),
                'mock_robot': bool(mock_robot),
                'mock_camera': bool(mock_camera),
                'mock_llm': bool(config.get('use_mock_llm', True)),
                'command': ' '.join(command),
            }
            self._reader = threading.Thread(target=self._pump_output, args=(proc,), daemon=True)
            self._reader.start()
            result = {'ok': True, 'status': self.status()}
            if reaped:
                result['reaped'] = len(reaped)
                result['warning'] = (
                    f'reaped {len(reaped)} leftover stack process(es) from a '
                    'previous session before launching'
                )
            return result

    def stop(self) -> dict:
        with self._lock:
            if not self.is_running():
                self._proc = None
                return {'ok': True, 'status': self.status()}
            proc = self._proc
            pgid = os.getpgid(proc.pid)
            self._log('stopping stack (SIGINT)…')
            # SIGINT mimics Ctrl-C so ros2 launch shuts its nodes down cleanly.
            self._signal_group(pgid, signal.SIGINT)

        # Wait outside the lock so status() stays responsive during shutdown.
        if not self._wait_exit(proc, self.STOP_GRACE_S):
            self._log('stack did not exit on SIGINT, escalating to SIGTERM')
            self._signal_group(pgid, signal.SIGTERM)
            if not self._wait_exit(proc, self.FORCE_KILL_GRACE_S):
                self._log('stack still alive, sending SIGKILL')
                self._signal_group(pgid, signal.SIGKILL)
                self._wait_exit(proc, self.FORCE_KILL_GRACE_S)

        # The launch ran in its own session, so every node inherits this pgid;
        # confirm the whole group is gone rather than just the launch process.
        if self._group_alive(pgid):
            self._log('WARNING: stack process group still present after SIGKILL; '
                      'sweeping remaining nodes by name')
            self._reap_stale()

        with self._lock:
            self._proc = None
            self._pgid = None
        return {'ok': True, 'status': self.status()}

    def restart(self, mode: str, mock_robot: bool, mock_camera: bool, config: dict) -> dict:
        self.stop()
        return self.start(mode, mock_robot, mock_camera, config)

    # -------------------------------------------------------------- graph guard
    def _wait_graph_clear(self):
        """Poll the executor count until it hits 0 (or time out).

        Returns the last observed count, or None when no count source is wired
        (in which case the caller proceeds — the name-based reap is the only
        guard available). A returned count > 0 means an executor persisted and
        the caller must refuse to launch.
        """
        if self._executor_count is None:
            return None
        deadline = time.monotonic() + self.GRAPH_CLEAR_TIMEOUT_S
        count = self._safe_executor_count()
        while count > 0 and time.monotonic() < deadline:
            time.sleep(self.GRAPH_POLL_S)
            count = self._safe_executor_count()
        return count

    def _safe_executor_count(self) -> int:
        try:
            return int(self._executor_count())
        except Exception as exc:
            self._log(f'executor-count probe failed: {exc}')
            return 0

    # ------------------------------------------------------------ orphan reaping
    def _reap_stale(self) -> list:
        """SIGKILL leftover stack processes from a previous (crashed) session.

        Matches the launch file and each node executable by command line. We
        exclude our own GUI process group so we never kill the server we run in.
        Returns the (pid, pattern) pairs that were killed.
        """
        own = {os.getpid(), os.getppid()}
        reaped = []
        for pattern in _STACK_PROCESS_PATTERNS:
            for pid in self._pgrep(pattern):
                if pid in own:
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    self._log(f'cannot kill pid={pid} ({pattern}): permission denied')
                    continue
                reaped.append((pid, pattern))
                self._log(f'reaped stale process pid={pid} ({pattern})')
        return reaped

    @staticmethod
    def _pgrep(pattern: str) -> list:
        try:
            out = subprocess.run(
                ['pgrep', '-f', pattern],
                capture_output=True, text=True, timeout=5.0,
            )
        except Exception:
            return []
        pids = []
        for token in out.stdout.split():
            try:
                pids.append(int(token))
            except ValueError:
                pass
        return pids

    @staticmethod
    def _group_alive(pgid) -> bool:
        if not pgid:
            return False
        try:
            os.killpg(pgid, 0)  # signal 0 = existence check
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _signal_group(pgid: int, sig):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _wait_exit(proc, timeout: float) -> bool:
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        running = self.is_running()
        return {
            'running': running,
            'target': self._target,
            'pid': self._proc.pid if running else None,
            'returncode': None if running or self._proc is None else self._proc.returncode,
            'logs': list(self._logs),
        }
