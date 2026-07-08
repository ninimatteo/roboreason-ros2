import asyncio
import os
from typing import Optional

from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from robo_reason_gui.options import get_options


class CommandRequest(BaseModel):
    command: str


class ExecuteRequest(BaseModel):
    plan_json: str


class ConfigRequest(BaseModel):
    mode: str = 'LLM'
    reasoning_method: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    use_mock_llm: Optional[bool] = None
    vlm_model_name: Optional[str] = None
    vlm_temperature: Optional[float] = None
    grounding_mode: Optional[str] = None
    reasoning_effort: Optional[str] = None


class StackRequest(BaseModel):
    mode: str = 'LLM'
    mock_robot: bool = True
    mock_camera: bool = True
    reasoning_method: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    use_mock_llm: Optional[bool] = None
    vlm_model_name: Optional[str] = None
    vlm_temperature: Optional[float] = None
    grounding_mode: Optional[str] = None
    reasoning_effort: Optional[str] = None


class DriverRequest(BaseModel):
    robot_ip: Optional[str] = None
    reverse_ip: Optional[str] = None


def create_app(bridge, supervisor, driver, camera=None):
    """Build the FastAPI app, wired to the bridge, stack and UR driver."""
    app = FastAPI(title='RoboReason GUI', version='0.1.0')

    static_dir = os.path.join(
        get_package_share_directory('robo_reason_gui'), 'static'
    )

    @app.on_event('startup')
    async def _capture_loop():
        # Hand the running asyncio loop to the bridge so its ROS callbacks can
        # push /execution_log lines onto the WebSocket queues thread-safely.
        bridge.set_event_loop(asyncio.get_running_loop())

    @app.get('/api/health')
    def health():
        return bridge.health()

    @app.get('/api/options')
    def options():
        return get_options()

    @app.get('/api/preflight')
    def preflight():
        # Detect a duplicate /execute_skill action server before the operator
        # runs a plan (a leftover executor would run every skill twice).
        return bridge.preflight()

    @app.post('/api/plan')
    def plan(req: CommandRequest):
        # Sync route -> FastAPI runs it in a threadpool, so the blocking
        # planning round-trip does not stall the event loop.
        return bridge.plan_command(req.command)

    @app.post('/api/execute')
    def execute(req: ExecuteRequest):
        return bridge.execute_command(req.plan_json)

    @app.post('/api/execute/cancel')
    def execute_cancel():
        # Emergency stop — cancels the in-flight skill, aborts the rest of the
        # plan, and returns the robot home. Sync route -> runs in a threadpool
        # so it isn't blocked behind a stuck /api/execute call on the same loop.
        return bridge.cancel_execution()

    @app.post('/api/config')
    def config(req: ConfigRequest):
        # Live-retune the running planner (B2) — no relaunch.
        return bridge.set_planner_config(req.model_dump())

    @app.get('/api/stack')
    def stack_status():
        return supervisor.status()

    @app.post('/api/stack/start')
    def stack_start(req: StackRequest):
        return supervisor.start(req.mode, req.mock_robot, req.mock_camera, req.model_dump())

    @app.post('/api/stack/stop')
    def stack_stop():
        return supervisor.stop()

    @app.post('/api/stack/restart')
    def stack_restart(req: StackRequest):
        return supervisor.restart(req.mode, req.mock_robot, req.mock_camera, req.model_dump())

    @app.get('/api/driver')
    def driver_status():
        return driver.status()

    @app.post('/api/driver/start')
    def driver_start(req: DriverRequest):
        return driver.start(req.model_dump())

    @app.post('/api/driver/stop')
    def driver_stop():
        return driver.stop()

    @app.post('/api/driver/reconnect')
    def driver_reconnect(req: DriverRequest):
        return driver.reconnect(req.model_dump())

    @app.get('/api/camera/status')
    def camera_status():
        return {'available': bridge.camera_available()}

    @app.get('/api/camera/service')
    def camera_service_status():
        return camera.status() if camera else {'running': False, 'ready': False, 'logs': []}

    @app.post('/api/camera/service/start')
    def camera_service_start():
        return camera.start() if camera else {'ok': False, 'error': 'no camera supervisor'}

    @app.post('/api/camera/service/stop')
    def camera_service_stop():
        return camera.stop() if camera else {'ok': True}

    @app.post('/api/camera/recalibrate')
    def camera_recalibrate():
        return bridge.recalibrate_camera()

    @app.get('/api/camera/frame')
    def camera_frame():
        # Grab the latest frame on demand (the GUI polls this). 503 lets the
        # frontend distinguish "no camera yet" from a hard error.
        jpeg = bridge.grab_camera_jpeg()
        if jpeg is None:
            raise HTTPException(status_code=503, detail='camera frame unavailable')
        return Response(
            content=jpeg,
            media_type='image/jpeg',
            headers={'Cache-Control': 'no-store'},
        )

    @app.websocket('/ws/execution')
    async def execution_log(ws: WebSocket):
        await ws.accept()
        queue = bridge.add_log_subscriber()
        try:
            while True:
                line = await queue.get()
                await ws.send_json({'log': line})
        except WebSocketDisconnect:
            pass
        finally:
            bridge.remove_log_subscriber(queue)

    @app.get('/')
    def index():
        return FileResponse(os.path.join(static_dir, 'index.html'))

    @app.get('/static/{path:path}')
    def static_files(path: str):
        # Serve our own static assets. We deliberately don't use Starlette's
        # StaticFiles mount: with `colcon build --symlink-install` the installed
        # files are symlinks into the build tree, and StaticFiles refuses to
        # follow symlinks that resolve outside the served directory (404). We
        # still guard against URL path traversal by normalising away any '..'.
        safe = os.path.normpath('/' + path).lstrip('/')
        full = os.path.join(static_dir, safe)
        if not os.path.isfile(full):
            raise HTTPException(status_code=404)
        return FileResponse(full)

    return app
