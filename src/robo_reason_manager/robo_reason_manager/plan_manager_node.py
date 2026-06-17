"""
plan_manager_node — exposes /execute_plan service.

Validates the plan, sends each skill to /execute_skill action server,
updates the virtual world state, and publishes /world_state and /execution_log topics.

Uses MultiThreadedExecutor + ReentrantCallbackGroup to avoid deadlock
between the service callback and the action client.
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import String
import json
import threading

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import ExecutePlan
from robo_reason_interfaces.action import ExecuteSkill
from robo_reason_manager.world_state import WorldState
from robo_reason_manager.plan_validator import PlanValidator
from robo_reason_manager.schemas import extract_skill_args, normalize_plan


class PlanManagerNode(Node):

    def __init__(self):
        super().__init__('plan_manager_node')

        self.declare_parameter('mode', 'LLM')   # not in settings — set per-launch
        self._mode = self.get_parameter('mode').value.upper()

        self._cb_group = ReentrantCallbackGroup()

        self._service = self.create_service(
            ExecutePlan, '/execute_plan',
            self._execute_plan_callback,
            callback_group=self._cb_group
        )

        self._skill_client = ActionClient(
            self, ExecuteSkill, '/execute_skill',
            callback_group=self._cb_group
        )

        self._world_state_pub = self.create_publisher(String, '/world_state', 10)
        self._log_pub = self.create_publisher(String, '/execution_log', 10)

        self.get_logger().info('[PlanManagerNode] Ready.')

    # -------------------------------------------------------------------------

    def _execute_plan_callback(self, request, response):
        self.get_logger().info('[PlanManagerNode] Received execute_plan request.')

        # Parse inputs
        try:
            plan_data = json.loads(request.plan_json)
            plan = normalize_plan(plan_data.get('plan', []))
        except Exception as e:
            response.success = False
            response.error_message = f'Invalid plan_json: {e}'
            return response

        try:
            world_state = WorldState(request.scene_json)
        except Exception as e:
            response.success = False
            response.error_message = f'Invalid scene_json: {e}'
            return response

        # Validate
        validator = PlanValidator()
        valid, val_err = validator.validate(plan, world_state.copy(), mode=self._mode)
        if not valid:
            self.get_logger().warn(f'[PlanManagerNode] Plan validation failed: {val_err}')
            response.success = False
            response.error_message = f'Validation error: {val_err}'
            return response

        self.get_logger().info(f'[PlanManagerNode] Plan valid ({len(plan)} steps). Executing...')

        # Wait for skill executor
        if not self._skill_client.wait_for_server(timeout_sec=10.0):
            response.success = False
            response.error_message = 'Skill executor action server not available.'
            return response

        report_lines = []

        for step in plan:
            skill_name = step.get('action_name', '').lower()
            skill_args = extract_skill_args(step)
            step_idx = step.get('step', '?')

            log_prefix = f'[Step {step_idx}] {skill_name}'
            self.get_logger().info(f'[PlanManagerNode] {log_prefix} args={skill_args}')

            # Build and send goal
            goal = ExecuteSkill.Goal()
            goal.skill_name = skill_name
            goal.skill_args_json = json.dumps(skill_args)

            result = self._send_skill_goal_sync(goal)

            if result is None or not result.success:
                err = result.error_message if result else 'Timeout or no result'
                self.get_logger().error(f'[PlanManagerNode] {log_prefix} FAILED: {err}')
                response.success = False
                response.error_message = f'{log_prefix} failed: {err}'
                return response

            # Update virtual state
            world_state.apply_skill_result(skill_name, skill_args)

            # Publish state and log
            state_msg = String()
            state_msg.data = world_state.to_json()
            self._world_state_pub.publish(state_msg)

            log_line = f'{log_prefix} -> OK'
            log_msg = String()
            log_msg.data = log_line
            self._log_pub.publish(log_msg)
            report_lines.append(log_line)

        response.success = True
        response.final_state_json = world_state.to_json()
        response.report = '\n'.join(report_lines)
        self.get_logger().info('[PlanManagerNode] Plan executed successfully.')
        return response

    # -------------------------------------------------------------------------

    def _send_skill_goal_sync(self, goal_msg, timeout_sec: float = 60.0):
        """Send an ExecuteSkill goal and block until result is received."""
        done_event = threading.Event()
        result_holder = [None]

        def on_result(future):
            result_holder[0] = future.result().result
            done_event.set()

        def on_goal_response(future):
            gh = future.result()
            if not gh.accepted:
                class _Rejected:
                    success = False
                    error_message = 'Goal rejected by executor'
                result_holder[0] = _Rejected()
                done_event.set()
            else:
                gh.get_result_async().add_done_callback(on_result)

        self._skill_client.send_goal_async(goal_msg).add_done_callback(on_goal_response)
        completed = done_event.wait(timeout=timeout_sec)

        if not completed:
            self.get_logger().error('[PlanManagerNode] Skill action timed out.')
        return result_holder[0]


def main(args=None):
    rclpy.init(args=args)
    node = PlanManagerNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
