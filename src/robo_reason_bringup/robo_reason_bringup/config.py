"""
Centralized configuration for the RoboReason ROS2 stack.

All values can be overridden via environment variables prefixed with ROBOREASON_
or via a .env file in the working directory.

Examples:
  export ROBOREASON_MODEL_NAME=groq/llama3.3-70b
  export ROBOREASON_ROBOT_IP=192.168.2.61
  export ROBOREASON_USE_MOCK_LLM=false

List values (e.g. HOME_JOINTS) must be passed as JSON arrays:
  export ROBOREASON_HOME_JOINTS='[-1.9, -1.5708, -1.5708, -1.5708, 1.5708, 0.0]'
"""

from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='ROBOREASON_',
        env_file='.env',
        extra='ignore',
    )

    # ── Planner (LLM + VLM) ──────────────────────────────────────────────────
    USE_MOCK_LLM: bool = True
    REASONING_METHOD: str = 'cot_sc'
    MODEL_NAME: str = 'nebius/nvidia-nemotron-120b'
    TEMPERATURE: float = 0.1

    # ── VLM planner ───────────────────────────────────────────────────────────
    TMP_DIR: str = '/root/ws/src/vlm_frames'

    # ── Executor ──────────────────────────────────────────────────────────────
    ROBOT_IP: str = '192.168.2.60'
    HOME_JOINTS: List[float] = Field(
        default=[-1.9, -1.5708, -1.5708, -1.5708, 1.5708, 0.0]
    )

    # ── Camera topics / services ──────────────────────────────────────────────
    COLOR_TOPIC: str = '/camera/color/image_raw'
    DEPTH_TOPIC: str = '/camera/depth/image_raw'
    CAMERA_INFO_TOPIC: str = '/camera/color/camera_info'
    GET_IMAGE_SERVICE: str = '/camera/get_image'
    DEPROJECT_SERVICE: str = '/camera/deproject'
    PIXEL_DEBUG_TOPIC: str = '/camera/debug_pixels'

    # ── Depth filtering ───────────────────────────────────────────────────────
    WINDOW_SIZE: int = 7
    MIN_DEPTH_M: float = 0.15
    MAX_DEPTH_M: float = 3.0
    Z_OFFSET_M: float = 0.01   # lift deprojected points above table surface

    # ── ChArUco calibration board ─────────────────────────────────────────────
    CHARUCO_ENABLED: bool = True
    CHARUCO_DICTIONARY: str = 'DICT_6X6_250'
    CHARUCO_SQUARES_X: int = 3
    CHARUCO_SQUARES_Y: int = 4
    CHARUCO_SQUARE_LENGTH_M: float = 0.062
    CHARUCO_MARKER_LENGTH_M: float = 0.031
    CHARUCO_AXIS_LENGTH_M: float = 0.08
    CHARUCO_MIN_CORNERS: int = 4
    CHARUCO_Z_SIGN: float = 1.0   # +1.0 for right-handed triad (fixed from -1.0)
    CHARUCO_FRAME_ID: str = 'charuco_board'

    # ── Board pose in robot base frame (for deproject) ────────────────────────
    # Measured once per physical setup. Position in metres, rotation as
    # intrinsic RPY in radians.
    BOARD_IN_BASE_X: float = -0.224
    BOARD_IN_BASE_Y: float = -0.348
    BOARD_IN_BASE_Z: float = -0.030
    BOARD_IN_BASE_ROLL: float = 3.14159
    BOARD_IN_BASE_PITCH: float = 0.0
    BOARD_IN_BASE_YAW: float = 1.5708

    # ── Mock camera service ───────────────────────────────────────────────────
    MOCK_IMAGES_DIR: str = '/root/ws/src/mock_frames'

    # ── Pixel overlay viewer ─────────────────────────────────────────────────
    VIEWER_WINDOW_NAME: str = 'VLM pixels on RGB'
    VIEWER_MARKER_SIZE: int = 14
    VIEWER_LINE_THICKNESS: int = 2


settings = Settings()
