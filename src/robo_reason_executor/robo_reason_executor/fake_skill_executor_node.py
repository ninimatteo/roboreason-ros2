"""
fake_skill_executor_node — dry-run skill executor.

Exposes the /execute_skill action server. Does NOT control a real robot.
Simulates skill execution with progress feedback and deterministic success.
Replace with ur5_skill_executor_node for real robot operation.
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
import json
import time

from robo_reason_interfaces.action import ExecuteSkill
from robo_reason_manager.schemas import ALLOWED_SKILLS, SKILL_REQUIRED_ARGS


class FakeSkillExecutorNode(Node):

    def __init__(self):
        super().__init__('fake_skill_executor_node')

        self._cb_group = ReentrantCallbackGroup()

        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            '/execute_skill',
            self._execute_skill_callback,
            cancel_callback=self._handle_cancel_request,
            callback_group=self._cb_group,
        )

        self.get_logger().info('[FakeSkillExecutorNode] Ready — dry-run mode.')

    def _handle_cancel_request(self, goal_handle):
        self.get_logger().warn('[FakeSkillExecutorNode] Cancel requested for in-progress (fake) skill.')
        return CancelResponse.ACCEPT

    def _execute_skill_callback(self, goal_handle):
        skill_name = goal_handle.request.skill_name.lower()
        result = ExecuteSkill.Result()

        try:
            skill_args = json.loads(goal_handle.request.skill_args_json)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'[FakeSkillExecutorNode] Malformed skill_args_json: {exc}')
            goal_handle.abort()
            result.success = False
            result.error_message = f'malformed skill_args_json: {exc}'
            return result

        if skill_name not in ALLOWED_SKILLS:
            self.get_logger().error(f'[FakeSkillExecutorNode] Unknown skill: {skill_name}')
            goal_handle.abort()
            result.success = False
            result.error_message = f'unknown skill: {skill_name}'
            return result

        for arg in SKILL_REQUIRED_ARGS.get(skill_name, []):
            if arg not in skill_args:
                self.get_logger().error(
                    f'[FakeSkillExecutorNode] Missing required arg for {skill_name}: {arg}'
                )
                goal_handle.abort()
                result.success = False
                result.error_message = f'missing required arg for {skill_name}: {arg}'
                return result

        self.get_logger().info(f'[FAKE {skill_name.upper()}] args={skill_args}')

        feedback = ExecuteSkill.Feedback()

        # Progress 0.30 — simulated motion, split into short sleeps so an
        # emergency-stop cancel is noticed within ~0.1s instead of only after
        # the whole (fake) skill finishes.
        feedback.status = f'Executing {skill_name}...'
        feedback.progress = 0.30
        goal_handle.publish_feedback(feedback)
        for _ in range(3):
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result.success = False
                result.error_message = 'cancelled: fake motion cancelled by user'
                return result
            time.sleep(0.1)

        # Progress 0.70
        feedback.progress = 0.70
        goal_handle.publish_feedback(feedback)
        for _ in range(3):
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result.success = False
                result.error_message = 'cancelled: fake motion cancelled by user'
                return result
            time.sleep(0.1)

        # Progress 1.00
        feedback.status = 'Done'
        feedback.progress = 1.00
        goal_handle.publish_feedback(feedback)

        goal_handle.succeed()
        result.success = True
        result.result_json = json.dumps({'skill': skill_name, 'status': 'fake_ok'})
        return result


def main(args=None):
    rclpy.init(args=args)
    node = FakeSkillExecutorNode()
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
