"""FHP / FFHP prompt templates for LLM and VLM modes."""


_SYSTEM_MESSAGE = (
    "You are an embodied agent capable of spatial reasoning and planning actions in an "
    "environment. You must determine whether each action in your plan is feasible based "
    "on the environment's current state."
)

_LLM_TASK_PLANNING_PROMPT = """
Your task is to analyze the current state of the environment and the user's request to generate a feasible action plan that is a list of actions in a JSON format.

**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Current Environment Configuration**: \n{current_env_config}
**Skills Library** A list of available skills and actions you can use: \n{skills}

**Policy for Action Selection**
Select actions strictly from the skills library.
Use object names and positions from the environment description.
Ensure the output strictly follows the provided JSON structured format.

**Spatial Reasoning — Object Dimensions and Stacking**
Every object in the scene has a `size: [width, depth, height]` field (meters). Use it when computing positions:
- Picking an object: `target_position.z = object.position.z` (contact point at the object centre).
- Releasing on the table or a flat zone: `release_position.z = surface_z` (table surface).
- Releasing on top of another object: `release_position = [target.position.x, target.position.y, target.position.z + target.size[2]]`.
  This places the held object on the top surface of the target, not inside it.
- Always set `object_height` in the release action to `size[2]` of the **held** object so the executor raises the TCP by the correct amount before opening the gripper.
- The `approach` before a release should use the same x, y, z as the release position — the executor adds the offset automatically.

**Penalty Policy for Misalignment**
Penalty for selecting actions not in the skills library.
Penalty for invalid parameters for the selected action (e.g. using object names not present in the environment description).
Penalty for not using the current environment description to define action parameters.
Penalty for proposing infeasible actions based on current state.
Penalty for computing release_position without accounting for the target object height when stacking.

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

_VLM_TASK_PLANNING_PROMPT = """
Your task is to analyze the image of the environment and the user's request to generate a feasible action plan that is a list of actions in a JSON format.

**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Current Environment Configuration**: Infer from image
**Skills Library** A list of available skills and actions you can use: \n{skills}

**Policy for Action Selection**
Select actions strictly from the skills library.
Identify objects and their positions directly from the image.
Ensure the output strictly follows the provided JSON structured format.

**Spatial Reasoning — Pixel Coordinates**
You are working in pixel space. The depth camera back-projects each pixel to a 3D point on
the visible surface, so pointing at the center of an object gives its top-surface 3D position.
The image you are given is {pixels_width} pixels wide and {pixels_height} pixels tall — every
pixel coordinate you output must satisfy 0 <= h < {pixels_height} and 0 <= w < {pixels_width}.
- `target_position`: [h, w] — center pixel of the object to grasp.
- `release_position`: [h, w] — center pixel of the target surface or object to stack on.
  Its deprojected z is already the top surface — do NOT add any z offset manually.
- Always set `object_height` to your visual estimate of the held object's real-world height
  in meters (e.g. 0.05 for a small block, 0.08 for a medium block, 0.10 for a cup, 0.15 for a bottle).
  The executor raises the TCP by this amount so the object bottom lands on the surface.
- The `approach` before a release must use the same [h, w] pixel as the release position.

**Penalty Policy for Misalignment**
Penalty for selecting actions not in the skills library.
Penalty for pixel coordinates that do not point to the center of the visible object.
Penalty for proposing infeasible actions based on the visible scene.
Penalty for setting object_height to 0.0 when stacking objects.

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


class FHP_FFHP_Prompts:

    @staticmethod
    def get_llm_prompts() -> tuple:
        """Return (system_message, task_planning_prompt) for LLM mode."""
        return _SYSTEM_MESSAGE, _LLM_TASK_PLANNING_PROMPT

    @staticmethod
    def get_vlm_prompts() -> tuple:
        """Return (system_message, task_planning_prompt) for VLM mode."""
        return _SYSTEM_MESSAGE, _VLM_TASK_PLANNING_PROMPT
