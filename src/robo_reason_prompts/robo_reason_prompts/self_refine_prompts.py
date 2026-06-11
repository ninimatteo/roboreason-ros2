"""Self-Refine prompt templates — adapted for UR5 from RoboReason-Lab."""

class SelfRefinePrompts:
    @staticmethod
    def get_prompts(use_vlm: bool = False):
        initial_solution_prompt = """Your task is to plan a sequence of actions to achieve the user's request.

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

        feedback_prompt = """
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

        refinement_prompt = """
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
        if use_vlm:
            initial_solution_prompt = initial_solution_prompt.replace("{environment_map}", "Infer from image")
            feedback_prompt = feedback_prompt.replace("{environment_map}", "Infer from image")
            refinement_prompt = refinement_prompt.replace("{environment_map}", "Infer from image")
            
        return initial_solution_prompt, feedback_prompt, refinement_prompt
