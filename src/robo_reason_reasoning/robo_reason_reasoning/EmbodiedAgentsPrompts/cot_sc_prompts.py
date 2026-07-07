"""CoT-SC prompt templates for LLM and VLM modes."""


_LLM_PLAN_PROMPT = """
Your task is to plan a sequence of actions to achieve the user's request, based on the information below:

**Environment Description** The physical information about the environment: \n{environment_map}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}

**JSON Output schema**:
```json
{{"plan": [
    {{
    {action_placeholder1}
    }},
    ...,
    {{
    {action_placeholder2}
    }}
]
}}
```
Respond strictly in the JSON format specified above.
Think step by step, starting from the current state of the environment and the user's request.
"""

_VLM_PLAN_PROMPT_POINT = """
Your task is to plan a sequence of actions to achieve the user's request, based on the image of the environment.

**Environment Description** Infer from image
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}

**Spatial Reasoning — Pixel Coordinates**
You are working in pixel space. The depth camera back-projects each pixel to a 3D point on
the visible surface, so pointing at the center of an object gives its top-surface 3D position.
The image you are given is {pixels_width} pixels wide and {pixels_height} pixels tall — every
pixel coordinate you output must satisfy 0 <= x < {pixels_width} and 0 <= y < {pixels_height}.
- `target_position`: [x, y] — center pixel of the object to grasp.
- `release_position`: [x, y] — center pixel of the target surface or object to stack on.
  Its deprojected z is already the top surface — do NOT add any z offset manually.
- Always set `object_height` to your visual estimate of the held object's real-world height
  in meters (e.g. 0.05 for a small block, 0.08 for a medium block, 0.10 for a cup, 0.15 for a bottle).
  The executor raises the TCP by this amount so the object bottom lands on the surface.
- Always set `grasp_width` in the pick action to your visual estimate of the object's real-world
  width in meters (e.g. 0.03 for a thin block, 0.06 for a cube, 0.08 for a cup). The executor uses
  this to select the correct gripper finger-aperture offset.
- The `approach` before a release must use the same [x, y] pixel as the release position.

**JSON Output schema**:
```json
{{"plan": [
    {{
    {action_placeholder1}
    }},
    ...,
    {{
    {action_placeholder2}
    }}
]
}}
```
Respond strictly in the JSON format specified above.
Think step by step, starting from what you see in the image and the user's request.
"""

_VLM_PLAN_PROMPT_BBOX = """
Your task is to plan a sequence of actions to achieve the user's request, based on the image of the environment.

**Environment Description** Infer from image
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}

**Spatial Reasoning — Pixel Bounding Boxes**
You are working in pixel space. The depth camera back-projects the center of a bounding box to a
3D point on the visible surface, so a tight box around an object gives its top-surface 3D position
plus its real-world footprint. The image you are given is {pixels_width} pixels wide and
{pixels_height} pixels tall — every pixel coordinate you output must satisfy
0 <= x < {pixels_width} and 0 <= y < {pixels_height}.
- `target_position`: [x_min, y_min, x_max, y_max] — the tightest pixel bounding box around the
  object to grasp. Do not include background or neighboring objects inside the box.
- `release_position`: [x_min, y_min, x_max, y_max] — the tightest pixel bounding box around the
  target surface or object to stack on. Its deprojected center z is already the top surface —
  do NOT add any z offset manually.
- Always set `object_height` to your visual estimate of the held object's real-world height
  in meters (e.g. 0.05 for a small block, 0.08 for a medium block, 0.10 for a cup, 0.15 for a bottle).
  The executor raises the TCP by this amount so the object bottom lands on the surface.
- Always set `grasp_width` to your visual estimate of the object's real-world width in meters as a
  fallback (e.g. 0.03 for a thin block, 0.06 for a cube, 0.08 for a cup) — the executor prefers
  deriving the width from your bounding box directly, but still needs this field populated.
- The `approach` before a release must use the same [x_min, y_min, x_max, y_max] box as the
  release position.

**JSON Output schema**:
```json
{{"plan": [
    {{
    {action_placeholder1}
    }},
    ...,
    {{
    {action_placeholder2}
    }}
]
}}
```
Respond strictly in the JSON format specified above.
Think step by step, starting from what you see in the image and the user's request.
"""


class CotScPrompts:

    @staticmethod
    def get_llm_prompts() -> str:
        """Return the plan prompt for LLM mode."""
        return _LLM_PLAN_PROMPT

    @staticmethod
    def get_vlm_prompts(grounding_mode: str = 'point') -> str:
        """Return the plan prompt for VLM mode."""
        return _VLM_PLAN_PROMPT_BBOX if grounding_mode == 'bbox' else _VLM_PLAN_PROMPT_POINT
