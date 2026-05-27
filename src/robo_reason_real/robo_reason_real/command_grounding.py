"""Command grounding: verify the user command refers to objects/colors present in the scene."""
import json
import re


def check_command_grounding(user_command: str, scene_json: str):
    """
    Check whether the user command references colors/objects present in the scene.
    Returns (True, '') if grounded, (False, error_message) if not.
    """
    try:
        scene = json.loads(scene_json)
    except json.JSONDecodeError:
        return False, "Scene JSON is invalid."

    objects = scene.get('objects', {})
    targets = scene.get('targets', {})

    # Collect colors and names from the scene
    scene_colors = set()
    scene_names = set()
    for obj_id, obj in objects.items():
        scene_names.add(obj_id.lower())
        color = obj.get('color', '')
        if color:
            scene_colors.add(color.lower())
    for tgt_id, tgt in targets.items():
        scene_names.add(tgt_id.lower())
        label = tgt.get('label', '')
        if label:
            scene_names.add(label.lower())

    cmd_lower = user_command.lower()

    # Extract color words from the command
    color_words = re.findall(r'\b(red|blue|green|yellow|orange|purple|pink|white|black|cyan|magenta)\b', cmd_lower)

    for color in color_words:
        if color not in scene_colors:
            available = sorted(scene_colors)
            return False, (
                f"Color '{color}' not found in scene objects. "
                f"Available colors: {available}"
            )

    return True, ""
