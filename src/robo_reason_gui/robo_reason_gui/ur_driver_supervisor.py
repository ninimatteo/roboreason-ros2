"""UR driver supervisor with auto-retry / reconnect (Phase 5).

The stock UR ``ur_control.launch.py`` driver fails intermittently on startup —
it often takes several restarts before the controllers come up and the robot is
reachable. Rather than make the operator babysit that, the GUI owns the driver
process and treats a failed launch as a *transient* condition: it relaunches
with a short backoff until the robot is ready (verified through the bridge's
connectivity probes) or an attempt budget is exhausted.

Readiness is decided by ``ready_check`` (the bridge's ``robot_ready``), not by
log scraping. ERROR_MARKERS lines are the one exception: they abort the
current attempt immediately (instead of waiting out READY_TIMEOUT_S) since a
launch that's already logged a failure isn't going to become ready by waiting
longer. All other markers are kept only as human-readable instrumentation in
the ring buffer and per-attempt history. Once connected, the supervisor watches
for an unexpected process exit and reconnects automatically.

This module only supervises a subprocess; it holds no ROS handles of its own.
"""

import os
import signal
import subprocess
import threading
import time
from collections import deque

from robo_reason_bringup.config import settings

# Child processes spawned by ur_control.launch.py that don't die when the
# launch process group is signalled (each node runs in its own session).
# These are swept by name in _reap_stale() to guarantee a clean state before
# each launch attempt and after stop().
_DRIVER_PROCESS_PATTERNS = (
    'ur_control.launch.py',
    'ur_ros2_control_node',
    'controller_stopper_node',
    'robot_state_helper',
    'urscript_interface',
    'trajectory_until_node',
)

# Defaults for the lab UR5cb; overridable per start()/reconnect() request.
DEFAULT_ROBOT_IP = settings.ROBOT_IP
DEFAULT_REVERSE_IP = settings.REVERSE_IP

# Substrings scanned in driver stdout purely for operator-facing log context.
SUCCESS_MARKERS = (
    'Robot connected to reverse interface',
    'Robot ready to receive control commands',
    'Activated cyclic mode',
)

# The pendant only accepts motion once this appears: the controllers can be up
# (probes green) while the teach pendant is still not connected on the reverse
# interface. Tracking it lets the GUI keep the LED amber until the robot can
# actually move (request #3).
REVERSE_INTERFACE_MARKER = 'Robot connected to reverse interface'
REVERSE_DROPPED_MARKER = 'Connection to reverse interface dropped'
ERROR_MARKERS = (
    'Failed to connect',
    'connection refused',
    'Timeout',
    'No connection to robot',
    'could not be reached',
)


def _driver_command(robot_ip: str, reverse_ip: str) -> list:
    """Build the ``ros2 launch ur_robot_driver ur_control.launch.py ...`` argv."""
    return [
        'ros2', 'launch', 'ur_robot_driver', 'ur_control.launch.py',
        'ur_type:=ur5',
        f'robot_ip:={robot_ip}',
        f'reverse_ip:={reverse_ip}',
        'use_fake_hardware:=false',
        'initial_joint_controller:=scaled_joint_trajectory_controller',
        'launch_rviz:=false',
    ]


