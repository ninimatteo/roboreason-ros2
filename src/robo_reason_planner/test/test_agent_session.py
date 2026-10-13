"""The agent loop against a scripted model and a fake robot (no ROS, no API)."""
import json
import threading

import pytest

from robo_reason_planner.agent.geometry import GraspGeometry
from robo_reason_planner.agent.session import AgentCancelled, AgentSession
from robo_reason_planner.agent.tool_chat import ChatReply, ToolCall
from robo_reason_planner.agent.tools import Toolbox, ToolboxConfig

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 16


class FakeBackend:
    """Flat table at z=0, one 4 cm cube at pixel (100, 100) = (0.1, -0.4)."""

    def __init__(self, tmp_path, fail_first_pick=False):
        self.tmp = tmp_path
        self.skills = []
        self.fail_first_pick = fail_first_pick
        self.n = 0

    def capture(self):
        self.n += 1
        path = self.tmp / f'{self.n}.png'
        path.write_bytes(PNG)
        return str(path), 640, 480

    def deproject(self, pixels):
        out = []
        for u, v in pixels:
            on_cube = 80 <= u <= 120 and 80 <= v <= 120
            out.append([0.1 + (u - 100) * 0.001, -0.4 + (v - 100) * 0.001, 0.04 if on_cube else 0.0])
        return out

    def metres_per_pixel(self, point):
        return 0.001, 0.001

    def run_skill(self, name, args):
        self.skills.append((name, args))
        if name == 'pick' and self.fail_first_pick and sum(s[0] == 'pick' for s in self.skills) == 1:
            return False, 'gripper close failed'
        return True, ''


