"""
task_interface_node — terminal interface.

Loads scene_mock.json, prompts user for a command, calls /plan_task then /execute_plan
and prints the plan, execution report and final world state.

Run separately from the services:
  ros2 run robo_reason_task_interface task_interface_node
"""

import rclpy
from rclpy.node import Node
from ament_index_python.packages import get_package_share_directory
import json
import threading
import time
import os

from robo_reason_interfaces.srv import PlanTask, ExecutePlan


class TaskInterfaceNode(Node):

    def __init__(self):
        super().__init__('task_interface_node')

        self._plan_client = self.create_client(PlanTask, '/plan_task')
        self._exec_client = self.create_client(ExecutePlan, '/execute_plan')

        pkg_share = get_package_share_directory('robo_reason_task_interface')
        scene_path = os.path.join(pkg_share, 'config', 'scene_mock.json')
        with open(scene_path, 'r') as f:
            self._scene_json = f.read()

        self.get_logger().info('[TaskInterfaceNode] Scene loaded. Waiting for services...')
        self._plan_client.wait_for_service()
        self._exec_client.wait_for_service()
        self.get_logger().info('[TaskInterfaceNode] Services ready.')

        # Start input thread last so self._scene_json is always set before first command.
        self._input_thread = threading.Thread(
            target=self._interactive_loop, daemon=True
        )
        self._input_thread.start()

    def _interactive_loop(self):
        while True:
            print('\n' + '=' * 60)
            print('RoboReason ROS2 — Task Interface')
            print('Type your command or "quit" to exit.')
            print('=' * 60)
            try:
                user_command = input('> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\nExiting.')
                break

            if user_command.lower() in ('quit', 'exit', 'q'):
                print('Exiting.')
                rclpy.shutdown()
                return
            if not user_command:
                continue

            # 1. Generate plan
            print(f'\n[Planning] "{user_command}" ...')
            plan_req = PlanTask.Request()
            plan_req.user_command = user_command
            plan_req.scene_json = self._scene_json

            plan_future = self._plan_client.call_async(plan_req)
            deadline = time.time() + 120.0
            while not plan_future.done() and time.time() < deadline:
                time.sleep(0.1)
            if not plan_future.done():
                print('[ERROR] Planning timed out after 120 s')
                continue
            try:
                plan_resp = plan_future.result()
            except Exception as exc:
                print(f'[ERROR] Planning call failed: {exc}')
                continue

            if not plan_resp.success:
                print(f'[ERROR] Planning failed: {plan_resp.error_message}')
                continue

            plan_data = json.loads(plan_resp.plan_json)
            print(f'\n[Plan] {plan_data.get("task_summary", "")}')
            for step in plan_data.get('plan', []):
                idx = step.get('step', '?')
                skill = step.get('action_name', '?')
                args = {k: v for k, v in step.items() if k not in ('step', 'action_name')}
                print(f'  Step {idx}: {skill}({args})')

            # 2. Execute plan
            print('\n[Executing]...')
            exec_req = ExecutePlan.Request()
            exec_req.plan_json = plan_resp.plan_json
            exec_req.scene_json = self._scene_json

            exec_future = self._exec_client.call_async(exec_req)
            deadline = time.time() + 300.0
            while not exec_future.done() and time.time() < deadline:
                time.sleep(0.1)
            if not exec_future.done():
                print('[ERROR] Execution timed out after 300 s')
                continue
            try:
                exec_resp = exec_future.result()
            except Exception as exc:
                print(f'[ERROR] Execution call failed: {exc}')
                continue

            if exec_resp.success:
                print(f'\n[Report]\n{exec_resp.report}')
                final_state = json.loads(exec_resp.final_state_json)
                holding = final_state.get('robot', {}).get('holding')
                print(f'\n[Final State] Robot holding: {holding}')
                for obj_id, obj in final_state.get('objects', {}).items():
                    print(f'  {obj_id}: state={obj.get("state")}, pos={obj.get("position")}')
            else:
                print(f'\n[ERROR] Execution failed: {exec_resp.error_message}')


def main(args=None):
    rclpy.init(args=args)
    node = TaskInterfaceNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
