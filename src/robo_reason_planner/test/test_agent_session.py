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
            # the room floor, 0.75 m below the table, shows past the table edge
            z = 0.04 if on_cube else (-0.75 if v > 400 else 0.0)
            out.append([0.1 + (u - 100) * 0.001, -0.4 + (v - 100) * 0.001, z])
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
        grasp=GraspGeometry(0.5, 0.02, 0.057), send_width=0, verify_actions=False,
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
    grasp = backend.skills[1][1]['target_position']                      # mid body grasp
    assert max(abs(a - b) for a, b in zip(grasp, [0.1, -0.4, 0.02])) <= 0.002
    # the box (30 px) snaps to the cube's real extent: 41 px, sampled every 4 px
    assert abs(backend.skills[1][1]['grasp_width'] - 0.041) <= 0.004
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
    # the box snaps to the cube (41 px captured, 1 mm each), whatever its size
    assert abs(res['width_m'] - 0.041) <= 0.004


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



def test_a_box_that_misses_reports_empty_and_names_the_nearest_object(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    tb = _toolbox(backend, lines)
    tb.capture()
    # the cube spans 80..120 px; this box is beside it, on the table
    res = json.loads(tb.run('inspect_object', {'bbox': [125, 90, 145, 110]}).text)
    assert res['looks_empty'] is True
    near = res['nearest_object']
    assert near['height_m'] == 0.04 and 76 <= near['bbox'][0] <= 84 and 116 <= near['bbox'][2] <= 124
    assert tb.run('pick', {'object': near['id']}).text.startswith('pick completed')
    # far from anything: empty, no suggestion
    res = json.loads(tb.run('inspect_object', {'bbox': [300, 300, 320, 320]}).text)
    assert res['looks_empty'] and 'nearest_object' not in res

def test_detect_objects_finds_ids_colours_and_sizes(tmp_path):
    from PIL import Image, ImageDraw

    class PaintedBackend(FakeBackend):
        def capture(self):
            self.n += 1
            path = self.tmp / f'{self.n}.png'
            im = Image.new('RGB', (640, 480), (225, 225, 220))
            ImageDraw.Draw(im).rectangle([80, 80, 120, 120], fill=(220, 40, 40))
            im.save(path)
            return str(path), 640, 480

    backend, lines = PaintedBackend(tmp_path), []
    tb = _toolbox(backend, lines)
    tb.capture()
    found = json.loads(tb.run('detect_objects', {}).text)
    assert len(found) == 1
    o = found[0]
    assert o['id'] == 'o1' and o['kind'] == 'object' and o['color'] == 'red'
    assert o['height_m'] == 0.04 and abs(o['width_m'] - 0.045) <= 0.006
    # and the id works straight away
    assert tb.run('pick', {'object': 'o1'}).text.startswith('pick completed')


def test_colour_names():
    from robo_reason_planner.agent.geometry import color_name
    assert color_name((242, 76, 0)) == 'orange'
    assert color_name((170, 125, 80)) == 'brown'
    assert color_name((60, 110, 230)) == 'blue'
    assert color_name((245, 245, 245)) == 'white'
    assert color_name((220, 40, 40)) == 'red'


class LiveBackend(FakeBackend):
    """A cube that really moves: picked when the gripper closes on it (unless
    the grasp slips), carried, and set down where the gripper opens."""

    def __init__(self, tmp_path, slip=False, drop_in_transit=False):
        super().__init__(tmp_path)
        self.cube = (100, 100)        # pixel centre; None while held
        self.slip, self.drop = slip, drop_in_transit
        self.tcp = None

    def deproject(self, pixels):
        out = []
        for u, v in pixels:
            on = self.cube is not None and abs(u - self.cube[0]) <= 20 and abs(v - self.cube[1]) <= 20
            out.append([0.1 + (u - 100) * 0.001, -0.4 + (v - 100) * 0.001, 0.04 if on else 0.0])
        return out

    def run_skill(self, name, args):
        self.skills.append((name, args))
        if name == 'pick':
            if self.slip:
                self.slip = False
            else:
                self.cube = None
        elif name == 'release' and self.cube is None:
            x, y = args['release_position'][:2]
            if self.drop:
                self.cube = (300, 300)      # fell somewhere on the way
            else:
                self.cube = (round(100 + (x - 0.1) / 0.001), round(100 + (y + 0.4) / 0.001))
        return True, ''


def _live_toolbox(backend, lines):
    tb = _toolbox(backend, lines)
    tb.config.verify_actions = True
    return tb


def test_verified_pick_and_place(tmp_path):
    backend, lines = LiveBackend(tmp_path), []
    tb = _live_toolbox(backend, lines)
    tb.capture()
    tb.run('detect_objects', {})
    tb.run('inspect_area', {'bbox': [180, 80, 220, 120]})            # a1, empty table at (0.2, -0.4)
    res = tb.run('pick', {'object': 'o1'}).text
    assert res.startswith('pick completed and checked') and 'held' in res
    res = tb.run('place', {'target': 'a1'}).text
    assert res.startswith('place completed and checked') and 'Objects now' in res
    assert backend.cube == (200, 100)


def test_a_slipped_grasp_is_caught_by_the_check(tmp_path):
    backend, lines = LiveBackend(tmp_path, slip=True), []
    tb = _live_toolbox(backend, lines)
    tb.capture()
    tb.run('detect_objects', {})
    res = tb.run('pick', {'object': 'o1'}).text
    assert res.startswith('ERROR') and 'grasp missed' in res
    assert tb.held is None
    assert tb.run('place', {'target': 'o1'}).text.startswith('REFUSED')   # nothing held


def test_a_drop_in_transit_is_caught_by_the_check(tmp_path):
    backend, lines = LiveBackend(tmp_path, drop_in_transit=True), []
    tb = _live_toolbox(backend, lines)
    tb.capture()
    tb.run('detect_objects', {})
    tb.run('inspect_area', {'bbox': [180, 80, 220, 120]})
    tb.run('pick', {'object': 'o1'})
    res = tb.run('place', {'target': 'a1'}).text
    assert res.startswith('ERROR') and 'dropped' in res
    assert '"height_m": 0.04' in res          # the list shows where it fell
