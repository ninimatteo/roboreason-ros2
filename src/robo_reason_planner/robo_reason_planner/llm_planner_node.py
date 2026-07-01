"""
LLMPlannerNode — exposes /plan_task service for text-based planning.

Uses EmbodiedAgent with an LLM client, or a deterministic mock for dry-runs.
The scene_json field from the request is used as the environment description.

ROS2 parameters:
  use_mock_llm      (bool,  default true)  — return a hardcoded pick-and-place plan
  reasoning_method  (str,   default 'fhp') — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str,   default 'groq/llama4-scout-17b')
  temperature       (float, default 0.1)
"""

import json
import traceback

import dotenv
import rclpy
from rclpy.node import Node

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import PlanTask
from robo_reason_planner.agent_runner import run_plan_loop
from robo_reason_planner.command_grounding import check_command_grounding
from robo_reason_planner.debug_recorder import DebugRun
from robo_reason_reasoning.embodied_agent import EmbodiedAgent


class LLMPlannerNode(Node):

    def __init__(self):
        super().__init__('llm_planner_node')

        # Parameters are declared here but read per-request in the callback, so
        # the GUI can retune the planner live (ros2 param set / SetParameters)
        # without relaunching the node.
        self.declare_parameter('use_mock_llm', settings.USE_MOCK_LLM)
        self.declare_parameter('reasoning_method', settings.REASONING_METHOD)
        self.declare_parameter('model_name', settings.MODEL_NAME)
        self.declare_parameter('temperature', settings.TEMPERATURE)

        dotenv.load_dotenv()

        self._service = self.create_service(PlanTask, '/plan_task', self._plan_task_callback)

        use_mock = self.get_parameter('use_mock_llm').value
        label = (
            'MOCK' if use_mock
            else f"{self.get_parameter('reasoning_method').value}, "
                 f"{self.get_parameter('model_name').value}"
        )
        self.get_logger().info(f'[LLMPlannerNode] Ready — {label} (params read per request)')

    # ── /plan_task callback ────────────────────────────────────────────────────

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        scene_json = request.scene_json

        self.get_logger().info(f'[LLMPlannerNode] Received: "{user_command}"')

        use_mock = self.get_parameter('use_mock_llm').value
        run = DebugRun(mode='LLM-mock' if use_mock else 'LLM', command=user_command, config={
            'reasoning_method': self.get_parameter('reasoning_method').value,
            'model_name': self.get_parameter('model_name').value,
            'temperature': self.get_parameter('temperature').value,
            'scene_json': scene_json,
        })

        grounded, err = check_command_grounding(user_command, scene_json)
        if not grounded:
            response.success = False
            response.error_message = err
            run.finish(success=False, error=err)
            return response

        try:
            if use_mock:
                plan_data = self._mock_plan(user_command, scene_json)
                self.get_logger().info('[LLMPlannerNode] Generated mock plan.')
            else:
                plan_data = self._llm_plan(user_command, scene_json, run)
                self.get_logger().info('[LLMPlannerNode] Generated LLM plan.')

            response.success = True
            response.plan_json = json.dumps(plan_data)
            run.finish(success=True, response=plan_data)

        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[LLMPlannerNode] Planning error:\n{tb}')
            response.success = False
            response.error_message = tb
            run.finish(success=False, error=tb)

        return response

    # ── LLM plan ───────────────────────────────────────────────────────────────

    def _llm_plan(self, user_command: str, scene_json: str, run: DebugRun) -> dict:
        reasoning_method = self.get_parameter('reasoning_method').value
        model_name = self.get_parameter('model_name').value
        temperature = self.get_parameter('temperature').value

        agent = EmbodiedAgent(
            reasoning_mode=reasoning_method,
            client_parameters={
                'model_name': model_name,
                'temperature': temperature,
            },
            client_type='llm',
        )

        observation = {
            'user_request': user_command,
            'environment_map': scene_json,
        }
        self.get_logger().info(f'[LLMPlannerNode] Starting plan with\n{observation}')
        run.log(f'Starting plan with {observation}')

        plan_steps = run_plan_loop(agent, observation)

        self.get_logger().info(
            f'[LLMPlannerNode] Plan done — "{user_command}", '
            f'method: {reasoning_method}, model: {model_name}, '
            f'steps: {len(plan_steps)}'
        )
        run.log(
            f'Plan done — "{user_command}", method: {reasoning_method}, '
            f'model: {model_name}, steps: {len(plan_steps)}'
        )
        for s in plan_steps:
            self.get_logger().info(
                f'[LLMPlannerNode]   Step {s["step"]}: {s.get("action_name", "?")}'
            )
            run.log(f'  Step {s["step"]}: {s.get("action_name", "?")}')

        return {
            'task_summary': user_command,
            'reasoning_method': reasoning_method,
            'model': model_name,
            'plan': plan_steps,
        }

    # ── Mock plan ───────────────────────────────────────────────────────────────

    def _mock_plan(self, user_command: str, scene_json: str) -> dict:
        scene = json.loads(scene_json)
        objects = scene.get('objects', {})
        targets = scene.get('targets', {})

        first_obj = next(
            (obj for obj in objects.values() if obj.get('graspable', False)), None
        )
        first_target = next(iter(targets.values()), None)

        if not first_obj or not first_target:
            return {'task_summary': 'No objects/targets found', 'plan': []}

        obj_pos = first_obj['position']
        tgt_pos = first_target['position']
        obj_height = first_obj.get('size', [0, 0, 0.05])[2]

        plan = [
            {'step': 1, 'action_name': 'approach',
             'target_position': obj_pos, 'offset': 0.1, 'approach_direction': 'z'},
            {'step': 2, 'action_name': 'pick',
             'target_position': obj_pos, 'grasp_axis': 'z', 'come_back': True},
            {'step': 3, 'action_name': 'approach',
             'target_position': [tgt_pos[0], tgt_pos[1], tgt_pos[2] + 0.1],
             'offset': 0.0, 'approach_direction': 'z'},
            {'step': 4, 'action_name': 'release',
             'release_position': [tgt_pos[0], tgt_pos[1], tgt_pos[2] + obj_height / 2],
             'come_back': False},
            {'step': 5, 'action_name': 'move_home'},
        ]

        return {
            'task_summary': f'[MOCK] {user_command}',
            'reasoning_method': 'mock',
            'plan': plan,
        }


def main(args=None):
    rclpy.init(args=args)
    node = LLMPlannerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
