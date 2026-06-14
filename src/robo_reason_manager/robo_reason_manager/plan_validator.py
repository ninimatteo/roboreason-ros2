"""Plan validator: checks structural and semantic correctness before execution."""
from robo_reason_manager.schemas import ALLOWED_SKILLS, SKILL_REQUIRED_ARGS
from robo_reason_manager.world_state import WorldState


class PlanValidator:
    """Validates a plan list against the current world state."""

    def validate(self, plan: list, world_state: WorldState, mode: str = 'LLM'):
        """
        Validate a plan by checking structure and simulating execution on a copy.
        Returns (True, '') on success or (False, error_message) on failure.
        """
        self._vlm_mode = mode.upper() == 'VLM'
        if not isinstance(plan, list) or len(plan) == 0:
            return False, "Plan must be a non-empty list of steps."

        sim_state = world_state.copy()

        for i, step in enumerate(plan):
            step_label = f"Step {i+1}"

            if not isinstance(step, dict):
                return False, f"{step_label}: step must be a dict."

            skill = step.get('action_name', '').lower()
            if not skill:
                return False, f"{step_label}: missing 'action_name'."

            if skill not in ALLOWED_SKILLS:
                return False, (
                    f"{step_label}: skill '{skill}' not in allowed set. "
                    f"Allowed: {sorted(ALLOWED_SKILLS)}"
                )

            # Check required arguments
            required = SKILL_REQUIRED_ARGS.get(skill, [])
            for arg in required:
                if step.get(arg) is None:
                    return False, f"{step_label}: skill '{skill}' missing required arg '{arg}'."

            # Semantic checks with simulation
            ok, err = self._check_semantics(skill, step, sim_state, step_label)
            if not ok:
                return False, err

            # Advance simulated state
            sim_state.apply_skill_result(skill, step)

        return True, ""

    def _check_semantics(self, skill: str, step: dict, sim_state: WorldState, label: str):
        if skill == 'approach':
            pos = step.get('target_position')
            if not sim_state.validate_workspace_position(pos):
                return False, f"{label}: approach target {pos} is outside workspace limits."

        elif skill == 'pick':
            if not self._vlm_mode and sim_state.robot_holding() is not None:
                return False, (
                    f"{label}: pick called but robot is already holding "
                    f"'{sim_state.robot_holding()}'. Release first."
                )
            pos = step.get('target_position')
            if not sim_state.validate_workspace_position(pos):
                return False, f"{label}: pick target {pos} is outside workspace limits."

        elif skill == 'release':
            if not self._vlm_mode and sim_state.robot_holding() is None:
                return False, f"{label}: release called but robot is not holding anything."
            pos = step.get('release_position')
            if not sim_state.validate_workspace_position(pos):
                return False, f"{label}: release position {pos} is outside workspace limits."

        elif skill == 'wait':
            t = step.get('time', 0)
            if t < 0:
                return False, f"{label}: wait time must be >= 0."

        return True, ""
