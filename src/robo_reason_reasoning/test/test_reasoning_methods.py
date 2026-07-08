"""Deterministic control-flow tests for all 6 reasoning methods.

Each test scripts the LLM client's response (see conftest.scripted_client)
so the "ground truth" being checked is not model output quality but this
package's own parsing/dispatch logic: given a well-formed response, does the
method hand back the right sequence of UR5Action objects; given a malformed
one, does it fail the way the code now behaves (see
docs/council-transcript-20260707-124140.md for the original diagnosis).

Regression tests here assert current, fixed behavior:
- A malformed LLM action field (e.g. a mashed-together number like
  '-0.50.03') now raises a clear ActionParsingError instead of a bare,
  hard-to-place pydantic.ValidationError.
- An empty LLM/VLM response (e.g. a reasoning-heavy model exhausting its
  max_tokens budget on internal chain-of-thought before emitting the final
  JSON) now raises a clear ResponseParsingError instead of a bare
  json.JSONDecodeError('Expecting value: line 1 column 1 (char 0)').
- ToT's early-exit bug is fixed: the search keeps expanding live
  (non-terminal) beam candidates for the full iteration budget instead of
  bailing out the instant any single top-beam candidate looks like
  idle/move_home, and returns the best-scoring complete chain found.
If either behavior changes again, update the corresponding test rather than
deleting it — that's what keeps this suite useful as future fixes land.
"""
import json

import pytest

from robo_reason_reasoning.fhp_ffhp import FHP
from robo_reason_reasoning.react import React
from robo_reason_reasoning.cot_sc import CoTSC
from robo_reason_reasoning.always_act import StepAction
from robo_reason_reasoning.self_refine import SelfRefine
from robo_reason_reasoning.tot import TreeOfThought
from robo_reason_reasoning.reasoning_method import ActionParsingError, ResponseParsingError

from conftest import (
    plan_response,
    SIMPLE_SCENE_JSON,
    SIMPLE_USER_REQUEST,
    EXPECTED_PLAN,
    MALFORMED_ACTION,
)


def _dispense_all(agent, n_actions):
    """Call agent(...) n_actions times and return the list of actions."""
    actions = []
    for _ in range(n_actions):
        result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
        assert result.end_of_simulation is False
        actions.append(result.action)
    return actions


# ---------------------------------------------------------------------------
# fhp / ffhp
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reasoning_mode", ["fhp", "ffhp"])
def test_fhp_ffhp_dispenses_full_plan_then_move_home(scripted_client, base_kwargs, reasoning_mode):
    scripted_client([
        "no relevant predicates",       # predict_predicates() raw response
        plan_response(EXPECTED_PLAN),   # plan_task() response
    ])
    agent = FHP(reasoning_mode=reasoning_mode, predicates="", **base_kwargs)

    actions = _dispense_all(agent, len(EXPECTED_PLAN))
    assert [a.action_name for a in actions] == [p["action_name"] for p in EXPECTED_PLAN]

    final = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert final.action.action_name == "move_home"
    assert final.end_of_simulation is True


def test_fhp_and_ffhp_share_the_same_prompt_template():
    """Characterizes council finding #1: get_llm_prompts() takes no mode
    argument at all, so fhp and ffhp are, today, the same prompt — FFHP's
    only mode-specific branch (force_replan) is never triggered by
    agent_runner.run_plan_loop. This test should start failing the day
    FFHP's prompt/replanning is actually differentiated.
    """
    from robo_reason_reasoning.EmbodiedAgentsPrompts.fhp_ffhp_prompts import FHP_FFHP_Prompts
    import inspect

    sig = inspect.signature(FHP_FFHP_Prompts.get_llm_prompts)
    assert "mode" not in sig.parameters and "reasoning_mode" not in sig.parameters


def test_fhp_task_planning_prompt_includes_predicted_predicates(scripted_client, base_kwargs):
    """Guards the fix for the dead predict_predicates() call: the task
    planning prompt must actually embed the predicted predicates string
    (matching RoboReason-Lab's upstream fhp_ffhp_prompts.py), not silently
    discard it via str.format()'s unused-kwarg behavior.
    """
    scripted_client([
        "Contact(red_cube, table)",
        plan_response(EXPECTED_PLAN),
    ])
    agent = FHP(reasoning_mode="fhp", predicates="Contact, Inside", **base_kwargs)
    agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)

    plan_call_kwargs = agent.client.calls[-1]
    assert "Contact(red_cube, table)" in plan_call_kwargs["user_message"]


