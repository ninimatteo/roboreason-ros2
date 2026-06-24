import asyncio
import os

from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from robo_reason_gui.options import get_options


class CommandRequest(BaseModel):
    command: str


class ExecuteRequest(BaseModel):
    plan_json: str


def create_app(bridge):
    """Build the FastAPI app, wired to the given GuiBridgeNode instance."""
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

    @app.post('/api/plan')
    def plan(req: CommandRequest):
        # Sync route -> FastAPI runs it in a threadpool, so the blocking
        # planning round-trip does not stall the event loop.
        return bridge.plan_command(req.command)

    @app.post('/api/execute')
    def execute(req: ExecuteRequest):
        return bridge.execute_command(req.plan_json)

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

    app.mount('/static', StaticFiles(directory=static_dir), name='static')
    return app
