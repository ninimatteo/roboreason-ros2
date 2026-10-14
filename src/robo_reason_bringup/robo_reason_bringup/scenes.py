"""Which scene the stack works on (ROBOAI-49).

A scene is a JSON file in the scene_mock.json schema: the objects and target
zones on the table. The LLM planners plan on it, the simulation builds its
world from it, the GUI sends it with every request. All of them read it
through scene_path(), so they always agree.

settings.SCENE (ROBOREASON_SCENE) picks it: empty for scene_mock.json, the
name of a scene of the library (robo_reason_task_interface/config/scenes),
or the path of any JSON file.
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from robo_reason_bringup.config import settings

DEFAULT_SCENE = 'scene_mock'


def _config_dir() -> Path:
    return Path(get_package_share_directory('robo_reason_task_interface')) / 'config'


def list_scenes() -> list:
    """The default scene first, then the library in alphabetical order."""
    library = sorted(p.stem for p in (_config_dir() / 'scenes').glob('*.json'))
    return [DEFAULT_SCENE] + [name for name in library if name != DEFAULT_SCENE]


def scene_path(scene: str = None) -> Path:
    scene = settings.SCENE if scene is None else scene
    if not scene or scene == DEFAULT_SCENE:
        return _config_dir() / 'scene_mock.json'
    if scene.endswith('.json'):
        return Path(scene).expanduser()
    path = _config_dir() / 'scenes' / f'{scene}.json'
    if not path.exists():
        raise FileNotFoundError(f'no scene named {scene!r}; available: {", ".join(list_scenes())}')
    return path
