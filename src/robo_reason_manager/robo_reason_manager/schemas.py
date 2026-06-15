"""Shared constants, helpers, and data structures for robo_reason_real."""
import json

ALLOWED_SKILLS = {"approach", "pick", "release", "move_home", "wait"}

SKILL_REQUIRED_ARGS = {
    "approach": ["target_position"],
    "pick": ["target_position"],
    "release": ["release_position"],
    "move_home": [],
    "wait": ["time"],
}

SKILL_OPTIONAL_ARGS = {
    "approach": ["offset", "approach_direction"],
    "pick": ["grasp_axis", "come_back"],
    "release": ["come_back", "open_force", "object_height"],
    "move_home": [],
    "wait": [],
}

SKILL_DEFAULTS = {
    "approach": {"offset": 0.1, "approach_direction": "z"},
    "pick": {"grasp_axis": "z", "come_back": False},
    "release": {"come_back": False, "open_force": 20.0, "object_height": 0.0},
    "move_home": {},
    "wait": {},
}


def load_json(json_str: str) -> dict:
    """Parse a JSON string and return a dict. Raises ValueError on failure."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")


def serialize_json(obj) -> str:
    """Serialize a dict or list to a JSON string."""
    return json.dumps(obj, indent=2)


def extract_skill_args(step: dict) -> dict:
    """Extract skill arguments from a plan step dict (removes action_name and step index)."""
    return {k: v for k, v in step.items() if k not in ("action_name", "step", "score")}


def apply_skill_defaults(skill_name: str, args: dict) -> dict:
    """Fill in default values for optional skill args."""
    defaults = SKILL_DEFAULTS.get(skill_name, {})
    return {**defaults, **args}


def normalize_plan(plan: list) -> list:
    """Fix common LLM parameter name aliases before validation."""
    for step in plan:
        skill = step.get('action_name', '').lower()
        # LLMs often use target_position instead of release_position for release
        if skill == 'release' and step.get('release_position') is None:
            fallback = step.get('target_position')
            if fallback:
                step['release_position'] = fallback
    return plan
