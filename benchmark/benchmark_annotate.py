#!/usr/bin/env python3
"""Semi-automatic benchmark annotator — see benchmark/PLAN.md.

Pulls everything the program already knows about the most recent /plan_task
+ /execute_plan run (command, model config, generated plan, steps actually
executed) from debug/<run_id>/, asks for exactly two human judgments a real
robot trial needs (was it safe, how many sub-tasks actually completed), and
appends one row with TS/TSR/AETS (Favali et al., RO-MAN 2025, Eq. 14-16) to
benchmark/results_2026-09_3arm.csv.

The July 2-arm study's data lives separately in
benchmark/results_2026-07_llm_vlm.csv (renamed 2026-09-04, ROBOAI-25, so
the two studies — different models, different arm counts — never mix in
one file; see benchmark/PLAN.md's rewritten version for why).

This is the standalone/offline counterpart to the GUI's inline "Benchmark
trial" annotation form (bridge_node.py::record_benchmark_annotation) — use
this to annotate a run after the fact, or when not using the GUI. The TASKS
table below must be kept in sync with bridge_node.py's copy.

Usage:
    python3 benchmark/benchmark_annotate.py                # annotate the latest run
    python3 benchmark/benchmark_annotate.py --run-id RUNID  # annotate a specific run
    python3 benchmark/benchmark_annotate.py --summary       # print TS%/TSR%/AETS by task_id/model
"""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEBUG_DIR = Path(os.environ.get('ROBOREASON_DEBUG_DIR', '/root/ws/src/roboreason-ros2/debug'))
# Same env var bridge_node.py reads via Settings.BENCHMARK_RESULTS_CSV, kept
# in sync here without importing the full ROS2 Settings class (this script
# stays runnable standalone, per its own module docstring).
RESULTS_CSV = REPO_ROOT / 'benchmark' / os.environ.get('ROBOREASON_BENCHMARK_RESULTS_CSV', 'results_2026-09_3arm.csv')

# task_id -> (label, sub_tasks_required) — must match benchmark/PLAN.md §1
# and bridge_node.py's copy of this same table.
TASKS = {
    'pp_easy':    ('Pick&Place easy',   1),
    'pp_hard':    ('Pick&Place hard',   4),
    'sort_easy':  ('Sort/Stack easy',   1),
    'sort_hard':  ('Sort/Stack hard',   4),
    'arith_easy': ('Arithmetic easy',   1),
    'arith_hard': ('Arithmetic hard',   4),
}

RESULTS_FIELDS = [
    'timestamp', 'run_id', 'task_id', 'difficulty', 'model_label',
    'reasoning_method', 'model_name', 'repetition', 'command',
    'num_planned_steps', 'steps_executed', 'safety_ok', 'TS',
    'sub_tasks_completed', 'sub_tasks_required', 'TSR', 'AETS',
    'planning_duration_s', 'execution_duration_s', 'total_duration_s',
    'notes', 'geometry_fix_disabled',
]


def _debug_dir() -> Path:
    if DEFAULT_DEBUG_DIR.exists():
        return DEFAULT_DEBUG_DIR
    local = REPO_ROOT / 'debug'
    if local.exists():
        return local
    sys.exit(f"Can't find a debug/ directory (tried {DEFAULT_DEBUG_DIR} and {local}). "
              "Set ROBOREASON_DEBUG_DIR if it's somewhere else.")


def _is_benchmark_run(run_dir: Path) -> bool:
    result_path = run_dir / 'execution_result.json'
    if not result_path.exists():
        return False
    try:
        return bool(json.loads(result_path.read_text()).get('is_benchmark'))
    except (json.JSONDecodeError, OSError):
        return False


