"""Structured per-run debug artifact recorder for planner nodes.

Every /plan_task call (LLM or VLM, mock or real) gets its own timestamped
folder under DEBUG_DIR containing the command, the modality/options used,
the response or error, targeted log lines for just that run, and (VLM only)
the raw camera frame plus a point+bounding-box debug overlay.

A single summary.csv at the debug root gets one row per run, so past
experiments can be scanned/filtered without opening every folder.

This exists because the GUI's in-memory log ring buffers get flooded by
high-frequency polling (e.g. camera GetImage spam) within minutes, making
them useless for after-the-fact debugging of a specific command run — see
DEBUG_DIR for the durable, per-run alternative.
"""
import csv
import json
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from robo_reason_bringup.config import settings

_TZ = ZoneInfo(settings.DEBUG_TIMEZONE)


def _now() -> datetime:
    """Wall-clock time in the operator's timezone, not the container's (UTC)."""
    return datetime.now(_TZ)

_CSV_FIELDS = [
    'timestamp', 'run_id', 'mode', 'command', 'reasoning_method', 'model_name',
    'temperature', 'success', 'num_steps', 'error',
]
_csv_lock = threading.Lock()


class DebugRun:
    """Records all debug artifacts for a single planner call."""

    def __init__(self, mode: str, command: str, config: dict):
        self.mode = mode
        self.command = command
        self.config = config
        self._started = _now()
        self._logs = []

        root = Path(settings.DEBUG_DIR)
        self.run_id = f'{self._started:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}'
        self.dir = root / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)

        (self.dir / 'command.txt').write_text(command or '')
        (self.dir / 'config.json').write_text(json.dumps(config, indent=2, default=str))

    def log(self, line: str) -> None:
        """Record one line of 'terminal output' scoped to just this run."""
        self._logs.append(f'{_now():%H:%M:%S.%f} {line}')

    def save_raw_frame(self, source_path: str) -> None:
        """Copy the captured camera frame into <run>/raw/."""
        raw_dir = self.dir / 'raw'
        raw_dir.mkdir(exist_ok=True)
        try:
            shutil.copy2(source_path, raw_dir / Path(source_path).name)
        except OSError as exc:
            self.log(f'[debug] failed to copy raw frame {source_path}: {exc}')

    def save_debug_image(self, source_path: str) -> None:
        """Copy an already-rendered point+bounding-box overlay into <run>/debug/."""
        debug_dir = self.dir / 'debug'
        debug_dir.mkdir(exist_ok=True)
        try:
            shutil.copy2(source_path, debug_dir / 'debug.png')
        except OSError as exc:
            self.log(f'[debug] failed to copy debug image {source_path}: {exc}')

    def save_generated_scene(self, source_path: str) -> None:
        """Copy the VLM-generated scene JSON (VLM_LLM mode) into <run>/generated_scene.json."""
        try:
            shutil.copy2(source_path, self.dir / 'generated_scene.json')
        except OSError as exc:
            self.log(f'[debug] failed to copy generated scene {source_path}: {exc}')

    def finish(self, success: bool, response: dict = None, error: str = None) -> None:
        """Write the response/error/logs and append one row to summary.csv."""
        (self.dir / 'response.json').write_text(
            json.dumps(response, indent=2, default=str) if response is not None else ''
        )
        if error:
            (self.dir / 'error.txt').write_text(error)
        (self.dir / 'logs.txt').write_text('\n'.join(self._logs))

        num_steps = len(response.get('plan', [])) if isinstance(response, dict) else None
        self._append_summary_row(success, num_steps, error)

    def _append_summary_row(self, success: bool, num_steps, error) -> None:
        root = Path(settings.DEBUG_DIR)
        root.mkdir(parents=True, exist_ok=True)
        csv_path = root / 'summary.csv'
        row = {
            'timestamp': self._started.isoformat(timespec='seconds'),
            'run_id': self.run_id,
            'mode': self.mode,
            'command': self.command,
            'reasoning_method': self.config.get('reasoning_method', ''),
            'model_name': self.config.get('model_name', ''),
            'temperature': self.config.get('temperature', ''),
            'success': success,
            'num_steps': num_steps if num_steps is not None else '',
            'error': (error or '').splitlines()[0][:300] if error else '',
        }
        with _csv_lock:
            is_new = not csv_path.exists()
            with open(csv_path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(row)
