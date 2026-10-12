"""Simulation supervisor: lets the GUI start and stop the Gazebo simulation.

Only used by a GUI started with the sim profile, where the simulation takes
the place of both the UR driver and the Orbbec camera script. Same process
handling as CameraServiceSupervisor (child process group, log ring buffer,
readiness from a bridge probe), different command.
"""

import os
import signal
import subprocess
import threading

from robo_reason_gui.camera_service_supervisor import CameraServiceSupervisor
from robo_reason_gui.process_scope import same_environment

# Leftovers of a simulation whose GUI died without stopping it. A second
# Gazebo server would publish a second set of joint states and camera frames.
_SIM_PROCESS_PATTERNS = (
    'robo_reason_sim sim.launch.py',
    'ign gazebo',
    'lib/robo_reason_sim/gripper_shim_node',
)


class SimSupervisor(CameraServiceSupervisor):
    LOG_PREFIX = 'sim'

    def __init__(self, ready_check=None, logger=None):
        super().__init__(script_path='', ready_check=ready_check, logger=logger)
        self._show_gazebo = None

    def start(self, show_gazebo: bool = True) -> dict:
        with self._lock:
            if self.is_running():
                return {'ok': False, 'error': 'simulation already running'}
            self._reap_stale()
            command = ['ros2', 'launch', 'robo_reason_sim', 'sim.launch.py',
                       f"gui:={'true' if show_gazebo else 'false'}"]
            self._log('starting: ' + ' '.join(command))
            try:
                proc = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, start_new_session=True,
                )
            except Exception as exc:
                return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
            self._proc = proc
            self._show_gazebo = show_gazebo
            threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
            return {'ok': True, 'status': self.status()}

    def _reap_stale(self):
        own = {os.getpid(), os.getppid()}
        for pattern in _SIM_PROCESS_PATTERNS:
            out = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True)
            for token in out.stdout.split():
                pid = int(token)
                if pid in own or not same_environment(pid):
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                    self._log(f'reaped stale simulation process pid={pid} ({pattern})')
                except (ProcessLookupError, PermissionError):
                    pass

    def status(self) -> dict:
        status = super().status()
        status['show_gazebo'] = self._show_gazebo
        return status
