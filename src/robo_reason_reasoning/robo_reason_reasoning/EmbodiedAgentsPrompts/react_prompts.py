"""ReAct prompt templates for LLM and VLM modes."""


_SYSTEM_MESSAGE = (
    "You are a ReAct expert. You will be provided with a user request and an environment map. "
    "Your task is to generate a sequence of actions that will lead to the completion of the "
    "user request in the given environment."
)

_LLM_REACT_PROMPT = """Your task is to generate a sequence of actions based on the user request and the current state of the environment.

You have to make a decision on the next step to take based on the information below:
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Current Environment Configuration** The physical information about the environment: \n{current_env_config}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**Reasoning** Your thoughts from the last reasoning step you took: \n{last_reasoning_step}

As a ReAct agent, you are expected to decide whether to take a reasoning step or an action based on the current state of the environment and the user request.
You are asked either to take a reasoning step or to take an action according to the skills you have.

**Spatial Reasoning — Object Dimensions and Stacking**
Every object in the scene has a `size: [width, depth, height]` field (meters). Use it when computing positions:
- Picking an object: `target_position.z = object.position.z` (contact point at the object centre).
- Releasing on the table or a flat zone: `release_position.z = surface_z` (table surface).
- Releasing on top of another object: `release_position = [target.position.x, target.position.y, target.position.z + target.size[2]]`.
  This places the held object on the top surface of the target, not inside it.
- Always set `object_height` in the release action to `size[2]` of the **held** object so the executor raises the TCP by the correct amount before opening the gripper.
- The `approach` before a release should use the same x, y, z as the release position — the executor adds the offset automatically.

**Output Requirements** adapt the output according to the following JSON format:

```json
{{
  "react_decision": "<either 'reasoning' or 'action'>",
  "action": <null or a JSON object with the following structure if you decide to act>,
    {{
    {action_placeholder}
    }}
  "reasoning": "<Null or your reasoning content if you decide to reason>",
  "end_of_simulation": "<bool: True or False, if you decide the goal has been reached>"
}}
Think step by step.
This is the list of past actions you took: \n{actions_memory}
"""

_VLM_REACT_PROMPT = """Your task is to generate a sequence of actions based on the user request and what you see in the image.

You have to make a decision on the next step to take based on the information below:
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Current Environment Configuration** Infer from image
**Skills Library** A list of available skills and actions you can use: \n{skills}
**Reasoning** Your thoughts from the last reasoning step you took: \n{last_reasoning_step}

As a ReAct agent, you are expected to decide whether to take a reasoning step or an action based on what you see in the image and the user request.
You are asked either to take a reasoning step or to take an action according to the skills you have.

**Spatial Reasoning — Stacking with Bounding Boxes**
You are working in pixel space. The depth camera sees the **top surface** of every object, so
deprojecting the center of an object's bounding box already gives a 3D point on its top surface.
- Picking an object: `target_position` = bounding box of the object to grasp.
- Releasing on the table or a flat zone: `release_position` = bounding box of the target table area.
- Releasing on top of another object (stacking): `release_position` = bounding box of the **target object**. Its deprojected z will be the top of that object — do NOT manually add any z offset.
- Always set `object_height` in the release action to your visual estimate of the **held** object's real-world height in meters (e.g. 0.05 for a small block, 0.10 for a cup). The executor raises the TCP by this amount so the held object's bottom lands on the target surface.
- The `approach` before a release must use the same bounding box as the release position.

**Output Requirements** adapt the output according to the following JSON format:

```json
{{
  "react_decision": "<either 'reasoning' or 'action'>",
  "action": <null or a JSON object with the following structure if you decide to act>,
    {{
    {action_placeholder}
    }}
  "reasoning": "<Null or your reasoning content if you decide to reason>",
  "end_of_simulation": "<bool: True or False, if you decide the goal has been reached>"
}}
Think step by step.
This is the list of past actions you took: \n{actions_memory}
"""


class ReActPrompts:

    @staticmethod
    def get_llm_prompts() -> tuple:
        """Return (system_message, react_prompt) for LLM mode."""
        return _SYSTEM_MESSAGE, _LLM_REACT_PROMPT

    @staticmethod
    def get_vlm_prompts() -> tuple:
        """Return (system_message, react_prompt) for VLM mode."""
        return _SYSTEM_MESSAGE, _VLM_REACT_PROMPT
