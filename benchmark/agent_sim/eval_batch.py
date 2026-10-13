"""Run benchmark tasks (plus fault variants) on the sim AGENT stack and grade them on ground truth.

usage: python3 eval_batch.py MODEL OUT_CSV [scenario ids...]
"""
import csv, json, math, subprocess, sys, time
from pathlib import Path

TASKS = {
    'pp_easy': "Pick the red cube and place it on the brown tray.",
    'pp_hard': "Put all the cubes on the brown tray.",
    'sort_easy': "Stack the red cube on top of the blue cube.",
    'sort_hard': "Sort all the cubes: put the red cube onto the brown tray, and arrange the rest in a straight line on the table.",
    'arith_easy': "Each cube has a value: blue = 1, red = 2, white = 3, orange = 4. Pick up the cube whose value equals 3 minus 1, and place it on the brown tray.",
    'arith_hard': "Each cube has a value: blue = 1, red = 2, white = 3, orange = 4. Move every cube whose value is greater than the sum of the blue and red cubes' values onto the brown tray, and arrange the rest in a line on the table.",
}
SCENARIOS = {  # id: (task, extra args)
    'pp_easy': ('pp_easy', []), 'pp_hard': ('pp_hard', []), 'sort_easy': ('sort_easy', []),
    'sort_hard': ('sort_hard', []), 'arith_easy': ('arith_easy', []), 'arith_hard': ('arith_hard', []),
    'pp_easy+slip': ('pp_easy', ['--slip', '1']),
    'pp_easy+drop': ('pp_easy', ['--drop', '2.0']),
    'pp_hard+move': ('pp_hard', ['--move', 'white_cube:0.15,-0.65']),
    'sort_easy+slip': ('sort_easy', ['--slip', '1']),
}
CUBES = ['red_cube', 'blue_cube', 'white_cube', 'orange_cube']
COMMIT = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True,
                        cwd=Path(__file__).parent).stdout.strip()

def on_tray(p): return 0.24 <= p[0] <= 0.46 and -0.43 <= p[1] <= -0.12 and p[2] > 0.03
def on_table(p): return p[2] < 0.01 and not on_tray(p) and -0.6 <= p[0] <= 0.6 and -0.8 <= p[1] <= 0.0

def line_ok(pts):
    """3+ points roughly collinear (<= 3 cm off the best line) and spread (>= 6 cm apart)."""
    if len(pts) < 2: return True
    if min(math.dist(a[:2], b[:2]) for i, a in enumerate(pts) for b in pts[i+1:]) < 0.06: return False
    if len(pts) == 2: return True
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    sxx = sum((x-mx)**2 for x in xs); syy = sum((y-my)**2 for y in ys); sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    ang = 0.5 * math.atan2(2*sxy, sxx - syy); dx, dy = math.cos(ang), math.sin(ang)
    return max(abs(-(x-mx)*dy + (y-my)*dx) for x, y in zip(xs, ys)) <= 0.03

def grade(task, P):
    if task in ('pp_easy', 'arith_easy'):
        subs = [on_tray(P['red_cube'])]
    elif task == 'pp_hard':
        subs = [on_tray(P[c]) for c in CUBES]
    elif task == 'sort_easy':
        r, b = P['red_cube'], P['blue_cube']
        subs = [math.dist(r[:2], b[:2]) < 0.025 and 0.035 <= r[2] - b[2] <= 0.06]
    elif task == 'sort_hard':
        rest = ['blue_cube', 'white_cube', 'orange_cube']
        line = line_ok([P[c] for c in rest])
        subs = [on_tray(P['red_cube'])] + [on_table(P[c]) and line for c in rest]
    elif task == 'arith_hard':
        rest = ['blue_cube', 'red_cube', 'white_cube']
        line = line_ok([P[c] for c in rest])
        subs = [on_tray(P['orange_cube'])] + [on_table(P[c]) and line for c in rest]
    return sum(subs), len(subs)

def regrade(log_dir: Path):
    """Grade every saved run log again with the current rules (prints a table)."""
    for log in sorted(log_dir.glob('*.log')):
        line = next((l for l in log.read_text().splitlines() if l.startswith('RESULT_JSON')), None)
        sid = log.stem.split('_', 3)[-1]
        if line is None:
            print(f'{log.stem:55s} no result'); continue
        r = json.loads(line[len('RESULT_JSON '):])
        done, req = grade(SCENARIOS[sid][0], r['poses'])
        print(f'{log.stem:55s} {done}/{req} agent={r["agent_success"]}')


def main():
    if sys.argv[1] == '--regrade':
        return regrade(Path(sys.argv[2]))
    model, out = sys.argv[1], Path(sys.argv[2]); ids = sys.argv[3:] or list(SCENARIOS)
    subprocess.run(['docker', 'exec', 'roboreason_ur_gui', 'bash', '-c',
                    f'source /tmp/ouroenv.sh; ros2 param set /sim/agent_node model_name {model}'], capture_output=True)
    new = not out.exists()
    for sid in ids:
        task, extra = SCENARIOS[sid]
        t0 = time.time()
        cmd = ['docker', 'exec', 'roboreason_ur_gui', 'bash', '-c',
               'source /tmp/ouroenv.sh; cd /tmp/ouro; timeout 1100 python3 /tmp/run_task.py '
               + ' '.join(subprocess.list2cmdline([x]) for x in [TASKS[task]] + extra)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        log = res.stdout + res.stderr
        Path(out.parent / 'logs').mkdir(exist_ok=True)
        (out.parent / 'logs' / f'{time.strftime("%H%M%S")}_{model.replace("/", "_")}_{sid}.log').write_text(log)
        line = next((l for l in log.splitlines() if l.startswith('RESULT_JSON')), None)
        row = {'time': time.strftime('%Y-%m-%d %H:%M'), 'commit': COMMIT, 'model': model,
               'scenario': sid, 'task': task}
        if line is None:
            err = next((l for l in log.splitlines() if 'success=False' in l or 'Error' in l), log.strip().splitlines()[-1:] or [''])
            row.update(agent_success='', subtasks='0', required='', ok=False, plan_s='', exec_s='',
                       model_calls='', note=(err if isinstance(err, str) else err[0])[:200])
        else:
            r = json.loads(line[len('RESULT_JSON '):])
            done, req = grade(task, r['poses'])
            tail = r['report_tail']
            calls = tail[-1].split(' model calls')[0] if tail else ''
            row.update(agent_success=r['agent_success'], subtasks=done, required=req, ok=done == req,
                       plan_s=r['plan_s'], exec_s=r['exec_s'], model_calls=calls, note=(tail[0] if tail else '')[:200])
        with out.open('a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(row)); 
            if new: w.writeheader(); new = False
            w.writerow(row)
        print(f"{sid:16s} ok={row['ok']} {row['subtasks']}/{row['required']} agent={row['agent_success']} "
              f"exec={row['exec_s']}s calls={row['model_calls']} wall={time.time()-t0:.0f}s | {row['note'][:90]}", flush=True)

if __name__ == '__main__':
    main()
