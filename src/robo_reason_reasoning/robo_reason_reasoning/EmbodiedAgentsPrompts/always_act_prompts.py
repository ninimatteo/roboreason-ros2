"""Always-Act (StepAction) prompt templates for LLM and VLM modes."""


_LLM_STEP_ACTION_PROMPT = """
Your task is to decide either the next action to take in the environment to move toward achieving the user's request, or if the goal has been reached.
If the goal has been reached, return action "move_home" with "end_of_simulation" set to true.

**Environment Description** This describes the current state of the environment: \n{environment_map}\n
**Skills Library** This is the set of symbolic skills available for reasoning: \n{skills}\n
**User Request** This is the specific task the user wants to accomplish: \n{user_request}\n

**JSON Output Schema** Use the following JSON format to output your decision:
```json
{{
"action": {{
    {action_placeholder}
}},
"end_of_simulation": "<bool: True or False>"
}}
```
Think step by step. Respond strictly in the JSON format specified above. What is your next action?
This is the history of actions taken so far: {actions_memory}
"""

_VLM_STEP_ACTION_PROMPT = """
Your task is to decide either the next action to take in the environment to move toward achieving the user's request, or if the goal has been reached.
If the goal has been reached, return action "move_home" with "end_of_simulation" set to true.

**Environment Description** Infer from image\n
**Skills Library** This is the set of symbolic skills available for reasoning: \n{skills}\n
**User Request** This is the specific task the user wants to accomplish: \n{user_request}\n

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

**JSON Output Schema** Use the following JSON format to output your decision:
```json
{{
"action": {{
    {action_placeholder}
}},
"end_of_simulation": "<bool: True or False>"
}}
```
Think step by step. Respond strictly in the JSON format specified above. What is your next action?
This is the history of actions taken so far: {actions_memory}
"""


class AlwaysActPrompts:

    @staticmethod
    def get_llm_prompts() -> str:
        """Return the step-action prompt for LLM mode."""
        return _LLM_STEP_ACTION_PROMPT

    @staticmethod
    def get_vlm_prompts() -> str:
        """Return the step-action prompt for VLM mode."""
        return _VLM_STEP_ACTION_PROMPT
