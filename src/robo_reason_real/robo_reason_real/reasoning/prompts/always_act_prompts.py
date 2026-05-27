"""Always-Act (StepAction) prompt templates — adapted for UR5 from RoboReason-Lab."""

step_action_prompt = """
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
