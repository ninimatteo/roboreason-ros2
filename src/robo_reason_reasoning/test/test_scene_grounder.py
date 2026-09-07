"""Tests for SceneGrounder.ground_scene's retry-on-blank-response behavior.

SceneGrounder is a one-shot VLM call, not a ReasoningMethod subclass, so it
doesn't go through ReasoningMethod._call_client and needs its own copy of
the same "retry once with a bigger max_tokens budget" recovery (see
reasoning_method.py's _call_client for the original, and the docstring on
ground_scene's retry block for why this call needed it too — a real
VLM_LLM hardware trial crashed on exactly this before the fix).
"""
import json

import pytest

from conftest import ScriptedClient


def _scene_json(target_label="tray"):
    return json.dumps({
        "objects": [{"label": "red_cube", "pixel_center": [100.0, 120.0], "size": [0.05, 0.05, 0.05]}],
        "targets": [{"label": target_label, "pixel_center": [300.0, 200.0], "size": [0.15, 0.15, 0.01]}],
    })


@pytest.fixture
def scripted_vlm_client(monkeypatch):
    """Like conftest's scripted_client, but patches the VLMClient binding
    SceneGrounder actually uses (its own direct import), not the one
    reasoning_method.py uses.
    """
    def _factory(responses):
        client = ScriptedClient(responses)
        monkeypatch.setattr(
            "robo_reason_reasoning.scene_grounder.VLMClient",
            lambda **kwargs: client,
        )
        return client

    return _factory


def test_ground_scene_retries_once_on_blank_response(scripted_vlm_client):
    """A blank force_json response is retried once with a doubled
    max_tokens budget, recovering the detection instead of raising."""
    from robo_reason_reasoning.scene_grounder import SceneGrounder

    client = scripted_vlm_client([
        "",                    # first attempt: blank, triggers retry
        _scene_json("tray"),   # retry: recovers
    ])
    grounder = SceneGrounder(client_parameters={"model_name": "nebius/kimi-k2.6"})

    result = grounder.ground_scene(image=None)

    assert result.targets[0].label == "tray"
    assert len(client.calls) == 2
    # ScriptedClient has no max_tokens attr, so the fallback default (8192,
    # same as ReasoningMethod._call_client's) applies to the base call, and
    # the retry asks for double that.
    assert client.calls[-1]["max_tokens"] == 16384


def test_ground_scene_does_not_retry_a_well_formed_response(scripted_vlm_client):
    """A normal, non-blank response is used as-is -- no retry call at all."""
    from robo_reason_reasoning.scene_grounder import SceneGrounder

    client = scripted_vlm_client([_scene_json("brown_top")])
    grounder = SceneGrounder(client_parameters={"model_name": "nebius/kimi-k2.6"})

    result = grounder.ground_scene(image=None)

    assert result.targets[0].label == "brown_top"
    assert len(client.calls) == 1


def test_ground_scene_raises_when_retry_also_comes_back_blank(scripted_vlm_client):
    """Two blank responses in a row still fail -- the retry is a one-shot
    recovery attempt, not a retry loop."""
    from robo_reason_reasoning.scene_grounder import SceneGrounder

    client = scripted_vlm_client(["", ""])
    grounder = SceneGrounder(client_parameters={"model_name": "nebius/kimi-k2.6"})

    with pytest.raises(ValueError, match="Empty response from model"):
        grounder.ground_scene(image=None)

    assert len(client.calls) == 2
