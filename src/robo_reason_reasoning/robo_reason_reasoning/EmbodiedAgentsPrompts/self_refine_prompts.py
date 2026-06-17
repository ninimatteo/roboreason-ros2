"""Self-Refine prompt templates for LLM and VLM modes."""


_LLM_INITIAL_SOLUTION_PROMPT = """Your task is to plan a sequence of actions to achieve the user's request.

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
Generate a plan that is feasible and aligns with the user's request. Think step by step, starting from the current state of the environment and the user's request.
"""

_VLM_INITIAL_SOLUTION_PROMPT = """Your task is to plan a sequence of actions to achieve the user's request.

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
Generate a plan that is feasible and aligns with the user's request. Think step by step, starting from what you see in the image and the user's request.
"""

_LLM_FEEDBACK_PROMPT = """
Your task is to provide detailed feedback on a solution to a given user request in a physical environment.
Analyze the following plan based on its feasibility, alignment with the user's request, and potential improvements.

**Environment Description** The physical information about the environment: \n{environment_map}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve with the plan: \n{user_request}

**Plan to Evaluate**:
{solution}

**JSON Output schema**:
```json
{{
    "user_request_consistency": "insufficient/poor/fair/good/excellent",
    "environment_feasibility": "insufficient/poor/fair/good/excellent",
    "embodiment_feasibility": "insufficient/poor/fair/good/excellent",
    "detailed_feedback": "Provide specific feedback about what works well and what needs improvement",
    "suggestions": "Provide specific suggestions for improvement",
    "is_satisfactory": true/false
}}
```

Provide constructive feedback that can help refine the solution. Focus on specific issues and actionable improvements.
"""

_VLM_FEEDBACK_PROMPT = """
Your task is to provide detailed feedback on a solution to a given user request in a physical environment.
Analyze the following plan based on its feasibility, alignment with the user's request, and potential improvements.

**Environment Description** Infer from image
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve with the plan: \n{user_request}

**Plan to Evaluate**:
{solution}

**JSON Output schema**:
```json
{{
    "user_request_consistency": "insufficient/poor/fair/good/excellent",
    "environment_feasibility": "insufficient/poor/fair/good/excellent",
    "embodiment_feasibility": "insufficient/poor/fair/good/excellent",
    "detailed_feedback": "Provide specific feedback about what works well and what needs improvement",
    "suggestions": "Provide specific suggestions for improvement",
    "is_satisfactory": true/false
}}
```

Provide constructive feedback that can help refine the solution. Focus on specific issues and actionable improvements.
"""

_LLM_REFINEMENT_PROMPT = """
Your task is to refine a plan based on the feedback provided. Use the feedback to improve the solution while maintaining alignment with the user's request.

**Environment Description** The physical information about the environment: \n{environment_map}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve: \n{user_request}

**Initial Solution**:
{initial_solution}

**Current Solution**:
{current_solution}

**All Previous Feedback**:
{feedback_history}

**Most Recent Feedback**:
{current_feedback}

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

Generate a refined plan that addresses the feedback while maintaining feasibility and alignment with the user's request.
"""

_VLM_REFINEMENT_PROMPT = """
Your task is to refine a plan based on the feedback provided. Use the feedback to improve the solution while maintaining alignment with the user's request.

**Environment Description** Infer from image
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve: \n{user_request}

**Initial Solution**:
{initial_solution}

**Current Solution**:
{current_solution}

**All Previous Feedback**:
{feedback_history}

**Most Recent Feedback**:
{current_feedback}

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

Generate a refined plan that addresses the feedback while maintaining feasibility and alignment with the user's request.
"""


class SelfRefinePrompts:

    @staticmethod
    def get_llm_prompts() -> tuple:
        """Return (initial_solution_prompt, feedback_prompt, refinement_prompt) for LLM mode."""
        return _LLM_INITIAL_SOLUTION_PROMPT, _LLM_FEEDBACK_PROMPT, _LLM_REFINEMENT_PROMPT

    @staticmethod
    def get_vlm_prompts() -> tuple:
        """Return (initial_solution_prompt, feedback_prompt, refinement_prompt) for VLM mode."""
        return _VLM_INITIAL_SOLUTION_PROMPT, _VLM_FEEDBACK_PROMPT, _VLM_REFINEMENT_PROMPT
