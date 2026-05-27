"""
llm_planner_node — exposes /plan_task service.

Translates a user command + scene JSON into a discrete skill plan using EmbodiedAgent.

ROS2 parameters:
  use_mock_llm      (bool, default true)   — deterministic plan without API calls
  reasoning_method  (str, default 'fhp')   — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str)                  — e.g. groq/llama4-scout-17b
  temperature       (float, default 0.1)
"""

import rclpy
from rclpy.node import Node
import json

from robo_reason_interfaces.srv import PlanTask
from robo_reason_planner.command_grounding import check_command_grounding
from robo_reason_reasoning.embodied_agent import EmbodiedAgent


class LLMPlannerNode(Node):

    def __init__(self):
        super().__init__('llm_planner_node')

        self.declare_parameter('use_mock_llm', True)
        self.declare_parameter('reasoning_method', 'fhp')
        self.declare_parameter('model_name', 'groq/llama4-scout-17b')
        self.declare_parameter('temperature', 0.1)

        self._use_mock = self.get_parameter('use_mock_llm').value
        self._reasoning_method = self.get_parameter('reasoning_method').value
        self._model_name = self.get_parameter('model_name').value
        self._temperature = self.get_parameter('temperature').value

        self._service = self.create_service(PlanTask, '/plan_task', self._plan_task_callback)

        mode = 'MOCK' if self._use_mock else f'LLM ({self._reasoning_method}, {self._model_name})'
        self.get_logger().info(f'[LLMPlanner] Ready — mode: {mode}')

    # -------------------------------------------------------------------------

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        scene_json = request.scene_json

        self.get_logger().info(f'[LLMPlanner] Received: "{user_command}"')

        # Command grounding check
        grounded, err = check_command_grounding(user_command, scene_json)
        if not grounded:
            response.success = False
            response.error_message = err
            return response

        try:
            if self._use_mock:
                plan_data = self._mock_plan(user_command, scene_json)
                self.get_logger().info('[LLMPlanner] Generated mock plan.')
            else:
                plan_data = self._llm_plan(user_command, scene_json)
                self.get_logger().info('[LLMPlanner] Generated Groq LLM plan.')

            response.success = True
            response.plan_json = json.dumps(plan_data)

        except Exception as e:
            self.get_logger().error(f'[LLMPlanner] Planning error: {e}')
            response.success = False
            response.error_message = str(e)

        return response

    # -------------------------------------------------------------------------

    def _llm_plan(self, user_command: str, scene_json: str) -> dict:
        """Use EmbodiedAgent to generate a plan."""
        llm_params = {
            'model_name': self._model_name,
            'temperature': self._temperature,
        }

        agent = EmbodiedAgent(
            reasoning_mode=self._reasoning_method,
            llm_parameters=llm_params,
        )

        observation = {
            'user_request': user_command,
            'environment_map': scene_json,
        }

        plan_steps = []
        max_steps = 25

        for step_idx in range(max_steps):
            result = agent.step(observation=observation)
            action = result.action
            eos = result.end_of_simulation

            action_name = action.action_name.lower()

            # Convert to dict, exclude None values
            try:
                action_dict = action.model_dump(exclude_none=True)
            except AttributeError:
                action_dict = action.dict(exclude_none=True)

            # Skip internal idle/eos markers in the output plan
            if action_name not in ('idle', 'end_of_simulation'):
                action_dict['step'] = step_idx + 1
                plan_steps.append(action_dict)

            if eos:
                break

        return {
            'task_summary': user_command,
            'reasoning_method': self._reasoning_method,
            'model': self._model_name,
            'plan': plan_steps,
        }

    def _mock_plan(self, user_command: str, scene_json: str) -> dict:
        """Generate a deterministic pick-and-place plan from the first object in the scene."""
        scene = json.loads(scene_json)
        objects = scene.get('objects', {})
        targets = scene.get('targets', {})

        # Pick first graspable object
        first_obj = next(
            (obj for obj in objects.values() if obj.get('graspable', False)),
            None
        )
        # Place in first target
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
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
