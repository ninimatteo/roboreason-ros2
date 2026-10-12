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
        grasp=GraspGeometry(0.5, 0.02, 0.057),
    )
    return Toolbox(backend=backend, config=cfg, log=lines.append)


PLAN = [('submit_plan', {'steps': [{'action': 'pick', 'description': 'pick the cube'},
                                   {'action': 'place', 'description': 'place it'}]})]


def test_draft_then_execute_happy_path(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([
        PLAN,
        [('inspect_object', {'bbox': [85, 85, 115, 115]})],
        [('pick', {'grasp_point': [0.1, -0.4, 0.02], 'grasp_width': 0.04})],
        [('place', {'position': [0.3, -0.3, 0.0], 'held_height': 0.02}),
         ('report_progress', {'step': 2, 'status': 'done'})],
        [('finish', {'success': True, 'summary': 'cube moved'})],
    ])
    session = AgentSession(client, _toolbox(backend, lines), 'move the cube', log=lines.append)

    steps = session.draft_plan()
    assert [s['action'] for s in steps] == ['pick', 'place']
    assert client.seen[0][0] == ['submit_plan']

    outcome = session.execute()
    assert outcome.success and outcome.summary == 'cube moved'
    assert [s[0] for s in backend.skills] == ['approach', 'pick', 'approach', 'approach', 'release']
    assert backend.skills[4][1]['object_height'] == 0.02
    assert '[Step 2] done -> OK' in lines
    # inspect_object's answer reached the model
    tool_msgs = [m for m in client.seen[2][1] if m['role'] == 'tool']
    measured = json.loads(tool_msgs[-1]['content'])
    assert measured['height_m'] == 0.04 and measured['grasp_point'] == [0.1, -0.4, 0.02]
    assert measured['width_m'] == 0.03     # 30 px bbox at 1 mm per pixel


def test_failed_pick_is_reported_back_and_retried(tmp_path):
    backend, lines = FakeBackend(tmp_path, fail_first_pick=True), []
    pick = ('pick', {'grasp_point': [0.1, -0.4, 0.02], 'grasp_width': 0.04})
    client = ScriptedClient([PLAN, [pick], [pick], [('finish', {'success': True, 'summary': 'ok'})]])
    session = AgentSession(client, _toolbox(backend, lines), 'pick', log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert outcome.success
    after_fail = [m for m in client.seen[2][1] if m['role'] == 'tool'][-1]['content']
    assert 'gripper close failed' in after_fail
    assert [a[2] for a in outcome.actions] == [False, True]


def test_unsafe_target_is_refused_not_corrected(tmp_path):
    backend, lines = FakeBackend(tmp_path), []
    client = ScriptedClient([
        PLAN,
        [('pick', {'grasp_point': [0.1, -0.4, -0.2], 'grasp_width': 0.04})],
        [('finish', {'success': False, 'summary': 'gave up'})],
    ])
    session = AgentSession(client, _toolbox(backend, lines), 'pick', log=lines.append)
    session.draft_plan()
    outcome = session.execute()
    assert not outcome.success
    assert backend.skills == []
    refused = [m for m in client.seen[2][1] if m['role'] == 'tool'][-1]['content']
    assert refused.startswith('REFUSED') and 'below the table' in refused


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
    # 100 px of 640 = 156.25/1000, 100 px of 480 = 208.3/1000
    res = json.loads(tb.run('inspect_object', {'bbox': [140, 190, 172, 227]}).text)
    assert res['height_m'] == 0.04


def test_actions_after_a_failed_one_in_the_same_turn_are_skipped(tmp_path):
    backend, lines = FakeBackend(tmp_path, fail_first_pick=True), []
    client = ScriptedClient([
        PLAN,
        [('pick', {'grasp_point': [0.1, -0.4, 0.02], 'grasp_width': 0.04}),
         ('place', {'position': [0.3, -0.3, 0.0], 'held_height': 0.02}),
         ('inspect_object', {'bbox': [85, 85, 115, 115]})],
        [('finish', {'success': False, 'summary': 'stop'})],
    ])
    session = AgentSession(client, _toolbox(backend, lines), 'x', log=lines.append)
    session.draft_plan()
    session.execute()
    assert 'release' not in [s[0] for s in backend.skills]
    results = [m['content'] for m in client.seen[2][1] if m['role'] == 'tool'][-3:]
    assert results[0].startswith('ERROR') and results[1].startswith('SKIPPED')
    assert '"height_m"' in results[2]      # perception still runs
