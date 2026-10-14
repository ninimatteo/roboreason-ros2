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


# Camera setups for settings.CAMERAS (ROBOAI-47), as the GUI lists them.
CAMERA_SETUPS = ('fixed', 'fixed+wrist', 'wrist')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='ROBOREASON_',
        env_file='.env',
        extra='ignore',
    )

    # ── Profile: real robot or simulation ────────────────────────────────────
    # Selects which set of ROS names interfaces.yaml provides (see
    # robo_reason_bringup/interfaces.py). 'real' or 'sim'.
    PROFILE: str = 'real'
    # Scene the stack works on (ROBOAI-49): '' for scene_mock.json, a scene
    # name from robo_reason_task_interface/config/scenes, or a JSON path. See
    # robo_reason_bringup/scenes.py.
    SCENE: str = ''
    # Which cameras the stack runs (ROBOAI-47): 'fixed' (the depth camera at
    # the far end of the table), 'fixed+wrist' (plus an RGB camera on the
    # wrist, under the wrist/ namespace) or 'wrist' (a depth camera on the
    # wrist is the only, primary camera).
    CAMERAS: str = 'fixed'   # one of CAMERA_SETUPS
    # Mount of the wrist camera body in the tool0 frame, [x, y, z, roll, pitch,
    # yaw] (metres, radians, body +x forward like Gazebo cameras): looking
    # along the tool axis, 7 cm to the side so the gripper fingers stay out of
    # most of the view. The simulation mounts its sensor here; on the robot
    # the same numbers are published as a static TF (measure the real mount).
    WRIST_CAMERA_MOUNT: List[float] = [0.0, -0.07, 0.03, 0.0, -1.5708, 0.0]
    # Real wrist RGB camera for 'fixed+wrist' (wrist_rgb_camera_node, plain
    # V4L2): its device, and [fx, fy, cx, cy] once calibrated. Empty
    # intrinsics: estimated from the horizontal field of view (rad).
    WRIST_CAMERA_DEVICE: str = '/dev/video0'
    WRIST_CAMERA_INTRINSICS: List[float] = []
    WRIST_CAMERA_HFOV: float = 1.2
    # Alternative interfaces.yaml to read instead of the packaged one.
    INTERFACES_FILE: str = ''

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

    # ── GPU perception container (ROBOAI-60) ─────────────────────────────────
    # Read by segment_node inside the container (docker/perception): paths are
    # the container's. Thresholds are the ones validated in the ROBOAI-56 spike.
    PERCEPTION_SAM2_CHECKPOINT: str = '/perception/models/sam2/sam2.1_hiera_base_plus.pt'
    PERCEPTION_SAM2_CONFIG: str = 'configs/sam2.1/sam2.1_hiera_b+.yaml'
    PERCEPTION_DETECTOR: str = 'IDEA-Research/grounding-dino-tiny'
    PERCEPTION_BOX_THRESHOLD: float = 0.30
    PERCEPTION_TEXT_THRESHOLD: float = 0.25

    # ── Closed loop agent (mode AGENT, agent_node) ───────────────────────────
    # A vision model with native tool calling. kimi-k3 answered a tool call
    # with an image in about 1.5 s when probed on 2026-10-12.
    AGENT_MODEL_NAME: str = 'mistral/ministral-14b-latest'
    AGENT_MAX_MODEL_CALLS: int = 60      # per task, draft included
    AGENT_MAX_DURATION_S: float = 900.0  # execution phase wall clock budget
    AGENT_KEEP_IMAGES: int = 1           # camera images kept in the model context
    # Token economy (free tiers): images go out downscaled, turns older than
    # AGENT_HISTORY_TURNS replies are shortened, a task stops at the budget.
    AGENT_IMAGE_WIDTH: int = 448
    AGENT_HISTORY_TURNS: int = 8
    AGENT_MAX_INPUT_TOKENS: int = 300000
    # Free tier tokens per minute per provider, paced client side (0 = none).
    AGENT_TPM_LIMITS: Dict[str, int] = Field(default={'groq': 8000})
    # Largest single request a provider accepts (Groq free: 7000 input tokens).
    AGENT_MAX_REQUEST_TOKENS: Dict[str, int] = Field(default={'groq': 6500})

    # ── Deterministic geometry ablation (ROBOAI-30) ────────────────────────────
    # Two independent flags, both default False (normal behavior). "Soft" arm
    # sets DISABLE_GEOMETRY_FIX only; "hard" arm sets both together.
    #
    # When True, llm_planner_node skips _fix_object_height/_fix_release_height
    # entirely and leaves the model's own computed object_height/release
    # positions untouched -- i.e. the pre-fix behavior these two functions
    # replaced. LLM-mode only.
    DISABLE_GEOMETRY_FIX: bool = False
    # When True, plan_manager_node skips distribute_zone_releases entirely,
    # leaving multiple releases at (about) the same point uncorrected instead
    # of spacing them into a grid. Runs for all three planner modes (LLM, VLM,
    # VLM_LLM) -- see the call site in plan_manager_node.py.
    DISABLE_ZONE_DISTRIBUTION: bool = False
    # Which benchmark/*.csv file bridge_node.py's inline annotation form (and
    # benchmark_annotate.py, via the same-named ROBOREASON_ env var) appends
    # to. Keeps studies from mixing in one file (see 2026-09-04, ROBOAI-25) --
    # point this at a separate file while collecting for the ROBOAI-30
    # ablation, then switch it back.
    BENCHMARK_RESULTS_CSV: str = 'results_2026-09_3arm.csv'

    # ── VLM planner ───────────────────────────────────────────────────────────
    TMP_DIR: str = 'src/vlm_frames'
    # 'point'  — VLM emits a single [x, y] pixel click per target (validated on
    #            hardware; default, unchanged behavior).
    # 'bbox'   — VLM emits a [x_min, y_min, x_max, y_max] pixel box per target;
    #            the box center is deprojected for (x, y, z) and the box's
    #            pixel width/height are converted to a real-world grasp_width
    #            instead of relying on the VLM's blind numeric guess.
    VLM_GROUNDING_MODE: str = 'point'
    # A pick bbox's raw left/right edge pixels sit exactly on the object's
    # silhouette boundary — the single worst place to sample depth (highest
    # rate of "no valid depth" dropouts on real hardware). Sampling this
    # fraction inward from each edge instead lands on the object's actual
    # surface. See _apply_grasp_width in vlm_planner_node.py.
    GRASP_WIDTH_EDGE_INSET_FRAC: float = 0.15
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
    # How the VLM writes image coordinates, for both VLM planners:
    # 'pixel'    — pixels of the image it was sent.
    # 'norm1000' — 0..1000 on both axes relative to the image width, what the
    #              Qwen vision models on Groq answer whatever the image size
    #              (ROBOAI-43; the agent's own 'coords' parameter is the same).
    # 'auto'     — norm1000 for Qwen models, pixel otherwise.
    VLM_COORDS: str = 'auto'

    # ── VLM+LLM hybrid planner (scene grounding call, independent of MODEL_NAME
    # which is used for the subsequent LLM planning call) ─────────────────────
    VLM_MODEL_NAME: str = 'groq/qwen3.8-27b'
    VLM_TEMPERATURE: float = 0.1

    # ── Depth-informed grasp geometry (VLM/VLM_LLM pipelines) ──────────────────
    # Neither camera-grounded pipeline has a hand-calibrated EE contact height
    # the way scene_mock.json does for LLM mode, so we derive one from real
    # depth: object height = table_surface_z - deprojected_top_surface_z, and
    # the pick contact point descends a fraction of that height below the top
    # surface (mid-body grasp) instead of targeting the bare top surface.
    PICK_GRASP_DEPTH_FRACTION: float = 0.5   # fraction of object height to descend for pick
    MIN_OBJECT_HEIGHT_M: float = 0.02        # clamp for depth-computed object height

    # ── Release-collision spacing (plan_manager, schemas.distribute_zone_releases) ──
    # A "place these objects onto the tray" task can produce multiple releases
    # that land on (about) the same point — same target zone for LLM, same
    # depth-deprojected point for VLM/VLM_LLM. distribute_zone_releases nudges
    # the 2nd/3rd/... release at a given spot along a line (wrapping into a
    # grid once a row is full), spaced by the released object's grasp_width
    # plus this margin. Two releases at the same (x, y) but different z are
    # treated as an intentional stack, not a collision, and left alone.
    ZONE_PLACEMENT_COLLISION_RADIUS_M: float = 0.03  # how close (x, y) counts as "the same spot"
    ZONE_PLACEMENT_DEFAULT_SPACING_M: float = 0.05   # fallback item footprint when grasp_width is unknown
    ZONE_PLACEMENT_MARGIN_M: float = 0.01            # extra clearance between adjacent items
    ZONE_PLACEMENT_ITEMS_PER_ROW: int = 3            # line length before wrapping into a new grid row
    # Releases colliding inside a zone of known bounds: 'fit' lays them out as
    # one grid centred on the zone, rows and columns following its shape
    # (ROBOAI-21); 'ring' keeps the first where the plan put it and rings the
    # others around it at minimum spacing (ROBOAI-18 behaviour).
    ZONE_PLACEMENT_LAYOUT: str = 'fit'

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
    # 'survey' skill (ROBOAI-48): TCP point [x, y, z] in base_link, gripper
    # pointing down, from which a wrist camera sees the whole table. Used
    # when the wrist camera is the only one (CAMERAS=wrist).
    SURVEY_POSITION: List[float] = [0.05, -0.42, 0.52]
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

    # Known pose of the camera optical frame in base_link, as
    # [x, y, z, roll, pitch, yaw] (metres, radians, same RPY convention as the
    # board_in_base_* parameters). When set, camera_services_node takes the
    # camera→base_link transform as given and skips ChArUco calibration. Empty
    # (the default) means calibrate from the board, as on the real robot. The
    # simulation sets it, since its camera pose is known exactly.
    FIXED_CAMERA_POSE: List[float] = []

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
    # Port of the GUI started with the sim profile, so a real-robot GUI and a
    # simulation GUI can run side by side (digital twin).
    GUI_PORT_SIM: int = 8081

    # ── GUI bridge (bridge_node.py) — timeouts ───────────────────────────────
    # Planner node names per mode — targets for live parameter updates.
    PLANNER_NODE_BY_MODE: Dict[str, str] = Field(default={
        'LLM': 'llm_planner_node',
        'VLM': 'vlm_planner_node',
        'VLM_LLM': 'vlm_llm_planner_node',
        'AGENT': 'agent_node',
    })
    SET_PARAM_TIMEOUT_S: float = 5.0
    # A /joint_states message older than this (seconds) is considered stale.
    JOINT_STATES_TIMEOUT_S: float = 2.0
    CAMERA_TIMEOUT_S: float = 4.0
    # Service-call budgets (planning may involve a slow LLM/VLM round-trip).
    # 600 rather than 300: kimi-k2.6 planning took up to 299 s in the
    # September benchmark (median 100 s), so 300 s cut slow runs off.
    PLAN_TIMEOUT_S: float = 600.0
    # 900 for the AGENT mode, whose execution includes all the model calls.
    EXECUTE_TIMEOUT_S: float = 900.0
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
