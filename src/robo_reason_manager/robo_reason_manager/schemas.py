"""Shared constants, helpers, and data structures for robo_reason_real."""

ALLOWED_SKILLS = {"approach", "pick", "release", "move_home", "wait"}

SKILL_REQUIRED_ARGS = {
    "approach": ["target_position"],
    "pick": ["target_position"],
    "release": ["release_position"],
    "move_home": [],
    "wait": ["time"],
}


def extract_skill_args(step: dict) -> dict:
    """Extract skill arguments from a plan step dict (removes action_name and step index)."""
    return {k: v for k, v in step.items() if k not in ("action_name", "step", "score")}


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