@pytest.mark.parametrize("grounding_mode", ["point", "bbox"])
def test_fhp_predicates_step_works_in_vlm_mode(scripted_client, base_kwargs, grounding_mode):
    """Regression for the real-world crash: PredicatesPrompts.get_vlm_prompts()
    didn't accept grounding_mode, but ReasoningMethod._select_prompts() always
    passes it, so predict_predicates() (called before plan_task() in FHP)
    raised TypeError for every VLM-mode fhp/ffhp run, regardless of
    grounding_mode.
    """
    scripted_client([
        "no relevant predicates",
        plan_response(EXPECTED_PLAN),
    ])
    vlm_kwargs = dict(base_kwargs)
    vlm_kwargs["client_type"] = "vlm"
    agent = FHP(reasoning_mode="fhp", predicates="", grounding_mode=grounding_mode, **vlm_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON, image="fake.jpg")
    assert result.action.action_name == EXPECTED_PLAN[0]["action_name"]


def test_fhp_crashes_on_malformed_llm_field(scripted_client, base_kwargs):
    """Malformed LLM field at fhp_ffhp.py:96 now raises ActionParsingError."""
    scripted_client([
        "no relevant predicates",
        plan_response([MALFORMED_ACTION]),
    ])
    agent = FHP(reasoning_mode="fhp", predicates="", **base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_fhp_raises_clear_error_on_empty_llm_response(scripted_client, base_kwargs):
    """Regression for the real-world crash: plan_task() raised a bare
    json.decoder.JSONDecodeError('Expecting value: line 1 column 1 (char 0)')
    when the LLM/VLM returned an empty string — which happens when a
    reasoning-heavy model (e.g. Groq's qwen3.6) exhausts its max_tokens
    budget on internal chain-of-thought before ever emitting the final JSON
    plan. Now retries once with a larger max_tokens budget (see
    test_call_client_retries_once_on_blank_force_json_response), and only
    raises a diagnosable ResponseParsingError if that retry is also blank —
    hence two empty scripted responses here, not one.
    """
    scripted_client([
        "no relevant predicates",
        "",
        "",
    ])
    agent = FHP(reasoning_mode="fhp", predicates="", **base_kwargs)
    with pytest.raises(ResponseParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


# ---------------------------------------------------------------------------
# react
# ---------------------------------------------------------------------------

def test_react_action_decision_parses_into_action(scripted_client, base_kwargs):
    scripted_client([
        json.dumps({
            "react_decision": "action",
            "action": EXPECTED_PLAN[0],
            "end_of_simulation": False,
        }),
    ])
    agent = React(**base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == EXPECTED_PLAN[0]["action_name"]
    assert result.end_of_simulation is False


def test_react_reasoning_decision_yields_wait_action(scripted_client, base_kwargs):
    scripted_client([
        json.dumps({
            "react_decision": "reasoning",
            "reasoning": "Scanning the scene before acting.",
            "end_of_simulation": False,
        }),
    ])
    agent = React(**base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == "wait"


def test_react_crashes_on_malformed_llm_field(scripted_client, base_kwargs):
    """Malformed LLM field at react.py:63 now raises ActionParsingError."""
    scripted_client([
        json.dumps({
            "react_decision": "action",
            "action": MALFORMED_ACTION,
            "end_of_simulation": False,
        }),
    ])
    agent = React(**base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_react_raises_clear_error_on_empty_llm_response(scripted_client, base_kwargs):
    """react_step()'s json.loads() at react.py:55 now raises a diagnosable
    ResponseParsingError instead of a bare JSONDecodeError on an empty
    (max_tokens-exhausted) LLM response — only once the retry-once-with-
    higher-budget path (see _call_client) also comes back blank.
    """
    scripted_client(["", ""])
    agent = React(**base_kwargs)
    with pytest.raises(ResponseParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


# ---------------------------------------------------------------------------
# always_act
# ---------------------------------------------------------------------------

def test_always_act_parses_single_action(scripted_client, base_kwargs):
    scripted_client([
        json.dumps({"action": EXPECTED_PLAN[1], "end_of_simulation": False}),
    ])
    agent = StepAction(**base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == EXPECTED_PLAN[1]["action_name"]
    assert result.action.grasp_width == pytest.approx(EXPECTED_PLAN[1]["grasp_width"])


def test_always_act_crashes_on_malformed_llm_field(scripted_client, base_kwargs):
    """Malformed LLM field at always_act.py:55 now raises ActionParsingError."""
    scripted_client([
        json.dumps({"action": MALFORMED_ACTION, "end_of_simulation": False}),
    ])
    agent = StepAction(**base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_always_act_raises_clear_error_on_empty_llm_response(scripted_client, base_kwargs):
    """step_action()'s json.loads() at always_act.py:53 now raises a
    diagnosable ResponseParsingError instead of a bare JSONDecodeError on an
    empty (max_tokens-exhausted) LLM response — only once the retry-once-
    with-higher-budget path (see _call_client) also comes back blank.
    """
    scripted_client(["", ""])
    agent = StepAction(**base_kwargs)
    with pytest.raises(ResponseParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


# ---------------------------------------------------------------------------
# cot_sc
# ---------------------------------------------------------------------------

def test_cot_sc_majority_vote_dispenses_full_plan(scripted_client, base_kwargs):
    k = 2
    scripted_client([plan_response(EXPECTED_PLAN) for _ in range(k)])
    agent = CoTSC(k=k, **base_kwargs)

    actions = _dispense_all(agent, len(EXPECTED_PLAN))
    assert [a.action_name for a in actions] == [p["action_name"] for p in EXPECTED_PLAN]


def test_cot_sc_raises_when_every_sample_fails_to_parse(scripted_client, base_kwargs):
    """Guards the fix already applied for the 'silent move_home' bug: if all
    k samples come back unparseable, CoT-SC must raise, not fabricate a
    confident move_home/end_of_simulation=True result.
    """
    k = 2
    scripted_client(["not valid json" for _ in range(k)])
    agent = CoTSC(k=k, **base_kwargs)
    with pytest.raises(RuntimeError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_cot_sc_crashes_on_malformed_llm_field(scripted_client, base_kwargs):
    """Malformed LLM field when dispensing the selected plan
    (cot_sc.py, self._build_action(self.task_plan[0])) now raises
    ActionParsingError.
    """
    k = 2
    scripted_client([plan_response([MALFORMED_ACTION]) for _ in range(k)])
    agent = CoTSC(k=k, **base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_cot_sc_crashes_on_non_dict_action_in_plan(scripted_client, base_kwargs):
    """Reproduces a real live-hardware crash: the VLM's own "plan" JSON array
    contained a raw int as one of its entries (not a well-formed action
    dict). select_consistent_plan() picks a raw per-sample plan directly, so
    that stray int reaches _build_action(self.task_plan[0]) at cot_sc.py's
    dispense site. Previously this raised a bare, hard-to-place
    `TypeError: UR5Action() argument after ** must be a mapping, not int`
    from `UR5Action(**action_dict)`'s `**` unpacking (not a
    pydantic.ValidationError, so the old except clause didn't catch it).
    _build_action's isinstance(action_dict, dict) guard now raises a clear
    ActionParsingError instead.
    """
    k = 2
    scripted_client([plan_response([3]) for _ in range(k)])
    agent = CoTSC(k=k, **base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


# ---------------------------------------------------------------------------
# self_refine
# ---------------------------------------------------------------------------

def test_self_refine_stops_when_feedback_is_satisfactory(scripted_client, base_kwargs):
    scripted_client([
        plan_response(EXPECTED_PLAN),               # generate_initial_solution
        json.dumps({"is_satisfactory": True}),       # generate_feedback -> stop immediately
    ])
    agent = SelfRefine(max_iterations=3, **base_kwargs)

    actions = _dispense_all(agent, len(EXPECTED_PLAN))
    assert [a.action_name for a in actions] == [p["action_name"] for p in EXPECTED_PLAN]


def test_self_refine_crashes_on_malformed_llm_field(scripted_client, base_kwargs):
    """Malformed LLM field at self_refine.py:157 now raises ActionParsingError."""
    scripted_client([
        plan_response([MALFORMED_ACTION]),
        json.dumps({"is_satisfactory": True}),
    ])
    agent = SelfRefine(max_iterations=3, **base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


# ---------------------------------------------------------------------------
# tot
# ---------------------------------------------------------------------------

def _tot_thought_response(*action_dicts):
    return json.dumps({"sampled_actions": list(action_dicts)})


def _tot_excellent_eval_response():
    return json.dumps({
        "user_request_consistency": "excellent",
        "environment_feasibility": "excellent",
        "embodiment_feasibility": "excellent",
    })


def test_tot_dispenses_single_action_plan(scripted_client, base_kwargs):
    scripted_client([
        _tot_thought_response(EXPECTED_PLAN[0]),
        _tot_excellent_eval_response(),
    ])
    agent = TreeOfThought(k=1, b=1, t=1, **base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == EXPECTED_PLAN[0]["action_name"]


def test_tot_keeps_exploring_live_beam_candidates_past_a_terminal_one(scripted_client, base_kwargs):
    """Fix for council finding #4 (tot.py's early-exit bug): previously, the
    search returned as soon as ANY top-beam candidate's action_name was
    'idle'/'move_home', discarding still-live sibling candidates and the
    remaining iteration budget. With b=2/k=2, each iteration samples one
    live (non-terminal) candidate alongside one terminal (move_home)
    candidate; the fix must keep expanding the live candidate for the full
    t=3 budget instead of bailing out on the first terminal sighting, and
    must return the best-scoring complete chain found (here, the 3rd
    iteration's 'approach'->'pick'->'move_home' chain, which scores higher
    than the near-empty 1st-iteration 'move_home' chain).
    """
    approach = {"action_name": "approach", "target_position": [0.3, 0.1, 0.2]}
    pick = {"action_name": "pick", "target_position": [0.3, 0.1, 0.03], "grasp_width": 0.03}
    release = {"action_name": "release", "release_position": [0.5, -0.1, 0.1]}

    def _mediocre_eval_response():
        return json.dumps({
            "user_request_consistency": "fair",
            "environment_feasibility": "fair",
            "embodiment_feasibility": "fair",
        })

    client = scripted_client([
        # iteration 1: [approach (live), move_home (terminal, mediocre)]
        _tot_thought_response(approach, {"action_name": "move_home"}),
        _tot_excellent_eval_response(),   # approach: excellent
        _mediocre_eval_response(),        # move_home: mediocre
        # iteration 2: expand approach -> [pick (live), move_home (terminal, weak)]
        _tot_thought_response(pick, {"action_name": "move_home"}),
        _tot_excellent_eval_response(),   # pick: excellent
        _mediocre_eval_response(),        # move_home: mediocre
        # iteration 3 (last): expand pick -> [release (live), move_home (terminal, best)]
        _tot_thought_response(release, {"action_name": "move_home"}),
        _tot_excellent_eval_response(),   # release: excellent
        _tot_excellent_eval_response(),   # move_home: excellent -> new best terminal
    ])
    agent = TreeOfThought(k=2, b=2, t=3, **base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)

    # All 3 budgeted iterations were used (1 generate + 2 evaluate each).
    assert len(client.calls) == 9
    # The richer chain built on top of the iteration-3 terminal candidate
    # wins over the near-empty iteration-1 one.
    assert result.action.action_name == "approach"
    assert [a["action_name"] for a in agent.task_plan] == ["pick"]


def test_tot_crashes_on_malformed_llm_field(scripted_client, base_kwargs):
    """Malformed LLM field at tot.py:184 now raises ActionParsingError."""
    scripted_client([
        _tot_thought_response(MALFORMED_ACTION),
        _tot_excellent_eval_response(),
    ])
    agent = TreeOfThought(k=1, b=1, t=1, **base_kwargs)
    with pytest.raises(ActionParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_tot_raises_clear_error_on_empty_generate_response(scripted_client, base_kwargs):
    """_generate_action_thought()'s json.loads() at tot.py:69 now raises a
    diagnosable ResponseParsingError instead of a bare JSONDecodeError on an
    empty (max_tokens-exhausted) LLM response — only once the retry-once-
    with-higher-budget path (see _call_client) also comes back blank.
    """
    scripted_client(["", ""])
    agent = TreeOfThought(k=1, b=1, t=1, **base_kwargs)
    with pytest.raises(ResponseParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


def test_tot_raises_clear_error_on_empty_evaluate_response(scripted_client, base_kwargs):
    """_evaluate_thought()'s json.loads() at tot.py:90 now raises a
    diagnosable ResponseParsingError instead of a bare JSONDecodeError on an
    empty (max_tokens-exhausted) LLM response — only once the retry-once-
    with-higher-budget path (see _call_client) also comes back blank.
    """
    scripted_client([
        _tot_thought_response(EXPECTED_PLAN[0]),
        "",
        "",
    ])
    agent = TreeOfThought(k=1, b=1, t=1, **base_kwargs)
    with pytest.raises(ResponseParsingError):
        agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)


# ---------------------------------------------------------------------------
# reasoning_method: retry-once-with-higher-budget / robust JSON extraction
# ---------------------------------------------------------------------------

def test_call_client_retries_once_on_blank_force_json_response(scripted_client, base_kwargs):
    """A blank force_json response (max_tokens exhausted on <think> reasoning
    before any JSON was emitted) is retried once, automatically, with a
    doubled max_tokens budget — recovering the plan instead of failing the
    whole reasoning method on a single transient empty response.
    """
    client = scripted_client([
        "no relevant predicates",       # predict_predicates() — non-blank, no retry
        "",                              # plan_task() first attempt — blank, triggers retry
        plan_response(EXPECTED_PLAN),    # plan_task() retry — recovers
    ])
    agent = FHP(reasoning_mode="fhp", predicates="", **base_kwargs)

    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == EXPECTED_PLAN[0]["action_name"]
    assert len(client.calls) == 3
    # The retried call asked for a larger max_tokens budget than the base
    # client default (8192, ScriptedClient has no max_tokens attr so the
    # fallback default applies).
    assert client.calls[-1]["max_tokens"] == 16384


def test_call_client_does_not_retry_a_well_formed_response(scripted_client, base_kwargs):
    """No retry fires when the first response already parses — the retry
    path must be a no-op in the common/happy case.
    """
    scripted_client([
        "no relevant predicates",
        plan_response(EXPECTED_PLAN),
    ])
    agent = FHP(reasoning_mode="fhp", predicates="", **base_kwargs)
    agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert len(agent.client.calls) == 2


def test_extract_json_strips_think_blocks_and_leading_prose(scripted_client, base_kwargs):
    """_extract_json (ported from a colleague's more battle-tested VLMClient)
    recovers a well-formed plan even when the model wraps it in a Qwen3-style
    <think>...</think> block and/or leading prose — cases the previous bare
    fence-strip + json.loads couldn't handle.
    """
    contaminated = (
        "<think>let me plan this out step by step...</think>"
        "Here is the plan:\n" + plan_response(EXPECTED_PLAN)
    )
    scripted_client([
        "no relevant predicates",
        contaminated,
    ])
    agent = FHP(reasoning_mode="fhp", predicates="", **base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == EXPECTED_PLAN[0]["action_name"]


def test_is_blank_response_treats_pure_think_block_as_blank():
    """A response that is entirely a <think> block (nothing left after
    stripping it) is treated as blank — the exact failure mode observed in
    the real groq/qwen3.6-27b JSONDecodeError crash.
    """
    from robo_reason_reasoning.reasoning_method import ReasoningMethod

    assert ReasoningMethod._is_blank_response("<think>only reasoning, no answer</think>") is True
    assert ReasoningMethod._is_blank_response("") is True
    assert ReasoningMethod._is_blank_response(None) is True
    assert ReasoningMethod._is_blank_response('<think>reasoning</think>{"plan": []}') is False


def test_is_blank_response_covers_unclosed_think_block():
    """An *unclosed* <think> block (max_tokens ran out mid-reasoning, before
    the model ever closed the tag or emitted JSON) is the exact real-world
    shape of the fhp/groq/qwen3.6-27b truncation bug ("Pick the blue
    cube..."). _strip_think_blocks' original regex required a closing tag,
    so this huge raw dump used to look non-blank and skip the retry path
    entirely; _UNCLOSED_THINK_BLOCK_RE now truncates it to empty too.
    """
    from robo_reason_reasoning.reasoning_method import ReasoningMethod

    unclosed_think_dump = (
        "\n<think>\nLet me find the pixel coordinates. x is roughly 40% "
        "from left -> 512. Continuing to reason about the box position "
        "without ever closing the think tag or emitting JSON..."
    )
    assert ReasoningMethod._is_blank_response(unclosed_think_dump) is True
    # Sanity: well-formed JSON (with or without a closed think block) is
    # still correctly treated as non-blank.
    assert ReasoningMethod._is_blank_response(plan_response(EXPECTED_PLAN)) is False
    assert ReasoningMethod._is_blank_response(
        '<think>reasoning</think>' + plan_response(EXPECTED_PLAN)
    ) is False


def test_call_client_retries_once_on_unclosed_think_block(scripted_client, base_kwargs):
    """End-to-end: an unclosed-<think>-block first attempt (no JSON anywhere,
    tag never closed) now triggers the same retry-once-with-higher-budget
    path as a plain blank response, recovering the plan on the second try —
    this is the scenario from the user's real "before the last fixes" crash.
    """
    unclosed_think_dump = "<think>\nreasoning about pixel coords, x -> 512, never closes..."
    client = scripted_client([
        "no relevant predicates",
        unclosed_think_dump,
        plan_response(EXPECTED_PLAN),
    ])
    agent = FHP(reasoning_mode="fhp", predicates="", **base_kwargs)
    result = agent(user_request=SIMPLE_USER_REQUEST, environment_map=SIMPLE_SCENE_JSON)
    assert result.action.action_name == EXPECTED_PLAN[0]["action_name"]
    assert len(client.calls) == 3
    assert client.calls[-1]["max_tokens"] == 16384
