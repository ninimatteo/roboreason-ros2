"""Run one task on the sim AGENT stack: reset, plan, execute, report ground truth."""
import json, sys, time, threading
import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage
from robo_reason_interfaces.srv import PlanTask, ExecutePlan

SCENE = '/root/ws/ws_ouroboros/src/roboreason-ros2/src/robo_reason_task_interface/config/scene_mock.json'
command = sys.argv[1]
import argparse, subprocess
ap = argparse.ArgumentParser(); ap.add_argument('command'); ap.add_argument('--no-reset', action='store_true')
ap.add_argument('--slip', type=int, default=0, help='the next N grasps slip')
ap.add_argument('--drop', type=float, default=0.0, help='the next grasp drops after S seconds')
ap.add_argument('--move', default='', help='cube:x,y  teleport this cube right after the first successful pick')
a = ap.parse_args(); command = a.command; reset = not a.no_reset
def set_param(name, value):
    subprocess.run(['ros2', 'param', 'set', '/sim/gripper_shim_node', name, str(value)], capture_output=True)
def teleport(cube, x, y):
    req = f'name: "{cube}", position: {{x: {x}, y: {y}, z: 0.0}}, orientation: {{w: 1}}'
    r = subprocess.run(['ign', 'service', '-s', '/world/roboreason/set_pose', '--reqtype', 'ignition.msgs.Pose',
                        '--reptype', 'ignition.msgs.Boolean', '--timeout', '3000', '--req', req], capture_output=True, text=True)
    print(f'   PERTURB moved {cube} to ({x},{y}):', r.stdout.strip(), flush=True)
moved = []
def on_log(m):
    print('   LOG', m.data, flush=True)
    if a.move and not moved and m.data.startswith('[Agent] pick') and m.data.endswith('-> done'):
        moved.append(1); cube, xy = a.move.split(':'); x, y = map(float, xy.split(','))
        threading.Thread(target=teleport, args=(cube, x, y), daemon=True).start()
rclpy.init(); n = Node('run_task', namespace='sim')
ex = MultiThreadedExecutor(); ex.add_node(n); threading.Thread(target=ex.spin, daemon=True).start()
n.create_subscription(String, 'execution_log', on_log, 50)
poses = {}
def on_pose(m):
    for tf in m.transforms:
        if tf.child_frame_id.endswith('cube'):
            t = tf.transform.translation; poses[tf.child_frame_id] = (t.x, t.y, t.z)
n.create_subscription(TFMessage, 'ground_truth/poses', on_pose, 10)

def call(cli, req, timeout):
    assert cli.wait_for_service(timeout_sec=10), cli.srv_name
    f = cli.call_async(req); t = time.time()
    while not f.done():
        if time.time() - t > timeout: raise TimeoutError(cli.srv_name)
        time.sleep(0.05)
    return f.result()

if reset:
    r = call(n.create_client(Trigger, 'reset_simulation'), Trigger.Request(), 60); print('reset:', r.success, r.message)
    time.sleep(2)
set_param('fail_next_grasps', a.slip); set_param('drop_next_grasp_after_s', float(a.drop))
scene = open(SCENE).read()
t0 = time.time()
p = call(n.create_client(PlanTask, 'plan_task'), PlanTask.Request(user_command=command, scene_json=scene), 300)
print(f'PLAN ({time.time()-t0:.1f}s) success={p.success}', p.error_message[-500:] if not p.success else '')
if not p.success: sys.exit(1)
plan = json.loads(p.plan_json)
for s in plan['plan']: print('  ', s['step'], s['action_name'], '-', s['description'])
t1 = time.time()
e = call(n.create_client(ExecutePlan, 'execute_plan'), ExecutePlan.Request(plan_json=p.plan_json, scene_json=scene), 1000)
print(f'EXEC ({time.time()-t1:.1f}s) success={e.success} {e.error_message[-300:]}')
print(e.report)
time.sleep(1.5)
for k, v in sorted(poses.items()): print(f'GT {k:12s} ({v[0]:+.3f},{v[1]:+.3f},{v[2]:+.3f})')
print('RESULT_JSON', json.dumps({'command': command, 'faults': {'slip': a.slip, 'drop': a.drop, 'move': a.move}, 'agent_success': e.success, 'plan_s': round(t1-t0,1), 'exec_s': round(time.time()-t1,1), 'report_tail': e.report.splitlines()[-2:] if e.report else [], 'poses': poses}), flush=True)
import os; os._exit(0)
