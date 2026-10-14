import pytest

from robo_reason_planner import model_coords


def test_auto_reads_qwen_answers_as_norm1000():
    assert model_coords.resolve('auto', 'groq/qwen3.8-27b') == 'norm1000'
    assert model_coords.resolve('auto', 'mistral/ministral-14b-latest') == 'pixel'
    assert model_coords.resolve('pixel', 'groq/qwen3.8-27b') == 'pixel'
    with pytest.raises(ValueError):
        model_coords.resolve('percent', 'groq/qwen3.8-27b')


def test_norm1000_is_relative_to_the_width_on_both_axes():
    # what qwen3.8 answered for the red cube at pixel (442, 430) of a 1280x720 frame
    x, y = model_coords.to_pixels([344, 333], 1280, 'norm1000')
    assert abs(x - 442) < 5 and abs(y - 430) < 5
    assert model_coords.to_pixels([100, 200, 300, 400], 1000, 'norm1000') == [100, 200, 300, 400]
    assert model_coords.to_pixels([10, 20], 1280, 'pixel') == [10, 20]
    assert model_coords.to_pixels(None, 1280, 'norm1000') is None
