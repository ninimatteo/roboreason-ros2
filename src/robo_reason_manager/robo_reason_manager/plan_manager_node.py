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
import time

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import ExecutePlan, CancelExecution
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

        self._cancel_service = self.create_service(
            CancelExecution, '/cancel_execution',
            self._cancel_execution_callback,
            callback_group=self._cb_group
        )

        self._skill_client = ActionClient(
            self, ExecuteSkill, '/execute_skill',
            callback_group=self._cb_group
        )

        # Set to the ExecuteSkill goal handle currently in flight (cleared once
        # its result arrives), and checked by the emergency-stop cancel service
        # below — this is what lets /cancel_execution interrupt a skill that's
        # already mid-motion on the executor, not just goals not yet sent.
        self._active_goal_handle = None
        self._active_goal_lock = threading.Lock()
        # Set for the duration of a cancel request; the plan loop polls this
        # between steps so it stops sending further skills once a stop is
        # requested, even if the in-flight skill's cancel hasn't resolved yet.
        self._stop_requested = threading.Event()
        # Prevents two concurrent /execute_plan calls from racing on
        # _active_goal_handle and from the second call clearing _stop_requested
        # mid-cancel of the first.
        self._plan_lock = threading.Lock()

        self._world_state_pub = self.create_publisher(String, '/world_state', 10)
        self._log_pub = self.create_publisher(String, '/execution_log', 10)

        self.get_logger().info('[PlanManagerNode] Ready.')

    # -------------------------------------------------------------------------

    def _execute_plan_callback(self, request, response):
        if not self._plan_lock.acquire(blocking=False):
            response.success = False
            response.error_message = 'A plan is already executing.'
            return response
        try:
            return self._execute_plan_locked(request, response)
        finally:
            self._plan_lock.release()

    def _execute_plan_locked(self, request, response):
        self.get_logger().info('[PlanManagerNode] Received execute_plan request.')

        # Clear any leftover stop flag from a previous cancelled run so this
        # fresh plan isn't blocked before it even starts.
        self._stop_requested.clear()

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
            if self._stop_requested.is_set():
                self.get_logger().warn('[PlanManagerNode] Execution cancelled by user; stopping remaining steps.')
                response.success = False
                response.error_message = 'Execution cancelled by user'
                return response

            skill_name = step.get('action_name', '').lower()
            skill_args = extract_skill_args(step)
            step_idx = step.get('step', '?')

            log_prefix = f'[Step {step_idx}] {skill_name}'
            self.get_logger().info(f'[PlanManagerNode] {log_prefix} args={skill_args}')

            # Build and send goal
            goal = ExecuteSkill.Goal()
            goal.skill_name = skill_name
            goal.skill_args_json = json.dumps(skill_args)

            # Derive per-skill timeout so a wait(time=N) step doesn't false-fail.
            timeout_sec = 60.0
            if skill_name == 'wait':
                timeout_sec = float(skill_args.get('time', 0)) + 10.0

            result = self._send_skill_goal_sync(goal, timeout_sec=timeout_sec)

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
        """Send an ExecuteSkill goal and block until result is received.

        Tracks the accepted goal handle on self._active_goal_handle for the
        duration of the goal so /cancel_execution can reach in and cancel a
        skill that's already mid-motion, not just goals not yet sent.
        """
        done_event = threading.Event()
        result_holder = [None]

        def on_result(future):
            with self._active_goal_lock:
                self._active_goal_handle = None
            try:
                result_holder[0] = future.result().result
            except Exception as exc:
                class _Error:
                    success = False
                    error_message = f'result retrieval failed: {exc}'
                result_holder[0] = _Error()
            done_event.set()

        def on_goal_response(future):
            try:
                gh = future.result()
            except Exception as exc:
                class _Error:
                    success = False
                    error_message = f'goal send failed: {exc}'
                result_holder[0] = _Error()
                done_event.set()
                return
            if not gh.accepted:
                class _Rejected:
                    success = False
                    error_message = 'Goal rejected by executor'
                result_holder[0] = _Rejected()
                done_event.set()
            else:
                with self._active_goal_lock:
                    self._active_goal_handle = gh
                gh.get_result_async().add_done_callback(on_result)

        self._skill_client.send_goal_async(goal_msg).add_done_callback(on_goal_response)
        completed = done_event.wait(timeout=timeout_sec)

        if not completed:
            self.get_logger().error('[PlanManagerNode] Skill action timed out — cancelling goal.')
            with self._active_goal_lock:
                gh = self._active_goal_handle
                self._active_goal_handle = None
            if gh is not None:
                try:
                    gh.cancel_goal_async()
                except Exception:
                    pass
        return result_holder[0]

    # -------------------------------------------------------------------------

    def _cancel_execution_callback(self, request, response):
        """Emergency-stop: cancel the in-flight skill, stop the plan loop,
        and command the robot back to its home position."""
        self.get_logger().warn('[PlanManagerNode] Cancel execution requested.')

        # Stop the per-step loop in _execute_plan_callback from sending any
        # further skills, even if the in-flight one hasn't resolved yet.
        self._stop_requested.set()

        # Cancel whatever skill goal is currently in flight, if any.
        with self._active_goal_lock:
            active_gh = self._active_goal_handle

        if active_gh is not None:
            try:
                active_gh.cancel_goal_async()
            except Exception as e:
                self.get_logger().error(f'[PlanManagerNode] Failed to cancel active goal: {e}')

        # Give the executor a brief moment to actually stop moving before we
        # command it home, so the home goal doesn't queue up behind the
        # cancelled one on the same joint controller.
        time.sleep(0.3)

        # Command the robot back to its home position.
        home_goal = ExecuteSkill.Goal()
        home_goal.skill_name = 'move_home'
        home_goal.skill_args_json = json.dumps({})

        home_result = self._send_skill_goal_sync(home_goal, timeout_sec=15.0)

        if home_result is None or not home_result.success:
            err = home_result.error_message if home_result else 'Timeout or no result'
            self.get_logger().error(f'[PlanManagerNode] Return-home after cancel FAILED: {err}')
            response.success = False
            response.message = f'Cancelled, but return-home failed: {err}'
        else:
            self.get_logger().info('[PlanManagerNode] Cancel + return-home completed.')
            response.success = True
            response.message = 'Execution cancelled and robot returned home.'

        return response


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
