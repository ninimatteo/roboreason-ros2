import os

from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from robo_reason_gui.options import get_options


class CommandRequest(BaseModel):
    command: str


def create_app(bridge):
    """Build the FastAPI app, wired to the given GuiBridgeNode instance."""
    app = FastAPI(title='RoboReason GUI', version='0.1.0')

    static_dir = os.path.join(
        get_package_share_directory('robo_reason_gui'), 'static'
    )

    @app.get('/api/health')
    def health():
        return bridge.health()

    @app.get('/api/options')
    def options():
        return get_options()

    @app.post('/api/command')
    def command(req: CommandRequest):
        # Sync route -> FastAPI runs it in a threadpool, so the blocking
        # plan/execute round-trip does not stall the event loop.
        return bridge.run_command(req.command)

    @app.get('/')
    def index():
        return FileResponse(os.path.join(static_dir, 'index.html'))

    app.mount('/static', StaticFiles(directory=static_dir), name='static')
    return app
