"""Tree of Thoughts prompt templates — adapted for UR5 from RoboReason-Lab."""

plan_generation_prompt = """
Your task is to plan a sequence of actions to achieve the user's request.
Inputs:
**Environment Description** The physical information about the environment: \n{environment_map}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Previous plan**: {previous_thought}

**JSON Output schema**:
```json
{{"plan": [
    {{
    {action_placeholder1}
    }},
    ...,
    {{
    {action_placeholder1}
    }}
]
}}
```
Generate a plan that is feasible and aligns with the user's request. Think step by step, starting from the current state of the environment and the user's request.
"""

action_generation_prompt = """
Your task is to generate a set of possible actions for the next step to achieve the user's request. You might already have a plan so far.
You must use the JSON output schema provided.
Inputs:
**Skills Library** A list of available skills and actions you can use: \n{skills}
**Environment Description** The physical information about the environment: \n{environment_map}
**User Request** The request of the user that is the goal you have to achieve: \n{user_request}
**Number of actions to generate in this step**: You must generate a list of {num_actions} single actions (diversified in type of action and/or parameters) according to a tree of thoughts approach.

Think step by step, starting from the current state of the environment, the user request, and the plan so far.
**Plan so far**: The plan you proposed and validated so far \n{previous_thought}

What are the possible actions you can take for the next step? Consider 'move_home' as a valid action if you think no further action is needed.

**JSON Output schema**:
```json
{{
"sampled_actions": [
    {{
    {action_placeholder1}
    }},
    ...,
    {{
    {eos_action_placeholder}
    }}
    ]
}}
```
"""

thought_evaluation_prompt = """
Your task is to evaluate the solutions to a given user request in a physical environment.
Evaluate the following plan based on its feasibility and alignment with the user's request.
Evaluate feasibility taking into account the environment map ('environment_feasibility' in your response) and the skills library ('embodiment_feasibility' in your response).

**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Environment Description** The physical information about the environment: \n{environment_map}
**Skills Library** A list of available skills and actions you can use: \n{skills}

**Plan** The plan to be evaluated:
{thought}

**JSON Output schema**:
```json
{{
    "user_request_consistency": "insufficient/poor/fair/good/excellent",
    "environment_feasibility": "insufficient/poor/fair/good/excellent",
    "embodiment_feasibility": "insufficient/poor/fair/good/excellent"
}}
```
Respond strictly in the JSON format specified above.
Think step by step, starting from the current state of the environment and the user's request.
"""

thoughts_evaluation_in_batch_prompt = """
Your task is to evaluate the solutions to a given user request in a physical environment.
Evaluate the following plans based on their feasibility and alignment with the user's request.
Evaluate feasibility taking into account the environment map ('environment_feasibility' in your response) and the skills library ('embodiment_feasibility' in your response).

**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Environment Description** The physical information about the environment: \n{environment_map}
**Skills Library** A list of available skills and actions you can use: \n{skills}
**Evaluation Criteria explanations** The criteria dimensions to evaluate each plan:
- ***user_request_consistency***: How well the plan aligns with the user's request received.
- ***environment_consistency***: How well the plan can be executed in the current environment considering its composition and implicit constraints.
- ***embodiment_consistency***: How well each action in the plan aligns with its skill explanation and the implicit outcomes in the environment.

**List of Plans** The list of plans to be evaluated according to the JSON schema:
{thoughts}

**JSON Output schema**:
```json
{{
    "scores": [
        {{
        "user_request_consistency": "insufficient/poor/fair/good/excellent",
        "environment_feasibility": "insufficient/poor/fair/good/excellent",
        "embodiment_feasibility": "insufficient/poor/fair/good/excellent"
        }},
        ...,
        {{
        "user_request_consistency": "insufficient/poor/fair/good/excellent",
        "environment_feasibility": "insufficient/poor/fair/good/excellent",
        "embodiment_feasibility": "insufficient/poor/fair/good/excellent"
        }}
    ]
}}
```
Respond strictly in the JSON format specified above.
Think step by step, starting from the current state of the environment and the user's request.
"""

thought_sorting_prompt = """
Your task is to sort a list of plans based on the last appended action. You must rank them according to their feasibility and alignment with the user's request.
Inputs:
**User Request** The request of the user that is the goal you have to achieve with your plan: \n{user_request}
**Environment Description** The physical information about the environment: \n{environment_map}
**Plans to sort**: A list of plans to be sorted based on their last action: \n{list_of_plans}

**JSON Output schema**:
```json
{{
"best_plan": {{
    {action_placeholder1},
    ...,
    {action_placeholder2}
    }},
"other_plans": [
    {{
    {action_placeholder3},
    ...,
    {action_placeholder4}
    }}
]
}}
```
"""
