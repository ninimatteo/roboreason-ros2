"""
EmbodiedAgent — adapted for UR5 real robot from RoboReason-Lab.

Changes from original:
- Skill set: approach/pick/release/move_home/wait (replaces Navigate/Pick/Place/…)
- Action type: UR5Action (replaces Action/SymbolicAction)
- No symbolic mode (always coordinate-based for real robot)
- Import paths use robo_reason_real.reasoning.*
- Supports both LLM and VLM foundation clients.
"""
from numpy import test
from inspect import trace
from collections import namedtuple
import os
import traceback
import dotenv


from robo_reason_reasoning.fhp_ffhp import FHP
from robo_reason_reasoning.react import React
from robo_reason_reasoning.cot_sc import CoTSC
from robo_reason_reasoning.always_act import StepAction
from robo_reason_reasoning.self_refine import SelfRefine
from robo_reason_reasoning.tot import TreeOfThought
from robo_reason_reasoning.predicates import Predicates
from robo_reason_reasoning.skills import UR5Skills
from robo_reason_reasoning.extraction_classes import UR5Action

dotenv.load_dotenv()

class EmbodiedAgent:
    """
    Orchestrates Foundation Model-based reasoning for robotic manipulation tasks.

    Supported reasoning modes:
      fhp, ffhp, react, cot_sc, tot, always_act, self_refine
    """

    def __init__(self, reasoning_mode: str, client_parameters: dict, client_type: str = 'llm',
                 verbose: bool = False, **kwargs):

        self.verbose = verbose
        self.reasoning_mode = reasoning_mode.lower()
        self.instance_name = kwargs.get("instance_name", "EmbodiedAgent")
        self.client_type = client_type.lower()

        self.predicates = Predicates.get_all_predicates()
        self.skills, self.action_placeholder = UR5Skills.get_embodiment_data(
            use_vlm=(self.client_type == 'vlm')
        )
        self.eos_placeholder = UR5Skills.get_eos_example()

        self.client_parameters = client_parameters
        assert 'model_name' in client_parameters, "client_parameters must include 'model_name'."

        self.step_counter = 0
        self._output = namedtuple('AgentOutput', ['action', 'end_of_simulation'])

        mode = self.reasoning_mode
        common = dict(
            client_parameters=client_parameters,
            client_type=client_type,
            skills=self.skills,
            action_placeholder=self.action_placeholder,
            verbose=verbose,
        )

        if mode in ('finite_horizon_planning', 'fhp'):
            self.reasoning_method = FHP(reasoning_mode='fhp', predicates=self.predicates, **common)

        elif mode in ('feasible_finite_horizon_planning', 'ffhp'):
            self.reasoning_method = FHP(reasoning_mode='ffhp', predicates=self.predicates, **common)

        elif mode in ('react', 'ra'):
            self.reasoning_method = React(predicates=self.predicates, **common)

        elif mode in ('cot_sc', 'cot-sc', 'csc'):
            self.reasoning_method = CoTSC(k=5, **common)

        elif mode in ('treeofthoughts', 'tot'):
            self.reasoning_method = TreeOfThought(
                eos_placeholder=self.eos_placeholder,
                predicates=self.predicates,
                use_iid_evaluation=True,
                b=2, k=3, t=20,
                **common,
            )

        elif mode in ('always_act', 'always-act', 'aa'):
            self.reasoning_method = StepAction(**common)

        elif mode in ('self_refine', 'self-refine', 'sr'):
            self.reasoning_method = SelfRefine(
                predicates=self.predicates,
                max_iterations=3,
                **common,
            )

        else:
            raise ValueError(
                f"Unknown reasoning mode: '{reasoning_mode}'. "
                "Supported: fhp, ffhp, react, cot_sc, tot, always_act, self_refine"
            )

    # -------------------------------------------------------------------------

    def get_used_tokens(self) -> int:
        if hasattr(self.reasoning_method.client, 'get_total_used_tokens'):
            return self.reasoning_method.client.get_total_used_tokens()
        return 0

    def get_detailed_token_usage(self):
        return self.reasoning_method.get_llm_usage_metrics()

    def get_current_step(self) -> int:
        return self.reasoning_method.step_counter

    def get_current_plan(self):
        if hasattr(self.reasoning_method, 'task_plan'):
            return self.reasoning_method.task_plan
        return None

    def step(self, observation: dict, force_replanning: bool = False, **kwargs):
        """
        Perform one reasoning step.

        observation must contain:
          - 'user_request': str
          - 'environment_map': str (scene JSON or description) OR 'image': local image path (if using VLM)

        Returns a namedtuple(action: UR5Action, end_of_simulation: bool).
        """
        assert 'user_request' in observation, "Observation must include 'user_request'."
        if self.client_type != 'vlm':
            assert 'environment_map' in observation, "Observation must include 'environment_map' when not using a VLM."
        else:
            assert 'image' in observation or 'environment_map' in observation, "Observation must include 'image' or 'environment_map' when using a VLM."

        print(f"\n({self.instance_name}) Thinking...\n")

        action, end_of_simulation = self.reasoning_method(
            **observation,
            verbose=self.verbose,
            force_replanning=force_replanning,
        )

        assert isinstance(action, UR5Action), \
            f"Expected UR5Action, got {type(action)}."

        return self._output(action=action, end_of_simulation=end_of_simulation)


