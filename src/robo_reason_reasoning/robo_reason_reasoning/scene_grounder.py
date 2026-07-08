"""VLM-only scene grounding: turns a single RGB image into a structured
scene description (objects + placement targets, each with a pixel center),
without planning any actions.

This is deliberately not a ReasoningMethod subclass — it's a one-shot
perception call, not a step()-driven action-planning loop. The result of
ground_scene() still needs its pixel_center fields deprojected to world
[x, y, z] by the caller (same /camera/deproject batching pattern used by
vlm_planner_node) before it can be merged into a scene_mock.json-shaped file.
"""
from robo_reason_reasoning.EmbodiedAgentsPrompts.scene_description_prompts import (
    SceneDescriptionPrompts,
)
from robo_reason_reasoning.extraction_classes import VLMSceneDescription
from robo_reason_reasoning.FoundationClients.src.vlm_client import VLMClient
from robo_reason_reasoning.reasoning_method import ReasoningMethod


def _image_pixel_dims(image) -> tuple:
    """Return (width, height) of the image file, or (0, 0) if unavailable."""
    if not image:
        return 0, 0
    try:
        from PIL import Image
        with Image.open(image) as img:
            return img.size
    except Exception:
        return 0, 0


class SceneGrounder:
    """Calls a VLM once to describe the scene visible in an image."""

    def __init__(self, client_parameters: dict = None):
        self.client = VLMClient(**(client_parameters or {}))

    def ground_scene(self, image: str) -> VLMSceneDescription:
        system_message, prompt_template = SceneDescriptionPrompts.get_vlm_prompts()
        width, height = _image_pixel_dims(image)
        user_message = prompt_template.format(pixels_width=width, pixels_height=height)
        text_prompt = f"**System Message**:\n{system_message}\n\n**User Message**: \n{user_message}"

        raw = self.client(
            text_prompt=text_prompt,
            image=image,
            force_json=True,
            forced_json_schema=VLMSceneDescription,
        )
        cleaned = ReasoningMethod._extract_json(raw)
        return VLMSceneDescription.model_validate_json(cleaned)