class UrDriverSupervisor:
    """Launch and babysit the real UR driver, retrying through flaky startups."""

    LOG_CAPACITY = settings.UR_DRIVER_LOG_CAPACITY
    HISTORY_CAPACITY = settings.UR_DRIVER_HISTORY_CAPACITY
    MAX_ATTEMPTS = settings.UR_DRIVER_MAX_ATTEMPTS
    READY_TIMEOUT_S = settings.UR_DRIVER_READY_TIMEOUT_S   # per-attempt wait for the robot to come up
    BACKOFF_S = settings.UR_DRIVER_BACKOFF_S               # pause between failed attempts
    READY_POLL_S = settings.UR_DRIVER_READY_POLL_S
    STOP_GRACE_S = settings.UR_DRIVER_STOP_GRACE_S
    FORCE_KILL_GRACE_S = settings.PROCESS_FORCE_KILL_GRACE_S

    # State machine: stopped -> connecting -> connected ; failed on giving up.
    def __init__(self, ready_check, logger=None):
        self._ready_check = ready_check
        self._logger = logger
        self._lock = threading.Lock()
        self._logs = deque(maxlen=self.LOG_CAPACITY)
        self._history = deque(maxlen=self.HISTORY_CAPACITY)
        self._proc = None
        self._worker = None
        self._stop_event = threading.Event()
        # Set by _pump_output as soon as an ERROR_MARKERS line appears for the
        # current attempt, so _await_ready can abort immediately instead of
        # waiting out the full READY_TIMEOUT_S on an attempt that has already
        # announced it failed (e.g. "Failed to connect", "connection refused").
        self._error_event = threading.Event()
        self._state = 'stopped'
        self._attempt = 0
        self._reverse_connected = False  # pendant seen on the reverse interface
        self._params = {'robot_ip': DEFAULT_ROBOT_IP, 'reverse_ip': DEFAULT_REVERSE_IP}

    # ------------------------------------------------------------------ logging
    def _log(self, line: str):
        self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': line})
        if self._logger is not None:
            self._logger.info(f'[ur-driver] {line}')

    def _set_state(self, state: str):
        if state != self._state:
            self._state = state
            self._log(f'state -> {state}')

    # ------------------------------------------------------------ orphan reaping
    def _reap_stale(self) -> list:
        """SIGKILL leftover driver processes from a previous (crashed) session.

        ur_control.launch.py spawns each ROS node in its own session, so
        killpg() on the launch process's group does not reach the children.
        This sweep catches all survivors by executable name, excluding our own
        process so we never kill the GUI server itself.
        """
        own = {os.getpid(), os.getppid()}
        reaped = []
        for pattern in _DRIVER_PROCESS_PATTERNS:
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
                self._log(f'reaped stale driver process pid={pid} ({pattern})')
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

    # ------------------------------------------------------------------ control
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def robot_connected(self) -> bool:
        """True once the teach pendant is connected on the reverse interface.

        Gates the header LED to green (request #3): the controllers can be up
        without the pendant, in which case the robot still cannot move.
        """
        return self.is_running() and self._reverse_connected

    def start(self, params: dict = None) -> dict:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return {'ok': False, 'error': 'driver supervisor already running (stop or reconnect)'}
            # Sweep any surviving nodes from previous crashed launches before
            # starting — without this, orphaned ur_ros2_control_node processes
            # hold the RTDE connection and block the new attempt.
            self._reap_stale()
            self._params = self._resolve_params(params)
            self._stop_event.clear()
            self._attempt = 0
            self._history.clear()
            self._worker = threading.Thread(
                target=self._run, args=(dict(self._params),), daemon=True,
            )
            self._set_state('connecting')
            self._worker.start()
            return {'ok': True, 'status': self.status()}

    def stop(self) -> dict:
        self._stop_event.set()
        self._terminate_current()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self.STOP_GRACE_S + 2 * self.FORCE_KILL_GRACE_S)
        with self._lock:
            self._worker = None
            self._proc = None
            self._reverse_connected = False
            self._set_state('stopped')
        # Sweep by name after signalling — catches nodes that ran in their own
        # session and didn't receive the killpg signal.
        self._reap_stale()
        return {'ok': True, 'status': self.status()}

    def reconnect(self, params: dict = None) -> dict:
        """Tear down any current attempt and start a fresh retry loop."""
        self.stop()
        return self.start(params)

    def _resolve_params(self, params: dict) -> dict:
        params = params or {}
        return {
            'robot_ip': params.get('robot_ip') or self._params.get('robot_ip') or DEFAULT_ROBOT_IP,
            'reverse_ip': params.get('reverse_ip') or self._params.get('reverse_ip') or DEFAULT_REVERSE_IP,
        }

    # ------------------------------------------------------------------ worker
    def _run(self, params: dict):
        """Retry-with-backoff launch loop; runs on the worker thread."""
        command = _driver_command(params['robot_ip'], params['reverse_ip'])
        while not self._stop_event.is_set() and self._attempt < self.MAX_ATTEMPTS:
            self._attempt += 1
            attempt = self._attempt
            started = time.monotonic()
            self._set_state('connecting')
            self._log(f"attempt {attempt}/{self.MAX_ATTEMPTS}: {' '.join(command)}")

            proc = self._spawn(command)
            if proc is None:
                self._record_attempt(attempt, 'spawn-failed', started, 'Popen failed')
                if self._backoff():
                    continue
                break

            outcome = self._await_ready(proc)
            duration = time.monotonic() - started

            if outcome == 'ready':
                self._record_attempt(attempt, 'connected', started, None)
                self._set_state('connected')
                self._attempt = 0  # reset budget now that we're up
                self._watch_connected(proc)
                if self._stop_event.is_set():
                    break
                # Unexpected drop while connected -> loop and reconnect.
                self._log('connection lost, attempting to reconnect')
                continue

            # Not ready: kill this attempt, record why, back off, retry.
            self._terminate(proc)
            if outcome == 'stopped':
                break
            reason = {'exited': 'driver process exited before ready',
                      'error': 'driver logged an error before connecting',
                      'timeout': f'robot not ready within {self.READY_TIMEOUT_S:.0f}s'}.get(outcome, outcome)
            self._record_attempt(attempt, outcome, started, reason)
            self._log(f'attempt {attempt} failed ({reason}, {duration:.1f}s)')
            if not self._backoff():
                break

        with self._lock:
            if self._stop_event.is_set():
                self._set_state('stopped')
            elif self._state != 'connected':
                self._set_state('failed')
                if self._attempt >= self.MAX_ATTEMPTS:
                    self._log(f'giving up after {self.MAX_ATTEMPTS} attempts')

    def _spawn(self, command: list):
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except Exception as exc:
            self._log(f'failed to launch driver: {exc}')
            return None
        with self._lock:
            self._proc = proc
            self._reverse_connected = False  # fresh process: pendant not yet up
        self._error_event.clear()
        threading.Thread(target=self._pump_output, args=(proc,), daemon=True).start()
        return proc

    def _pump_output(self, proc):
        """Drain driver stdout into the ring buffer, tagging known markers."""
        for raw in iter(proc.stdout.readline, ''):
            line = raw.rstrip('\n')
            tag = ''
            if any(m in line for m in SUCCESS_MARKERS):
                tag = 'OK '
            elif any(m in line for m in ERROR_MARKERS):
                tag = 'ERR '
                self._error_event.set()
            if REVERSE_INTERFACE_MARKER in line:
                self._reverse_connected = True
            elif REVERSE_DROPPED_MARKER in line:
                self._reverse_connected = False
            self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': f'{tag}{line}'})

    def _await_ready(self, proc) -> str:
        """Wait for readiness. Returns ready|exited|error|timeout|stopped.

        Bails out as soon as an ERROR_MARKERS line is seen instead of waiting
        out the full READY_TIMEOUT_S — a launch that's already logged e.g.
        "Failed to connect" isn't going to become ready by waiting longer.
        """
        deadline = time.monotonic() + self.READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return 'stopped'
            if proc.poll() is not None:
                return 'exited'
            if self._safe_ready():
                return 'ready'
            if self._error_event.is_set():
                return 'error'
            time.sleep(self.READY_POLL_S)
        return 'timeout'

    def _watch_connected(self, proc):
        """Block while connected; return when the process dies or we're stopped."""
        while not self._stop_event.is_set():
            if proc.poll() is not None:
                self._log(f'driver process exited unexpectedly (returncode={proc.returncode})')
                return
            # If the robot drops off the probes while the process lingers, treat
            # it as a lost connection and force a clean relaunch.
            if not self._safe_ready():
                self._log('robot probes went stale while connected')
                self._terminate(proc)
                return
            time.sleep(1.0)

    def _backoff(self) -> bool:
        """Sleep BACKOFF_S unless stopped; False means we were asked to stop."""
        return not self._stop_event.wait(self.BACKOFF_S)

    def _safe_ready(self) -> bool:
        try:
            return bool(self._ready_check())
        except Exception as exc:
            self._log(f'ready_check error: {exc}')
            return False

    def _record_attempt(self, attempt, outcome, started, error):
        self._history.append({
            'attempt': attempt,
            'started': time.strftime('%H:%M:%S'),
            'outcome': outcome,
            'duration_s': round(time.monotonic() - started, 1),
            'error': error,
        })

    # ------------------------------------------------------------- termination
    def _terminate_current(self):
        with self._lock:
            proc = self._proc
        if proc is not None:
            self._terminate(proc)

    def _terminate(self, proc):
        if proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        self._signal_group(pgid, signal.SIGINT)
        if not self._wait_exit(proc, self.STOP_GRACE_S):
            self._signal_group(pgid, signal.SIGTERM)
            if not self._wait_exit(proc, self.FORCE_KILL_GRACE_S):
                self._signal_group(pgid, signal.SIGKILL)
                self._wait_exit(proc, self.FORCE_KILL_GRACE_S)

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
            'state': self._state,
            'attempt': self._attempt,
            'max_attempts': self.MAX_ATTEMPTS,
            'pid': self._proc.pid if running else None,
            'robot_ready': self._safe_ready(),
            'robot_connected': self.robot_connected(),
            'params': dict(self._params),
            'history': list(self._history),
            'logs': list(self._logs),
        }