if __name__ == "__main__":
    import os
    import json
    
    test_dir = os.path.join(os.path.dirname(__file__), 'test')
    user_request = "Pick up one object base on your preference and place it at the center of the table"
    
    # ---------------------------------------------------------
    # LLM Test
    # ---------------------------------------------------------
    print("Testing LLM EmbodiedAgent Execution")
    llm_params = {
        'model_name': 'groq/llama4-scout-17b',
        'temperature': 0.0,

    }
    llm_agent = EmbodiedAgent(
        reasoning_mode='fhp',
        client_parameters=llm_params,
        client_type='llm',
        verbose=True
    )
    print("LLM Agent created successfully.")
    
    text_path = os.path.join(test_dir, 'scene_mock.json')
    if os.path.exists(text_path):
        with open(text_path, 'r') as f:
            env_map = json.dumps(json.load(f))
            
        observation_llm = {
            'user_request': user_request,
            'environment_map': env_map
        }
        
        print(f"\n--- Running LLM Agent Step ---")
        try:
            output = llm_agent.step(observation_llm)
            print(f"LLM output action: \n{output.action}")
            print(f"LLM full plan generated: \n{llm_agent.get_current_plan()}")
        except Exception as e:
            print(f"LLM execution failed: {traceback.print_exc()}")
    else:
        print(f"\nSkipping LLM execution: '{text_path}' not found. Please add a valid test_env.json inside test/.")

    # ---------------------------------------------------------
    # VLM Test
    # ---------------------------------------------------------
    for test_image in ['vlm_test_ego.jpg', 'vlm_test_ext.jpg']:
        print("\nTesting VLM EmbodiedAgent Execution on {}.".format(test_image))
        vlm_params = {
            'model_name': 'nebius/qwen3-2.5-70b',
            'temperature': 0.0,
        }
        vlm_agent = EmbodiedAgent(
            reasoning_mode='fhp',
            client_parameters=vlm_params,
            client_type='vlm',
            verbose=True
        )
        print("VLM Agent created successfully.")
        
        image_path = os.path.join(test_dir, test_image)
        if os.path.exists(image_path):
            observation_vlm = {
                'user_request': user_request,
                'image': image_path
            }
            
            print(f"\n--- Running VLM Agent Step ---")
            try:
                output = vlm_agent.step(observation_vlm)
                print(f"VLM output action: \n{output.action}")
                print(f"VLM full plan generated: \n{vlm_agent.get_current_plan()}")
            except Exception as e:
                print(f"VLM execution failed: {traceback.print_exc()}")
        else:
            print(f"\nSkipping VLM execution: '{image_path}' not found. Please add a valid test_image.jpg inside test/.")
