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
        f"reasoning_method:={config.get('reasoning_method', 'fhp')}",
        f"model_name:={config.get('model_name', 'groq/llama4-scout-17b')}",
        f"temperature:={config.get('temperature', 0.1)}",
    ]


class StackSupervisor:
    """Start/stop/restart the ROS2 stack as a child process group."""

    LOG_CAPACITY = 400
    STOP_GRACE_S = 8.0

    def __init__(self, logger=None):
        self._logger = logger
        self._lock = threading.Lock()
        self._proc = None
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
            self._target = {
                'mode': (mode or 'LLM').upper(),
                'mock_robot': bool(mock_robot),
                'mock_camera': bool(mock_camera),
                'mock_llm': bool(config.get('use_mock_llm', True)),
                'command': ' '.join(command),
            }
            self._reader = threading.Thread(target=self._pump_output, args=(proc,), daemon=True)
            self._reader.start()
            return {'ok': True, 'status': self.status()}

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
            if not self._wait_exit(proc, 3.0):
                self._log('stack still alive, sending SIGKILL')
                self._signal_group(pgid, signal.SIGKILL)
                self._wait_exit(proc, 3.0)

        with self._lock:
            self._proc = None
        return {'ok': True, 'status': self.status()}

    def restart(self, mode: str, mock_robot: bool, mock_camera: bool, config: dict) -> dict:
        self.stop()
        return self.start(mode, mock_robot, mock_camera, config)

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
