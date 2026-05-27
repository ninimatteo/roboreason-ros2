"""ReAct prompt templates — adapted for UR5 from RoboReason-Lab."""

react_system_message = (
    "You are a ReAct expert. You will be provided with a user request and an environment map. "
    "Your task is to generate a sequence of actions that will lead to the completion of the "
    "user request in the given environment."
)

react_prompt_message = """Your task is to generate a sequence of actions based on the user request and the current state of the environment.

You have to make a decision on the next step to take based on the information below:
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Current Environment Configuration** The physical information about the environment: \n{current_env_config}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**Reasoning** Your thoughts from the last reasoning step you took: \n{last_reasoning_step}

As a ReAct agent, you are expected to decide whether to take a reasoning step or an action based on the current state of the environment and the user request.
You are asked either to take a reasoning step or to take an action according to the skills you have.

**Output Requirements** adapt the output according to the following JSON format:

```json
{{
  "react_decision": "<either 'reasoning' or 'action'>",
  "action": <null or a JSON object with the following structure if you decide to act>,
    {{
    {action_placeholder}
    }}
  "reasoning": "<Null or your reasoning content if you decide to reason>",
  "end of simulation": "<bool: True or False, if you decide the goal has been reached>"
}}
Think step by step.
This is the list of past actions you took: \n{actions_memory}
"""
