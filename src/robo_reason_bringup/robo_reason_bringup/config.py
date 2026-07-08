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

from typing import Dict, List

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
    # Per-request HTTP timeout (seconds) for every FoundationClient provider
    # call (Groq/OpenAI/Nebius/Anthropic — see base_client.py). Bounds a
    # single stuck LLM/VLM call so it fails fast with a catchable error
    # instead of silently absorbing the whole PLAN_TIMEOUT_S budget. Large
    # models (e.g. nebius/nvidia-nemotron-120b) can legitimately take longer
    # than the default per call — raise this if you see APITimeoutError on
    # requests that are just slow, not stuck.
    REQUEST_TIMEOUT_S: float = 120.0

    # ── VLM planner ───────────────────────────────────────────────────────────
    TMP_DIR: str = 'src/vlm_frames'
    # 'point'  — VLM emits a single [x, y] pixel click per target (validated on
    #            hardware; default, unchanged behavior).
    # 'bbox'   — VLM emits a [x_min, y_min, x_max, y_max] pixel box per target;
    #            the box center is deprojected for (x, y, z) and the box's
    #            pixel width/height are converted to a real-world grasp_width
    #            instead of relying on the VLM's blind numeric guess.
    VLM_GROUNDING_MODE: str = 'point'
    # Qwen3-family reasoning control, forwarded verbatim to Groq as the
    # 'reasoning_effort' request param (see LLMClient/VLMClient._call_groq).
    # ''       — omitted (default): unchanged behavior, model's own default.
    # 'none'   — disables <think> chain-of-thought entirely; worth trying when
    #            a reasoning-heavy VLM (e.g. groq/qwen3.6-27b) is visibly doing
    #            imprecise percentage-estimation arithmetic for pixel grounding
    #            instead of grounding directly (see debug-image "shifted point"
    #            reports on Groq vs. Nebius).
    # 'low' / 'medium' / 'high' / 'hidden' — other Groq-supported values.
    # Applies to both the direct VLM planner (vlm_planner_node) and the
    # VLM->LLM hybrid's scene-grounding call (vlm_llm_planner_node).
    VLM_REASONING_EFFORT: str = ''

    # ── VLM+LLM hybrid planner (scene grounding call, independent of MODEL_NAME
    # which is used for the subsequent LLM planning call) ─────────────────────
    VLM_MODEL_NAME: str = 'groq/qwen3.6-27b'
    VLM_TEMPERATURE: float = 0.1

    # ── Depth-informed grasp geometry (VLM/VLM_LLM pipelines) ──────────────────
    # Neither camera-grounded pipeline has a hand-calibrated EE contact height
    # the way scene_mock.json does for LLM mode, so we derive one from real
    # depth: object height = table_surface_z - deprojected_top_surface_z, and
    # the pick contact point descends a fraction of that height below the top
    # surface (mid-body grasp) instead of targeting the bare top surface.
    PICK_GRASP_DEPTH_FRACTION: float = 0.5   # fraction of object height to descend for pick
    MIN_OBJECT_HEIGHT_M: float = 0.02        # clamp for depth-computed object height

    # ── Per-run debug artifacts (command/response/errors/images/summary.csv) ──
    DEBUG_DIR: str = '/root/ws/src/roboreason-ros2/debug'
    # Container clock runs in UTC regardless of the operator's local time, so
    # debug timestamps need an explicit IANA zone to match wall-clock reality.
    DEBUG_TIMEZONE: str = 'Europe/Berlin'

    # ── Executor ──────────────────────────────────────────────────────────────
    ROBOT_IP: str = '192.168.2.60'
    HOME_JOINTS: List[float] = Field(
        default=[-1.9, -1.5708, -1.5708, -1.5708, 1.5708, 0.0]
    )
    # TCP offset (metres) — tool + gripper stack, e.g. OnRobot RG2 on UR5cb.
    # Set ROBOREASON_TCP_OFFSET_X/Y/Z env vars (or .env) to override without rebuild.
    TCP_OFFSET_X: float = 0.0
    TCP_OFFSET_Y: float = 0.0
    # The RG2 fingers pivot/arc, so the true flange-to-contact-point distance
    # varies with finger aperture (i.e. with object width). Calibrated values:
    # fully open ~0.175 m, mid-open ~0.207 m, fully closed ~0.213 m. Until
    # width-based interpolation picks the right one per object, default to the
    # mid-open calibration (closest to how the previous 0.16 default was set).
    # The width->offset calibration table + interpolation function live in
    # grasp_geometry.py (derived logic, not a plain setting).
    TCP_OFFSET_Z: float = 0.207
    # Length of the rigid clamp/mount body below the flange (metres) — unlike
    # the fingers, this part doesn't pivot around the object, so a pick that
    # needs to descend more than (TCP_OFFSET_Z - TCP_CLAMP_CLEARANCE_M) below
    # an object's top surface will crash the clamp into the object on the way
    # down. Used to cap pick-target descent depth for tall objects.
    TCP_CLAMP_CLEARANCE_M: float = 0.15

    # ── Camera topics / services ──────────────────────────────────────────────
    COLOR_TOPIC: str = '/camera/color/image_raw'
    DEPTH_TOPIC: str = '/camera/depth/image_raw'
    CAMERA_INFO_TOPIC: str = '/camera/color/camera_info'
    GET_IMAGE_SERVICE: str = '/camera/get_image'
    DEPROJECT_SERVICE: str = '/camera/deproject'
    PIXEL_DEBUG_TOPIC: str = '/camera/debug_pixels'
    CHARUCO_AXIS_TOPIC: str = '/camera/charuco_axis'
    CALIBRATION_STATUS_TOPIC: str = '/camera/calibration_status'

    # ── Depth filtering ───────────────────────────────────────────────────────
    WINDOW_SIZE: int = 7
    MIN_DEPTH_M: float = 0.15
    MAX_DEPTH_M: float = 3.0
    Z_OFFSET_M: float = 0.01   # lift deprojected points above table surface

    # ── Local plane-centroid experiment (Deproject pixel refinement) ─────────
    # Opt-in, fail-open experiment addressing VLM clicks landing on a random
    # point of the object (often an edge/side facet, not the centre): take a
    # local window of raw depth around the clicked pixel, keep only the points
    # near the window's highest base-frame z (the object's local top
    # horizontal plane), and re-target the (x, y) centroid + z of that plane
    # instead of trusting the raw click. Falls back to the existing raw
    # single-pixel lookup whenever too few points survive the threshold.
    LOCAL_PLANE_CENTROID_ENABLED: bool = True
    LOCAL_PLANE_CENTROID_RADIUS_PX: int = 25
    LOCAL_PLANE_CENTROID_Z_TOL_M: float = 0.01
    LOCAL_PLANE_CENTROID_MIN_POINTS: int = 8

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

    # ── GUI server (server_node.py) ───────────────────────────────────────────
    # Bind to 0.0.0.0 so the server is reachable from the host. With the
    # container's --network host mode, http://localhost:GUI_PORT on the host
    # hits this.
    GUI_HOST: str = '0.0.0.0'
    GUI_PORT: int = 8080

    # ── GUI bridge (bridge_node.py) — topics/services/timeouts ────────────────
    EXECUTION_LOG_TOPIC: str = '/execution_log'
    # The /execute_skill action server publishes this status topic exactly once
    # per server, so its publisher count == number of skill-executor servers.
    EXECUTE_SKILL_STATUS_TOPIC: str = '/execute_skill/_action/status'
    # Planner node names per mode — targets for live parameter updates.
    PLANNER_NODE_BY_MODE: Dict[str, str] = Field(default={
        'LLM': 'llm_planner_node',
        'VLM': 'vlm_planner_node',
        'VLM_LLM': 'vlm_llm_planner_node',
    })
    SET_PARAM_TIMEOUT_S: float = 5.0
    TRAJ_ACTION: str = '/scaled_joint_trajectory_controller/follow_joint_trajectory'
    GRIPPER_IO_SERVICE: str = '/io_and_status_controller/set_io'
    JOINT_STATES_TOPIC: str = '/joint_states'
    # A /joint_states message older than this (seconds) is considered stale.
    JOINT_STATES_TIMEOUT_S: float = 2.0
    CAMERA_TIMEOUT_S: float = 4.0
    RECALIBRATE_SERVICE: str = '/camera/recalibrate'
    # Service-call budgets (planning may involve a slow LLM/VLM round-trip).
    PLAN_TIMEOUT_S: float = 300.0
    EXECUTE_TIMEOUT_S: float = 300.0
    CANCEL_TIMEOUT_S: float = 20.0

    # ── Stack supervisor (stack_supervisor.py) ────────────────────────────────
    STACK_LOG_CAPACITY: int = 400
    STACK_STOP_GRACE_S: float = 8.0
    STACK_GRAPH_CLEAR_TIMEOUT_S: float = 6.0   # wait for orphan executors to leave the graph
    STACK_GRAPH_POLL_S: float = 0.3

    # ── UR driver supervisor (ur_driver_supervisor.py) ────────────────────────
    UR_DRIVER_LOG_CAPACITY: int = 600
    UR_DRIVER_HISTORY_CAPACITY: int = 30
    UR_DRIVER_MAX_ATTEMPTS: int = 10
    UR_DRIVER_READY_TIMEOUT_S: float = 30.0   # per-attempt wait for the robot to come up
    UR_DRIVER_BACKOFF_S: float = 2.0          # pause between failed attempts
    UR_DRIVER_READY_POLL_S: float = 0.5
    UR_DRIVER_STOP_GRACE_S: float = 8.0
    # Defaults for the lab UR5cb; overridable per start()/reconnect() request.
    # ROBOT_IP (above) doubles as the driver's default robot_ip.
    REVERSE_IP: str = '192.168.2.80'

    # ── Camera service supervisor (camera_service_supervisor.py) ──────────────
    CAMERA_SERVICE_LOG_CAPACITY: int = 300
    CAMERA_SERVICE_STOP_GRACE_S: float = 6.0

    # ── Subprocess shutdown escalation (stack/UR-driver/camera supervisors) ───
    # Grace period after SIGTERM, and again after SIGKILL, before giving up and
    # considering the process gone.
    PROCESS_FORCE_KILL_GRACE_S: float = 3.0


settings = Settings()
