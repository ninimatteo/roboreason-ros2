"""Pydantic data models for UR5 action extraction and predicate reasoning."""
from pydantic import BaseModel, Field
from typing import List, Optional


class GoalReach(BaseModel):
    """Whether the goal has been reached."""
    goal_reached: bool = Field(description='True if the goal has been reached.')
    explanation: Optional[str] = Field(default=None)


class Predicate(BaseModel):
    """A spatial or state relationship between objects."""
    predicate: str = Field(description='Predicate name (e.g. Above, Contact, Inside)')
    main: str = Field(description='Main object')
    relative: Optional[str] = Field(default=None, description='Relative object, if applicable')
    explanation: str = Field(description='Short explanation of the relationship')
    score: float = Field(description='Relevance score 0–1')


class Predicates(BaseModel):
    predicates: List[Predicate]


class Effect(BaseModel):
    """An effect predicate for goal-reaching planning."""
    effect: str
    main: str
    relative: Optional[str] = None
    score: float


class Effects(BaseModel):
    effects: List[Effect]


class UR5Action(BaseModel):
    """
    A single UR5 skill primitive action with its parameters.

    Supported skills: approach, pick, release, move_home, wait
    Positions are in the robot base frame (meters) for LLMs [x, y, z] 
    or in the image frame (pixels) for VLMs [h, w].
    """
    action_name: str = Field(
        description='Skill name: approach | pick | release | move_home | wait'
    )
    target_position: Optional[List[float]] = Field(
        default=None,
        description='[x, y, z] in robot base frame (LLM) or [h, w] in image frame (VLM) — used by approach and pick'
    )
    release_position: Optional[List[float]] = Field(
        default=None,
        description='[x, y, z] in robot base frame (LLM) or [h, w] in image frame (VLM) — used by release'
    )
    offset: Optional[float] = Field(
        default=0.1,
        description='Approach offset in meters (default 0.1)'
    )
    approach_direction: Optional[str] = Field(
        default='z',
        description="Approach direction: 'z' (from above), 'x', 'y'"
    )
    grasp_axis: Optional[str] = Field(
        default='z',
        description="Grasp axis for pick: 'z' (top-down), 'x', 'y'"
    )
    come_back: Optional[bool] = Field(
        default=False,
        description='Return to pre-action position after execution'
    )
    time: Optional[float] = Field(
        default=None,
        description='Duration in seconds — used by wait'
    )
    score: Optional[float] = Field(
        default=None,
        description='Action importance score 0–1'
    )


class UR5Actions(BaseModel):
    """A list of UR5 actions (used for batch extraction)."""
    actions: List[UR5Action]
