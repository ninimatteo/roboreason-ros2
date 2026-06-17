"""Pydantic data models for UR5 action extraction and predicate reasoning."""
from pydantic import BaseModel, Field
from typing import List, Optional


class UR5Action(BaseModel):
    """
    A single UR5 skill primitive action with its parameters.

    Supported skills: approach, pick, release, move_home, wait
    Positions are in the robot base frame (meters) for LLMs [x, y, z]
    or in the image frame (pixels) for VLMs [x_min, y_min, x_max, y_max].
    """
    action_name: str = Field(
        description='Skill name: approach | pick | release | move_home | wait'
    )
    target_position: Optional[List[float]] = Field(
        default=None,
        description='[x, y, z] in robot base frame (LLM) or [x_min, y_min, x_max, y_max] bbox in pixels (VLM) — used by approach and pick'
    )
    release_position: Optional[List[float]] = Field(
        default=None,
        description='[x, y, z] in robot base frame (LLM) or [x_min, y_min, x_max, y_max] bbox in pixels (VLM) — used by release'
    )
    object_height: Optional[float] = Field(
        default=0.0,
        description='Estimated real-world height of the held object in meters — used by release to lift TCP above the surface'
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
