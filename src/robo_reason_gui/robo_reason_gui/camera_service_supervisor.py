"""Camera service supervisor — wraps scripts/run_orbbec_registered.sh.

The Orbbec camera requires a separate launch script that sources ROS 2 and
brings up the registered depth+colour streams. Rather than duplicating that
logic inside a node, we supervise the script as a subprocess (same proven
pattern as UrDriverSupervisor). The GUI gains full start/stop control and a
live log tail; readiness is signalled by the bridge's camera_available() probe
(i.e. the /camera/get_image service becomes reachable).
"""

import os
import signal
import subprocess
import threading
import time
from collections import deque


class CameraServiceSupervisor:
    """Supervise the Orbbec camera script as a child process."""

    LOG_CAPACITY = 300
    STOP_GRACE_S = 6.0

    def __init__(self, script_path: str, ready_check=None, logger=None):
        """
        Parameters
        ----------
        script_path : str
            Absolute path to run_orbbec_registered.sh (installed in the
            package's share/scripts directory).
        ready_check : callable, optional
            Returns True once /camera/get_image is reachable (bridge.camera_available).
        logger : rclpy.impl.rcutils_logger.RcutilsLogger, optional
        """
        self._script = script_path
        self._ready_check = ready_check
        self._logger = logger
        self._lock = threading.Lock()
        self._proc = None
        self._logs = deque(maxlen=self.LOG_CAPACITY)

    # ------------------------------------------------------------------ logging
    def _log(self, line: str):
        self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': line})
        if self._logger:
            self._logger.info(f'[camera] {line}')

    # ------------------------------------------------------------------ control
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> dict:
        with self._lock:
            if self.is_running():
                return {'ok': False, 'error': 'camera service already running'}
            if not os.path.isfile(self._script):
                return {'ok': False, 'error': f'camera script not found: {self._script}'}
            self._log(f'starting: bash {self._script}')
            try:
                proc = subprocess.Popen(
                    ['bash', self._script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except Exception as exc:
                return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
            self._proc = proc
            threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
            return {'ok': True, 'status': self.status()}

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            self._log('stopping camera (SIGINT)…')
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pgid = None
            if pgid:
                self._signal_group(pgid, signal.SIGINT)
                if not self._wait_exit(proc, self.STOP_GRACE_S):
                    self._signal_group(pgid, signal.SIGTERM)
                    if not self._wait_exit(proc, 3.0):
                        self._signal_group(pgid, signal.SIGKILL)
                        self._wait_exit(proc, 3.0)
        with self._lock:
            self._proc = None
        return {'ok': True, 'status': self.status()}

    # ------------------------------------------------------------------ output
    def _pump(self, proc):
        for raw in iter(proc.stdout.readline, ''):
            self._logs.append({'t': time.strftime('%H:%M:%S'), 'line': raw.rstrip('\n')})
        rc = proc.wait()
        self._log(f'camera process exited (returncode={rc})')

    # ------------------------------------------------------------ readiness
    def _safe_ready(self) -> bool:
        try:
            return bool(self._ready_check()) if self._ready_check else False
        except Exception:
            return False

    # ------------------------------------------------------------- termination
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
            'ready': self._safe_ready(),
            'pid': self._proc.pid if running else None,
            'returncode': (
                None if running or self._proc is None else self._proc.returncode
            ),
            'logs': list(self._logs),
        }