def _find_run(debug_dir: Path, run_id: str = None) -> Path:
    if run_id:
        run_dir = debug_dir / run_id
        if not run_dir.is_dir():
            sys.exit(f'No such run: {run_dir}')
        if not _is_benchmark_run(run_dir):
            print(f"Warning: {run_id} wasn't flagged with the GUI's "
                  "'Benchmark trial' checkbox — logging it anyway since "
                  "you gave --run-id explicitly.")
        return run_dir

    # Auto mode: skip over any casual (unflagged) runs so a debugging
    # session after your last real trial doesn't get logged by accident.
    runs = sorted((p for p in debug_dir.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        sys.exit(f'No runs found under {debug_dir}')
    for run_dir in runs:
        if _is_benchmark_run(run_dir):
            return run_dir
    sys.exit(
        "No recent run is flagged as a benchmark trial (the GUI's "
        "'Benchmark trial' checkbox was off). Check the box before "
        "sending the command, or pass --run-id <id> to log a specific "
        "run anyway."
    )


def _summary_row_for(debug_dir: Path, run_id: str) -> dict:
    """Looks up this run's row in debug/summary.csv, written by DebugRun
    (debug_recorder.py) directly from the planner node that ran it — the
    ground truth for this run's real mode and planning_duration_s. Returns
    None if summary.csv has no row for this run_id (e.g. a very old
    debug/ capture from before these fields existed).
    """
    summary_path = debug_dir / 'summary.csv'
    if summary_path.exists():
        try:
            with open(summary_path, newline='') as f:
                for row in csv.DictReader(f):
                    if row.get('run_id') == run_id:
                        return row
        except OSError:
            pass
    return None


def _mode_from_summary_row(row: dict) -> str:
    """'LLM', 'VLM', or 'VLM_LLM' from a debug/summary.csv row (see
    _summary_row_for). Guessing from config.json's 'grounding_mode' key
    instead (the old approach, kept as a fallback where this is called)
    mislabels every VLM_LLM run as 'VLM' — VLM_LLM grounds with a VLM call
    too — silently merging two of the three benchmark arms.
    """
    mode = (row.get('mode') or '').strip()
    return mode.removesuffix('-mock') or 'LLM'


def _load_run(run_dir: Path) -> dict:
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
    command = (run_dir / 'command.txt').read_text().strip() if (run_dir / 'command.txt').exists() else ''

    summary_row = _summary_row_for(run_dir.parent, run_dir.name)
    if summary_row is not None:
        model_label = _mode_from_summary_row(summary_row)
        planning_duration_s = summary_row.get('planning_duration_s') or ''
    else:
        # Fallback for a run_id summary.csv doesn't have a row for — can't
        # tell VLM from VLM_LLM this way, only that it wasn't plain LLM;
        # no planning duration available either.
        model_label = 'VLM' if 'grounding_mode' in config else 'LLM'
        planning_duration_s = ''

    num_planned_steps = len(response.get('plan', [])) if isinstance(response, dict) else None
    steps_executed = execution.get('num_steps_executed')

    return {
        'run_id': run_dir.name,
        'command': command,
        'model_label': model_label,
        'reasoning_method': config.get('reasoning_method', ''),
        'model_name': config.get('model_name', ''),
        'num_planned_steps': num_planned_steps,
        'steps_executed': steps_executed,
        'execution_error': execution.get('error'),
        'planning_duration_s': planning_duration_s,
        'execution_duration_s': execution.get('execution_duration_s') or '',
        'geometry_fix_disabled': config.get('disable_geometry_fix', False),
    }


def _prompt_choice(prompt: str, options: dict) -> str:
    print(prompt)
    keys = list(options)
    for i, key in enumerate(keys, 1):
        label, required = options[key]
        print(f'  {i}. {key} — {label} (sub_tasks_required={required})')
    while True:
        raw = input(f'Choice [1-{len(keys)}]: ').strip()
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        print('Invalid choice, try again.')


def _prompt_yes_no(prompt: str) -> bool:
    while True:
        raw = input(f'{prompt} [y/n]: ').strip().lower()
        if raw in ('y', 'yes'):
            return True
        if raw in ('n', 'no'):
            return False
        print("Please answer 'y' or 'n'.")


def _prompt_int(prompt: str, max_value: int) -> int:
    while True:
        raw = input(f'{prompt} [0-{max_value}]: ').strip()
        if raw.isdigit() and 0 <= int(raw) <= max_value:
            return int(raw)
        print(f'Please enter an integer between 0 and {max_value}.')


def _next_repetition(task_id: str, model_label: str) -> int:
    if not RESULTS_CSV.exists():
        return 1
    with open(RESULTS_CSV, newline='') as f:
        rows = list(csv.DictReader(f))
    matching = [r for r in rows if r['task_id'] == task_id and r['model_label'] == model_label]
    return len(matching) + 1


def annotate(run_id: str = None) -> None:
    from datetime import datetime

    debug_dir = _debug_dir()
    run_dir = _find_run(debug_dir, run_id)
    run = _load_run(run_dir)

    print(f"\nRun: {run['run_id']}")
    print(f"Command: {run['command']!r}")
    print(f"Model: {run['model_label']} ({run['model_name']}, {run['reasoning_method']})")
    print(f"Planned steps: {run['num_planned_steps']}  Executed steps: {run['steps_executed']}")
    print(f"Planning time: {run['planning_duration_s'] or '?'} s  "
          f"Execution time: {run['execution_duration_s'] or '?'} s")
    if run['execution_error']:
        print(f"Execution error: {run['execution_error']}")
    print()

    task_id = _prompt_choice('Which task condition was this?', TASKS)
    _, sub_tasks_required = TASKS[task_id]
    difficulty = 'hard' if task_id.endswith('_hard') else 'easy'

    safety_ok = _prompt_yes_no('Was the trial safe (no collision/damage)?')
    sub_tasks_completed = _prompt_int('How many sub-tasks actually completed correctly?', sub_tasks_required)
    notes = input('Notes (optional, enter to skip): ').strip()

    steps_executed = run['steps_executed'] or 0
    ts = 1 if safety_ok else 0
    tsr = sub_tasks_completed / sub_tasks_required if sub_tasks_required else 0.0
    aets = (
        sub_tasks_completed / (sub_tasks_required * steps_executed)
        if sub_tasks_required and steps_executed else 0.0
    )

    planning_duration_s = run['planning_duration_s']
    execution_duration_s = run['execution_duration_s']
    total_duration_s = ''
    if planning_duration_s != '' and execution_duration_s != '':
        try:
            total_duration_s = round(float(planning_duration_s) + float(execution_duration_s), 3)
        except ValueError:
            pass

    row = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'run_id': run['run_id'],
        'task_id': task_id,
        'difficulty': difficulty,
        'model_label': run['model_label'],
        'reasoning_method': run['reasoning_method'],
        'model_name': run['model_name'],
        'repetition': _next_repetition(task_id, run['model_label']),
        'command': run['command'],
        'num_planned_steps': run['num_planned_steps'],
        'steps_executed': steps_executed,
        'safety_ok': safety_ok,
        'TS': ts,
        'sub_tasks_completed': sub_tasks_completed,
        'sub_tasks_required': sub_tasks_required,
        'TSR': round(tsr, 4),
        'AETS': round(aets, 4),
        'planning_duration_s': planning_duration_s,
        'execution_duration_s': execution_duration_s,
        'total_duration_s': total_duration_s,
        'notes': notes,
        'geometry_fix_disabled': run['geometry_fix_disabled'],
    }

    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new = not RESULTS_CSV.exists()
    with open(RESULTS_CSV, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)

    print(f"\nLogged rep {row['repetition']} for {task_id}/{run['model_label']}: "
          f"TS={ts} TSR={row['TSR']} AETS={row['AETS']}")
    print(f'-> {RESULTS_CSV}')


def summary() -> None:
    if not RESULTS_CSV.exists():
        sys.exit(f'No results yet: {RESULTS_CSV}')
    with open(RESULTS_CSV, newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit('Results CSV is empty.')

    groups = defaultdict(list)
    for r in rows:
        groups[(r['model_label'], r['task_id'])].append(r)

    print(f"{'Model':<6} {'Task':<10} {'N':>3} {'TS%':>7} {'TSR%':>7} {'AETS':>8}")
    for (model_label, task_id) in sorted(groups):
        group = groups[(model_label, task_id)]
        n = len(group)
        ts_pct = 100 * sum(float(r['TS']) for r in group) / n
        tsr_pct = 100 * sum(float(r['TSR']) for r in group) / n
        aets_avg = sum(float(r['AETS']) for r in group) / n
        print(f"{model_label:<6} {task_id:<10} {n:>3} {ts_pct:>6.1f}% {tsr_pct:>6.1f}% {aets_avg:>8.4f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run-id', help='Annotate a specific run instead of the latest one.')
    parser.add_argument('--summary', action='store_true', help='Print TS%%/TSR%%/AETS grouped by model and task_id.')
    args = parser.parse_args()

    if args.summary:
        summary()
    else:
        annotate(args.run_id)


if __name__ == '__main__':
    main()
