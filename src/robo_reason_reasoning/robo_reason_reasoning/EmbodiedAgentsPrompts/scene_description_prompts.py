"""Scene-description prompt for the VLM->LLM hybrid pipeline.

Unlike the other prompt templates in this package, this one does not ask the
model to plan any actions. It asks it to *describe* the scene — every
graspable object and every placement target/zone visible in the image — so
the result can be deprojected and assembled into a scene_mock.json-shaped
file that the standard LLM planning pipeline can then ground on.
"""

_SYSTEM_MESSAGE = (
    "You are a vision system for a tabletop robot workspace. You must "
    "identify every graspable object and every placement target/zone "
    "visible in the image and report their positions as image pixel "
    "coordinates. You do not plan any actions."
)

_VLM_SCENE_DESCRIPTION_PROMPT = """
Your task is to analyze the image of the robot's tabletop workspace and describe every graspable object and every placement target/zone that is visible.

**Objects** are discrete graspable items on the table (e.g. cubes, cylinders, tools).
**Targets** are placement zones or containers where an object could be placed (e.g. marked boxes, trays, an empty area of the table).

**Pixel Coordinates**
The image is {pixels_width} pixels wide and {pixels_height} pixels tall — every
`pixel_center` you output must satisfy 0 <= x < {pixels_width} and 0 <= y < {pixels_height}.
- `pixel_center`: [x, y] — the center of the object/target's visible top surface, x = horizontal (column), y = vertical (row). Point at the CENTER, not a corner or edge.

**Size Estimation**
For each object/target, estimate its real-world `size` as [width, depth, height] in meters,
based on typical object scale (e.g. a small cube ~0.05 m, a box/tray ~0.15 m, a cylinder ~0.05 m diameter).

**Output Requirements**
Give a short unique `label` for every object/target (snake_case, based on color+shape, e.g. "black_cube", "white_box").
Set `graspable` and `state` for objects (default `graspable: true`, `state: "on_table"` unless clearly otherwise).
Do not invent objects/targets that are not visible. Do not output any action plan.
Your output must be a single JSON structure with exactly this shape, no extra text, comments, or markdown fences:

{{
  "objects": [
    {{
      "label": "<name>",
      "type": "<shape/category>",
      "color": "<color>",
      "pixel_center": [x, y],
      "size": [width, depth, height],
      "graspable": true,
      "state": "on_table"
    }}
  ],
  "targets": [
    {{
      "label": "<name>",
      "type": "<zone/box/etc>",
      "color": "<color>",
      "pixel_center": [x, y],
      "size": [width, depth, height]
    }}
  ]
}}
"""


class SceneDescriptionPrompts:

    @staticmethod
    def get_vlm_prompts() -> tuple:
        """Return (system_message, scene_description_prompt) for VLM scene grounding."""
        return _SYSTEM_MESSAGE, _VLM_SCENE_DESCRIPTION_PROMPT
