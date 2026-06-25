"""UR driver supervisor with auto-retry / reconnect (Phase 5).

The stock UR ``ur_control.launch.py`` driver fails intermittently on startup —
it often takes several restarts before the controllers come up and the robot is
reachable. Rather than make the operator babysit that, the GUI owns the driver
process and treats a failed launch as a *transient* condition: it relaunches
with a short backoff until the robot is ready (verified through the bridge's
connectivity probes) or an attempt budget is exhausted.

Readiness is decided by ``ready_check`` (the bridge's ``robot_ready``), not by
log scraping — log markers are kept only as human-readable instrumentation in
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

# Defaults for the lab UR5cb; overridable per start()/reconnect() request.
DEFAULT_ROBOT_IP = '192.168.2.60'
DEFAULT_REVERSE_IP = '192.168.2.80'

# Substrings scanned in driver stdout purely for operator-facing log context.
SUCCESS_MARKERS = (
    'Robot connected to reverse interface',
    'Robot ready to receive control commands',
    'Activated cyclic mode',
)
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

    LOG_CAPACITY = 600
    HISTORY_CAPACITY = 30
    MAX_ATTEMPTS = 10
    READY_TIMEOUT_S = 30.0   # per-attempt wait for the robot to come up
    BACKOFF_S = 2.0          # pause between failed attempts
    READY_POLL_S = 0.5
    STOP_GRACE_S = 8.0

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
        self._state = 'stopped'
        self._attempt = 0
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

    # ------------------------------------------------------------------ control
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, params: dict = None) -> dict:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return {'ok': False, 'error': 'driver supervisor already running (stop or reconnect)'}
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
            worker.join(timeout=self.STOP_GRACE_S + 6.0)
        with self._lock:
            self._worker = None
            self._proc = None
            self._set_state('stopped')
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
            self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': f'{tag}{line}'})

    def _await_ready(self, proc) -> str:
        """Wait for readiness. Returns ready|exited|timeout|stopped."""
        deadline = time.monotonic() + self.READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return 'stopped'
            if proc.poll() is not None:
                return 'exited'
            if self._safe_ready():
                return 'ready'
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
            if not self._wait_exit(proc, 3.0):
                self._signal_group(pgid, signal.SIGKILL)
                self._wait_exit(proc, 3.0)

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
            'params': dict(self._params),
            'history': list(self._history),
            'logs': list(self._logs),
        }
