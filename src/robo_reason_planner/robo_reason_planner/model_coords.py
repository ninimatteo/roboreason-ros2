"""Image coordinates as a vision model writes them, back to pixels.

Qwen vision models on Groq answer in 0..1000 on BOTH axes relative to the
image WIDTH, whatever the size of the image they were sent (probed
2026-10-14 on a 1280x720 sim frame resized to 640..1280 px wide: the answer
was the same 0..1000 value every time, ROBOAI-43). The agent handles the
same convention in agent/tools.py (_to_pixels).
"""


def resolve(setting: str, model_name: str) -> str:
    """'pixel' or 'norm1000' for a VLM_COORDS setting ('auto' picks by model)."""
    if setting == 'auto':
        return 'norm1000' if 'qwen' in model_name.lower() else 'pixel'
    if setting not in ('pixel', 'norm1000'):
        raise ValueError(f"coords must be 'auto', 'pixel' or 'norm1000', got {setting!r}")
    return setting


def to_pixels(values, image_width: int, coords: str):
    """A point [x, y] or a box [x0, y0, x1, y1] from the model, in pixels."""
    if coords != 'norm1000' or not isinstance(values, (list, tuple)):
        return values
    return [v * image_width / 1000.0 for v in values]
