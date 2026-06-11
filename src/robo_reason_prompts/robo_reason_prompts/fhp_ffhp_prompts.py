"""FHP / FFHP prompt templates — adapted for UR5 from RoboReason-Lab."""

class FHP_FFHP_Prompts:
    @staticmethod
    def get_prompts(use_vlm: bool = False):
        fhp_ffhp_system_message = (
            "You are an embodied agent capable of spatial reasoning and planning actions in an "
            "environment. You must determine whether each action in your plan is feasible based "
            "on the environment's current state."
        )

        task_planning_prompt = """
Your task is to analyze the current state of the environment and the user's request to generate a feasible action plan that is a list of actions in a JSON format.

**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Current Environment Configuration**: \n{current_env_config}
**Skills Library** A list of available skills and actions you can use: \n{skills}

**Policy for Action Selection**
Select actions strictly from the skills library.
Use object names and positions from the environment description.
Ensure the output strictly follows the provided JSON structured format.

**Penalty Policy for Misalignment**
Penalty for selecting actions not in the skills library.
Penalty for invalid parameters for the selected action (e.g. using object names not present in the environment description).
Penalty for not using the current environment description to define action parameters.
Penalty for proposing infeasible actions based on current state.

**Score Assignment**
You have to assign an action_score between 0.0 and 1.0 to each action, representing its relevance with respect to the overall actions you can take and feasibility based on current state.

**Output Requirements**
Your output must be a structured list of relevant actions following the provided JSON format. The answer must provide just a single JSON structure. Do not insert comments, hidden characters or other stuff that may compromise Structured Output.
Output Format (JSON)

{{
  "plan": [
    {{
      {action_placeholder1}
    }},
    ...,
    {{
      {action_placeholder2}
    }}
  ]
}}
"""
        if use_vlm:
            task_planning_prompt = task_planning_prompt.replace("{current_env_config}", "Infer from image")
            
        return fhp_ffhp_system_message, task_planning_prompt
