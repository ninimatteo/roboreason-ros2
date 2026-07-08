"""Predicate extraction prompt templates for LLM and VLM modes."""


_LLM_PREDICATES_PROMPT = """You have to predict the predicates that define relationships among objects based on the environment description.

You will receive:
- **Predicates library** You must use only the predicates from the provided predicates library:
{predicates_library}
- **Environment Description** which is used to describe the current symbolic state of the environment with precise information about the environment, objects positions and dimensions.
{environment_description}
- **Score** You must assign a score between 0.0 and 1.0 to each predicted predicate, representing its relevance.
- **JSON Output Schema** Your output must be a structured list of relevant predicates following the provided JSON Output Schema:
```json
{{
"predicates": [
  {{
    "predicate": "predicate",
    "main": "object1",
    "relative": "object2",
    "explanation": "Short explanation of the relationship.",
    "score": 0.9
  }},
  {{
    "predicate": "predicate",
    "main": "object3",
    "relative": "object4",
    "explanation": "Short explanation of the relationship.",
    "score": 1.0
  }}
]
}}
```

**Penalty Policy for Misalignment:**
- You will receive a penalty for predicting more than 12 predicates.
- You will receive a penalty if you invent predicates outside the provided predicates library.
- You will receive a penalty if you predict predicates that are not applicable or realistic.
- You will receive a penalty if you predict predicates that are not relevant to the task's request.
- You will receive a penalty if you predict false predicates.
- You will receive a penalty if you provide more information on how you decided to apply certain predicates.

- You have to predict the applicable predicates among objects based on the environment description.
- You are not required to describe trivial predicates (ie. Contact(Chair, Floor) or Inside(Chair, Room/Scene)).
"""

_VLM_PREDICATES_PROMPT = """You have to predict the predicates that define relationships among objects based on what you see in the image.

You will receive:
- **Predicates library** You must use only the predicates from the provided predicates library:
{predicates_library}
- **Environment Description**: Infer from image
- **Score** You must assign a score between 0.0 and 1.0 to each predicted predicate, representing its relevance.
- **JSON Output Schema** Your output must be a structured list of relevant predicates following the provided JSON Output Schema:
```json
{{
"predicates": [
  {{
    "predicate": "predicate",
    "main": "object1",
    "relative": "object2",
    "explanation": "Short explanation of the relationship.",
    "score": 0.9
  }},
  {{
    "predicate": "predicate",
    "main": "object3",
    "relative": "object4",
    "explanation": "Short explanation of the relationship.",
    "score": 1.0
  }}
]
}}
```

**Penalty Policy for Misalignment:**
- You will receive a penalty for predicting more than 12 predicates.
- You will receive a penalty if you invent predicates outside the provided predicates library.
- You will receive a penalty if you predict predicates that are not applicable or realistic.
- You will receive a penalty if you predict predicates that are not relevant to the task's request.
- You will receive a penalty if you predict false predicates.

- You have to predict the applicable predicates among objects based on the image.
- You are not required to describe trivial predicates (ie. Contact(Chair, Floor) or Inside(Chair, Room/Scene)).
"""

_LLM_GOAL_PREDICATES_PROMPT = """You must understand the overall goal and you have to predict the predicates that will describe the environment once the goal is achieved.

You have to answer based on the following information:

- **Predicates Library** You must use only the predicates from the provided predicates library:
{predicates_library}

- **Environment Description** which is used to describe the current symbolic state of the environment with precise information about the environment (in a JSON fashion):
{environment_description}

- **User Request** The user will provide you with a request that you should be able to perform:
{user_request}

You will predict:
- **Goal Predicates**: The predicates that will describe the environment once the goal is achieved.
- **Score**: You must assign a score between 0.0 and 1.0 to each predicted predicate, representing its relevance.
- **JSON Output Schema**: Your output must be a structured list of relevant predicates following the provided JSON Output Schema:
```json
{{
"predicates": [
  {{
    "predicate": "predicate",
    "main": "object1",
    "relative": "object2",
    "explanation": "Short explanation of the relationship.",
    "score": 0.9
  }},
  {{
    "predicate": "predicate",
    "main": "object3",
    "relative": "object4",
    "explanation": "Short explanation of the relationship.",
    "score": 1.0
  }}
]
}}
```

**Penalty Policy for Misalignment:**
- You will receive a penalty if you invent predicates outside the provided predicates library.
- You will receive a penalty if you predict predicates that are not applicable or realistic.
- You will receive a penalty if you predict predicates that are not relevant to the task's request or the way the environment will look like once the task will be completed.
- You will receive a penalty if you predict negation of a given predicate (ie. not(blocking(a, b))).
"""

_VLM_GOAL_PREDICATES_PROMPT = """You must understand the overall goal and you have to predict the predicates that will describe the environment once the goal is achieved.

You have to answer based on the following information:

- **Predicates Library** You must use only the predicates from the provided predicates library:
{predicates_library}

- **Environment Description**: Infer from image

- **User Request** The user will provide you with a request that you should be able to perform:
{user_request}

You will predict:
- **Goal Predicates**: The predicates that will describe the environment once the goal is achieved.
- **Score**: You must assign a score between 0.0 and 1.0 to each predicted predicate, representing its relevance.
- **JSON Output Schema**: Your output must be a structured list of relevant predicates following the provided JSON Output Schema:
```json
{{
"predicates": [
  {{
    "predicate": "predicate",
    "main": "object1",
    "relative": "object2",
    "explanation": "Short explanation of the relationship.",
    "score": 0.9
  }},
  {{
    "predicate": "predicate",
    "main": "object3",
    "relative": "object4",
    "explanation": "Short explanation of the relationship.",
    "score": 1.0
  }}
]
}}
```

**Penalty Policy for Misalignment:**
- You will receive a penalty if you invent predicates outside the provided predicates library.
- You will receive a penalty if you predict predicates that are not applicable or realistic.
- You will receive a penalty if you predict predicates that are not relevant to the task's request or the way the environment will look like once the task will be completed.
- You will receive a penalty if you predict negation of a given predicate (ie. not(blocking(a, b))).
"""


class PredicatesPrompts:

    @staticmethod
    def get_llm_prompts() -> tuple:
        """Return (predicates_prompt, goal_predicates_prompt) for LLM mode."""
        return _LLM_PREDICATES_PROMPT, _LLM_GOAL_PREDICATES_PROMPT

    @staticmethod
    def get_vlm_prompts(grounding_mode: str = 'point') -> tuple:
        """Return (predicates_prompt, goal_predicates_prompt) for VLM mode.

        grounding_mode is accepted (and ignored) for interface parity with
        the other EmbodiedAgentsPrompts classes — _select_prompts() always
        passes it — since predicate prompts describe object relationships,
        not pixel coordinates, so there's no point/bbox variant to select.
        """
        return _VLM_PREDICATES_PROMPT, _VLM_GOAL_PREDICATES_PROMPT
