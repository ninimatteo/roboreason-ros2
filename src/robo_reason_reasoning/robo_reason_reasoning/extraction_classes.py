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


class DetectedObject(BaseModel):
    """A single graspable object detected by the VLM scene-grounding step."""
    label: str = Field(description='Short unique name for the object, e.g. "black_cube"')
    type: str = Field(default='object', description='Object shape/category, e.g. "cube", "cylinder"')
    color: Optional[str] = Field(default=None, description='Dominant color of the object')
    pixel_center: List[float] = Field(
        description='[w, h] (x, y) center pixel of the object in the source image'
    )
    size: List[float] = Field(
        default=[0.05, 0.05, 0.05],
        description='Visually estimated [width, depth, height] in meters'
    )
    graspable: bool = Field(default=True, description='Whether the gripper can pick this object')
    state: str = Field(default='on_table', description='Current object state, e.g. "on_table"')


class DetectedTarget(BaseModel):
    """A single placement target/zone detected by the VLM scene-grounding step."""
    label: str = Field(description='Short unique name for the target, e.g. "white_box"')
    type: str = Field(default='zone', description='Target category, e.g. "zone", "box"')
    color: Optional[str] = Field(default=None, description='Dominant color of the target')
    pixel_center: List[float] = Field(
        description='[w, h] (x, y) center pixel of the target surface in the source image'
    )
    size: List[float] = Field(
        default=[0.15, 0.15, 0.01],
        description='Visually estimated [width, depth, height] in meters'
    )


class VLMSceneDescription(BaseModel):
    """Full VLM scene-grounding output: every detected object and placement target."""
    objects: List[DetectedObject] = Field(default_factory=list)
    targets: List[DetectedTarget] = Field(default_factory=list)
