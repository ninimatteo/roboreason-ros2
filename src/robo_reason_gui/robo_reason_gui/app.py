import os

from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def create_app(bridge):
    """Build the FastAPI app, wired to the given GuiBridgeNode instance."""
    app = FastAPI(title='RoboReason GUI', version='0.1.0')

    static_dir = os.path.join(
        get_package_share_directory('robo_reason_gui'), 'static'
    )

    @app.get('/api/health')
    def health():
        return bridge.health()

    @app.get('/')
    def index():
        return FileResponse(os.path.join(static_dir, 'index.html'))

    app.mount('/static', StaticFiles(directory=static_dir), name='static')
    return app
