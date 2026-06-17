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

_VLM_PLAN_PROMPT = """
Your task is to plan a sequence of actions to achieve the user's request, based on the image of the environment.

**Environment Description** Infer from image
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
Think step by step, starting from what you see in the image and the user's request.
"""


class CotScPrompts:

    @staticmethod
    def get_llm_prompts() -> str:
        """Return the plan prompt for LLM mode."""
        return _LLM_PLAN_PROMPT

    @staticmethod
    def get_vlm_prompts() -> str:
        """Return the plan prompt for VLM mode."""
        return _VLM_PLAN_PROMPT
