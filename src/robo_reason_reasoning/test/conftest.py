"""Shared fixtures for the reasoning-method test suite.

These tests exercise the *parsing/control-flow* layer of every reasoning
method (fhp, ffhp, react, cot_sc, tot, always_act, self_refine) against
scripted, deterministic LLM responses instead of real provider calls.

Why not call real providers here: reasoning-model sampling variance (see
docs/council-transcript-20260707-124140.md, part C) means a real LLM
response can't serve as a stable "known ground truth" — the same prompt can
legitimately produce two different, both-correct plans. Scripting the
client's response lets us assert deterministically that each reasoning
method (a) builds a valid prompt, (b) parses a well-formed response into the
right sequence of UR5Action objects, and (c) behaves as currently
implemented — bugs included — when the response is malformed. Provider wire
-format differences (message shape, force_json handling per provider) are
covered separately in test_llm_client_providers.py against mocked SDK
clients, still without any network access.
"""
import json

import pytest


class ScriptedClient:
    """Drop-in replacement for LLMClient/VLMClient.

    Returns one scripted response per call, in order. Each scripted item is
    either a raw string (the client's usual return type) or an Exception
    instance, which is raised instead of returned.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.usage_metrics = None

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                f"ScriptedClient: ran out of scripted responses after {len(self.calls)} calls"
            )
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_total_usage(self):
        return {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}


def plan_response(actions: list) -> str:
    """A canned {"plan": [...]} response, as fhp/ffhp/tot/self_refine expect."""
    return json.dumps({"plan": actions})


# A minimal, deterministic "ground truth" scene + plan used across tests:
# one red cube to pick, one blue box to place it in.
SIMPLE_SCENE_JSON = json.dumps({
    "objects": [{"label": "red_cube", "position": [0.30, 0.10, 0.03], "size": [0.05, 0.05, 0.05]}],
    "targets": [{"label": "blue_box", "position": [0.50, -0.10, 0.02]}],
})

SIMPLE_USER_REQUEST = "Pick the red cube and place it in the blue box"

EXPECTED_PLAN = [
    {"action_name": "approach", "target_position": [0.30, 0.10, 0.20]},
    {"action_name": "pick", "target_position": [0.30, 0.10, 0.03], "grasp_width": 0.03},
    {"action_name": "approach", "target_position": [0.50, -0.10, 0.20]},
    {"action_name": "release", "release_position": [0.50, -0.10, 0.10]},
]

# Reproduces the exact malformed-field shape observed in the debug folders
# (docs/council-transcript-20260707-124140.md, code-level fact #3): a
# reasoning-model sometimes mashes two numbers together into one string with
# a missing separator/comma.
MALFORMED_ACTION = {"action_name": "approach", "target_position": [-0.50, "-0.50.03", 0.20]}


@pytest.fixture
def scripted_client(monkeypatch):
    """Patch reasoning_method.LLMClient/VLMClient to return scripted responses.

    Usage: client = scripted_client(["resp1", "resp2", ...])
    Returns the ScriptedClient instance so tests can inspect client.calls.
    """
    created = {}

    def _factory(responses):
        client = ScriptedClient(responses)
        monkeypatch.setattr(
            "robo_reason_reasoning.reasoning_method.LLMClient",
            lambda **kwargs: client,
        )
        monkeypatch.setattr(
            "robo_reason_reasoning.reasoning_method.VLMClient",
            lambda **kwargs: client,
        )
        created["client"] = client
        return client

    return _factory


@pytest.fixture
def base_kwargs():
    """Common constructor kwargs shared by every reasoning method."""
    return dict(
        client_parameters={"model_name": "groq/openai-oss-120b"},
        client_type="llm",
        skills="approach, pick, release, move_home, wait",
        action_placeholder='{"action_name": "approach", "target_position": [0,0,0]}',
        verbose=False,
    )
