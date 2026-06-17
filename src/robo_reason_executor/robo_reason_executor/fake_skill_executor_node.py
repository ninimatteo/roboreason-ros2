"""
fake_skill_executor_node — dry-run skill executor.

Exposes the /execute_skill action server. Does NOT control a real robot.
Simulates skill execution with progress feedback and deterministic success.
Replace with ur5_skill_executor_node for real robot operation.
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
import json
import time

from robo_reason_interfaces.action import ExecuteSkill


class FakeSkillExecutorNode(Node):

    def __init__(self):
        super().__init__('fake_skill_executor_node')

        self._cb_group = ReentrantCallbackGroup()

        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            '/execute_skill',
            self._execute_skill_callback,
            callback_group=self._cb_group,
        )

        self.get_logger().info('[FakeSkillExecutorNode] Ready — dry-run mode.')

    def _execute_skill_callback(self, goal_handle):
        skill_name = goal_handle.request.skill_name
        skill_args = json.loads(goal_handle.request.skill_args_json)

        self.get_logger().info(f'[FAKE {skill_name.upper()}] args={skill_args}')

        feedback = ExecuteSkill.Feedback()
        result = ExecuteSkill.Result()

        # Progress 0.30
        feedback.status = f'Executing {skill_name}...'
        feedback.progress = 0.30
        goal_handle.publish_feedback(feedback)
        time.sleep(0.3)

        # Progress 0.70
        feedback.progress = 0.70
        goal_handle.publish_feedback(feedback)
        time.sleep(0.3)

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