class ScriptedClient:
    """Replays a list of turns; each turn is a list of (tool, args)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def chat(self, messages, tools, tool_choice='auto'):
        self.seen.append(([t['function']['name'] for t in tools], json.loads(json.dumps(messages))))
        calls = [ToolCall(id=f'c{len(self.seen)}_{i}', name=n, arguments=json.dumps(a))
                 for i, (n, a) in enumerate(self.turns.pop(0))]
        msg = {'role': 'assistant', 'content': ''}
        if calls:
            msg['tool_calls'] = [{'id': c.id, 'type': 'function',
                                  'function': {'name': c.name, 'arguments': c.arguments}} for c in calls]
        return ChatReply(content='', tool_calls=calls, message=msg)


def _toolbox(backend, lines):
    cfg = ToolboxConfig(
        table_z=0.0, limits={'x': (-0.6, 0.6), 'y': (-0.8, 0.0), 'z': (-0.03, 0.55)},
        grasp=GraspGeometry(0.5, 0.02, 0.057), send_width=0,
    )
    return Toolbox(backend=backend, config=cfg, log=lines.append)


PLAN = [('submit_plan', {'steps': [{'action': 'pick', 'description': 'pick the cube'},
                                   {'action': 'place', 'description': 'place it'}]})]


def test_draft_then_execute_happy_path(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([
        PLAN,
        [('inspect_object', {'bbox': [85, 85, 115, 115]}), ('inspect_area', {'bbox': [280, 80, 320, 120]})],
        [('pick', {'object': 'o1'})],
        [('place', {'target': 'a1'}), ('report_progress', {'step': 2, 'status': 'done'})],
        [('finish', {'success': True, 'summary': 'cube moved'})],
    ])
    session = AgentSession(client, _toolbox(backend, lines), 'move the cube', log=lines.append)

    steps = session.draft_plan()
    assert [s['action'] for s in steps] == ['pick', 'place']
    assert client.seen[0][0] == ['submit_plan']

    outcome = session.execute()
    assert outcome.success and outcome.summary == 'cube moved'
    assert [s[0] for s in backend.skills] == ['approach', 'pick', 'approach', 'approach', 'release']
    assert backend.skills[1][1]['target_position'] == [0.1, -0.4, 0.02]   # mid body grasp
    assert backend.skills[1][1]['grasp_width'] == 0.03                    # 30 px at 1 mm per pixel
    assert backend.skills[4][1]['object_height'] == 0.02
    assert backend.skills[4][1]['release_position'][:2] == [0.3, -0.4]
    assert '[Step 2] done -> OK' in lines
    # the model gets ids and sizes, no coordinates to copy
    measured = json.loads([m for m in client.seen[2][1] if m['role'] == 'tool'][-2]['content'])
    assert measured['id'] == 'o1' and measured['height_m'] == 0.04 and 'grasp_point' not in measured

def test_failed_pick_is_reported_back_and_retried(tmp_path):
    backend, lines = FakeBackend(tmp_path, fail_first_pick=True), []
    client = ScriptedClient([PLAN, [('inspect_object', {'bbox': [85, 85, 115, 115]})],
                             [('pick', {'object': 'o1'})], [('pick', {'object': 'o1'})],
                             [('finish', {'success': True, 'summary': 'ok'})]])
    session = AgentSession(client, _toolbox(backend, lines), 'pick', log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert outcome.success
    after_fail = [m for m in client.seen[3][1] if m['role'] == 'tool'][-1]['content']
    assert 'gripper close failed' in after_fail
    assert [a[2] for a in outcome.actions] == [False, True]

def test_unsafe_target_is_refused_not_corrected(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([
        PLAN,
        [('inspect_object', {'bbox': [85, 85, 115, 115]})],
        [('pick', {'object': 'o1'})],
        [('finish', {'success': False, 'summary': 'gave up'})],
    ])
    tb = _toolbox(backend, lines)
    tb.config.max_reach_m = 0.3          # the cube sits 0.41 m from the base
    session = AgentSession(client, tb, 'pick', log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert not outcome.success
    assert backend.skills == []
    refused = [m for m in client.seen[3][1] if m['role'] == 'tool'][-1]['content']
    assert refused.startswith('REFUSED') and 'beyond its reach' in refused

def test_only_the_latest_images_stay_in_context(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([PLAN] + [[('look', {})]] * 4 + [[('finish', {'success': True, 'summary': ''})]])
    session = AgentSession(client, _toolbox(backend, lines), 'x', keep_images=2, log=lines.append)
    session.draft_plan()
    session.execute()
    last_messages = client.seen[-1][1]
    images = [p for m in last_messages if isinstance(m['content'], list)
              for p in m['content'] if p.get('type') == 'image_url']
    assert len(images) == 2


def test_model_that_stops_calling_tools_is_nudged_then_given_up(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([PLAN] + [[]] * 4)
    session = AgentSession(client, _toolbox(backend, lines), 'x', max_nudges=3, log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert not outcome.success and 'stopped calling tools' in outcome.summary


def test_cancel_stops_before_the_next_model_call(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([PLAN, [('look', {})]])
    session = AgentSession(client, _toolbox(backend, lines), 'x', log=lines.append)
    session.draft_plan()
    stop = threading.Event()
    stop.set()
    with pytest.raises(AgentCancelled):
        session.execute(cancelled=stop)
    assert len(client.seen) == 1


def test_norm1000_coordinates_are_scaled_to_pixels(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    tb = _toolbox(backend, lines)
    tb.config.coords = 'norm1000'
    tb.capture()
    # both axes relative to the width: 100 px of 640 = 156.25/1000
    res = json.loads(tb.run('inspect_object', {'bbox': [140, 140, 172, 172]}).text)
    assert res['height_m'] == 0.04


def test_actions_after_a_failed_one_in_the_same_turn_are_skipped(tmp_path):
    backend, lines = FakeBackend(tmp_path, fail_first_pick=True), []
    client = ScriptedClient([
        PLAN,
        [('inspect_object', {'bbox': [85, 85, 115, 115]}), ('inspect_area', {'bbox': [280, 80, 320, 120]})],
        [('pick', {'object': 'o1'}), ('place', {'target': 'a1'}),
         ('inspect_object', {'bbox': [85, 85, 115, 115]})],
        [('finish', {'success': False, 'summary': 'stop'})],
    ])
    session = AgentSession(client, _toolbox(backend, lines), 'x', log=lines.append)
    session.draft_plan()
    session.execute()
    assert 'release' not in [s[0] for s in backend.skills]
    results = [m['content'] for m in client.seen[3][1] if m['role'] == 'tool'][-3:]
    assert results[0].startswith('ERROR') and results[1].startswith('SKIPPED')
    assert '"height_m"' in results[2]      # perception still runs

def test_stop_does_not_wait_for_a_slow_model_call(tmp_path):
    import time

    class SlowClient(ScriptedClient):
        def chat(self, messages, tools, tool_choice='auto'):
            if len(self.seen) == 1:
                time.sleep(3)
            return super().chat(messages, tools, tool_choice)

    backend, lines = FakeBackend(tmp_path), []
    client = SlowClient([PLAN, [('look', {})]])
    session = AgentSession(client, _toolbox(backend, lines), 'x', log=lines.append)
    session.draft_plan()
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    with pytest.raises(AgentCancelled):
        session.execute(cancelled=stop)
    assert time.monotonic() - t0 < 1.5


def test_downscaled_image_coordinates_map_back_to_full_resolution(tmp_path):
    from PIL import Image

    class RealImageBackend(FakeBackend):
        def capture(self):
            self.n += 1
            path = self.tmp / f'{self.n}.png'
            Image.new('RGB', (640, 480)).save(path)
            return str(path), 640, 480

    backend, lines = RealImageBackend(tmp_path), []
    tb = _toolbox(backend, lines)
    tb.config.send_width = 320
    sent = tb.capture()
    assert Image.open(sent).size == (320, 240) and tb.image_size == (320, 240)
    # the cube spans 80..120 px at full resolution = 40..60 in the sent image
    res = json.loads(tb.run('inspect_object', {'bbox': [43, 43, 57, 57]}).text)
    assert res['height_m'] == 0.04
    assert res['width_m'] == 0.028        # 14 px sent = 28 px captured, 1 mm each


def test_old_turns_are_compacted_but_stay_paired(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    long_args = {'steps': [{'action': 'x', 'description': 'y' * 300}], 'reason': 'z'}
    client = ScriptedClient([PLAN] + [[('update_plan', long_args)]] * 6
                            + [[('finish', {'success': True, 'summary': ''})]])
    session = AgentSession(client, _toolbox(backend, lines), 'x', history_turns=2, log=lines.append)
    session.draft_plan()
    session.execute()
    msgs = client.seen[-1][1]
    tool_ids = [m['tool_call_id'] for m in msgs if m['role'] == 'tool']
    call_ids = [c['id'] for m in msgs if m['role'] == 'assistant' for c in m.get('tool_calls', [])]
    assert tool_ids == call_ids                    # protocol intact
    plan_msgs = [m for m in msgs if m['role'] == 'user' and isinstance(m['content'], str)]
    assert all('compacted' not in m for m in msgs)  # nothing provider specific leaks


def test_token_budget_stops_the_task(tmp_path):
    backend, lines = FakeBackend(tmp_path), []

    class CostlyClient(ScriptedClient):
        def chat(self, messages, tools, tool_choice='auto'):
            reply = super().chat(messages, tools, tool_choice)
            reply.input_tokens = 5000
            return reply

    client = CostlyClient([PLAN] + [[('look', {})]] * 10)
    session = AgentSession(client, _toolbox(backend, lines), 'x', max_input_tokens=12000, log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert not outcome.success and 'token budget' in outcome.summary
    assert len(client.seen) == 3


def test_pacer_waits_for_the_token_window(monkeypatch):
    from robo_reason_planner.agent import tool_chat
    clock = {'t': 100.0}
    slept = []
    monkeypatch.setattr(tool_chat.time, 'monotonic', lambda: clock['t'])
    monkeypatch.setattr(tool_chat.time, 'sleep', lambda s: (slept.append(s), clock.__setitem__('t', clock['t'] + s)))
    c = tool_chat.ToolChatClient.__new__(tool_chat.ToolChatClient)
    c.tpm_limit, c._window, c._last_request_tokens = 8000, tool_chat.collections.deque(), 3000
    c._window.extend([(90.0, 3000), (95.0, 3000)])
    c._pace()          # 6000 used + ~3750 needed > 8000: waits for the first to expire
    assert slept and clock['t'] >= 150.0


def test_the_same_refused_call_three_times_ends_the_task(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    bad = ('pick', {'object': 'o9'})      # never measured
    client = ScriptedClient([PLAN, [bad], [bad], [bad], [('look', {})]])
    session = AgentSession(client, _toolbox(backend, lines), 'x', log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert not outcome.success and 'failed 3 times' in outcome.summary
    assert 'unknown id' in outcome.summary
    assert len(client.seen) == 4

