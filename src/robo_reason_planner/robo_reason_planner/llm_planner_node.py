"""
LLMPlannerNode — exposes /plan_task service for text-based planning.

Uses EmbodiedAgent with an LLM client, or a deterministic mock for dry-runs.
The scene_json field from the request is used as the environment description.

ROS2 parameters:
  use_mock_llm      (bool,  default true)  — return a hardcoded pick-and-place plan
  reasoning_method  (str,   default 'fhp') — fhp|ffhp|react|cot_sc|tot|always_act|self_refine
  model_name        (str,   default 'groq/llama4-scout-17b')
  temperature       (float, default 0.1)
"""

import json
import traceback

import dotenv
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_srvs.srv import Trigger

from robo_reason_bringup.config import settings
from robo_reason_interfaces.srv import PlanTask
from robo_reason_planner.agent_runner import run_plan_loop
from robo_reason_planner.command_grounding import check_command_grounding
from robo_reason_planner.debug_recorder import DebugRun, fetch_terminal_logs
from robo_reason_reasoning.embodied_agent import EmbodiedAgent


class LLMPlannerNode(Node):

    def __init__(self):
        super().__init__('llm_planner_node')

        # Parameters are declared here but read per-request in the callback, so
        # the GUI can retune the planner live (ros2 param set / SetParameters)
        # without relaunching the node.
        self.declare_parameter('use_mock_llm', settings.USE_MOCK_LLM)
        self.declare_parameter('reasoning_method', settings.REASONING_METHOD)
        self.declare_parameter('model_name', settings.MODEL_NAME)
        self.declare_parameter('temperature', settings.TEMPERATURE)

        dotenv.load_dotenv()

        # ReentrantCallbackGroup so /plan_task can call /gui/get_terminal_logs
        # without deadlocking on the MultiThreadedExecutor (same requirement
        # as vlm_planner_node/vlm_llm_planner_node).
        self._cb_group = ReentrantCallbackGroup()

        self._service = self.create_service(
            PlanTask, '/plan_task', self._plan_task_callback,
            callback_group=self._cb_group,
        )
        self._terminal_logs_client = self.create_client(
            Trigger, '/gui/get_terminal_logs', callback_group=self._cb_group,
        )

        use_mock = self.get_parameter('use_mock_llm').value
        label = (
            'MOCK' if use_mock
            else f"{self.get_parameter('reasoning_method').value}, "
                 f"{self.get_parameter('model_name').value}"
        )
        self.get_logger().info(f'[LLMPlannerNode] Ready — {label} (params read per request)')

    # ── /plan_task callback ────────────────────────────────────────────────────

    def _plan_task_callback(self, request, response):
        user_command = request.user_command
        scene_json = request.scene_json

        self.get_logger().info(f'[LLMPlannerNode] Received: "{user_command}"')

        use_mock = self.get_parameter('use_mock_llm').value
        run = DebugRun(mode='LLM-mock' if use_mock else 'LLM', command=user_command, config={
            'reasoning_method': self.get_parameter('reasoning_method').value,
            'model_name': self.get_parameter('model_name').value,
            'temperature': self.get_parameter('temperature').value,
            'scene_json': scene_json,
        })

        grounded, err = check_command_grounding(user_command, scene_json)
        if not grounded:
            response.success = False
            response.error_message = err
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=False, error=err)
            return response

        try:
            if use_mock:
                plan_data = self._mock_plan(user_command, scene_json)
                self.get_logger().info('[LLMPlannerNode] Generated mock plan.')
            else:
                plan_data = self._llm_plan(user_command, scene_json, run)
                self.get_logger().info('[LLMPlannerNode] Generated LLM plan.')

            response.success = True
            response.plan_json = json.dumps(plan_data)
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=True, response=plan_data)

        except Exception:
            tb = traceback.format_exc()
            self.get_logger().error(f'[LLMPlannerNode] Planning error:\n{tb}')
            response.success = False
            response.error_message = tb
            run.save_terminal_logs(fetch_terminal_logs(self._terminal_logs_client))
            run.finish(success=False, error=tb)

        return response

    # ── LLM plan ───────────────────────────────────────────────────────────────

    def _llm_plan(self, user_command: str, scene_json: str, run: DebugRun) -> dict:
        reasoning_method = self.get_parameter('reasoning_method').value
        model_name = self.get_parameter('model_name').value
        temperature = self.get_parameter('temperature').value

        agent = EmbodiedAgent(
            reasoning_mode=reasoning_method,
            client_parameters={
                'model_name': model_name,
                'temperature': temperature,
            },
            client_type='llm',
        )

        observation = {
            'user_request': user_command,
            'environment_map': scene_json,
        }
        self.get_logger().info(f'[LLMPlannerNode] Starting plan with\n{observation}')
        run.log(f'Starting plan with {observation}')

        plan_steps = run_plan_loop(agent, observation)
        plan_steps = self._fix_object_height(plan_steps, scene_json)
        plan_steps = self._fix_release_height(plan_steps, scene_json)

        self.get_logger().info(
            f'[LLMPlannerNode] Plan done — "{user_command}", '
            f'method: {reasoning_method}, model: {model_name}, '
            f'steps: {len(plan_steps)}'
        )
        run.log(
            f'Plan done — "{user_command}", method: {reasoning_method}, '
            f'model: {model_name}, steps: {len(plan_steps)}'
        )
        for s in plan_steps:
            self.get_logger().info(
                f'[LLMPlannerNode]   Step {s["step"]}: {s.get("action_name", "?")}'
            )
            run.log(f'  Step {s["step"]}: {s.get("action_name", "?")}')

        return {
            'task_summary': user_command,
            'reasoning_method': reasoning_method,
            'model': model_name,
            'plan': plan_steps,
        }

    def _fix_object_height(self, plan_steps: list, scene_json: str) -> list:
        """Overwrite each release's object_height with the picked object's
        actual grasp height above the table, instead of trusting the LLM's
        own "object_height = size[2] of the held object" arithmetic (every
        LLM-mode prompt's instruction — see fhp_ffhp_prompts.py etc.).

        That instruction is only correct if the object's authored
        `position.z` sits exactly at its top surface — LLM mode has no
        camera to verify this, unlike VLM mode's equivalent, real
        depth-derived correction in vlm_planner_node.py::_deproject_plan.
        If position.z is instead authored lower (e.g. tuned so `pick`
        actually contacts the object, rather than as a literal "top
        surface" value), the executor's release_position.z += object_height
        (ur5_skill_executor_node.py) overshoots by however far short of the
        object's true top that grasp point falls — observed on hardware as
        a stacked object landing several cm above where it should have
        (grasp z - table surface_z = 0.01 m, but size[2] = 0.045 m, an
        0.035 m — 3.5 cm — overshoot, matching the reported "at least 3cm").

        Computing object_height as (grasp z - table surface_z) instead
        doesn't care where on the object position.z was actually authored
        — it just measures how far the real contact point sits above the
        table the object was resting on, which is exactly the distance the
        TCP needs to rise for the object's bottom to land on the target
        surface.
        """
        try:
            table_surface_z = json.loads(scene_json)['workspace']['table']['surface_z']
        except (json.JSONDecodeError, KeyError, TypeError):
            return plan_steps

        held_height = None
        for step in plan_steps:
            action = step.get('action_name')
            if action == 'pick' and isinstance(step.get('target_position'), list):
                grasp_z = step['target_position'][2]
                # Only a sanity floor against a malformed scene (grasp point
                # below the table) — not a noise filter like VLM mode's
                # MIN_OBJECT_HEIGHT_M, since these are clean authored
                # numbers, not a real depth reading.
                held_height = max(grasp_z - table_surface_z, 0.0)
            elif action == 'release' and held_height is not None:
                old = step.get('object_height')
                step['object_height'] = held_height
                if old is None or abs(old - held_height) > 1e-3:
                    self.get_logger().info(
                        f'[LLMPlannerNode] release: object_height '
                        f'{old} -> {held_height:.3f} m (grasp height above table)'
                    )
                held_height = None
        return plan_steps

    def _fix_release_height(self, plan_steps: list, scene_json: str) -> list:
        """Overwrite every release's target x/y/z — before the executor's
        own object_height lift — with a deterministic surface reference
        looked up from the scene, instead of trusting the LLM's own
        release_position arithmetic.

        Height (z): this was observed getting it wrong even for the "flat
        release" case, which the prompt documents as simply
        release_position.z = table.surface_z: on hardware the LLM instead
        output a value ~2cm higher (coincidentally close to the held
        object's own grasp height), so the executor's later +=
        object_height lift double counted that offset. Separately,
        "release on top of another object" (release_position.z =
        target.position.z + target.size[2]) is wrong whenever the target
        is an objects.* entry, because an object's authored position.z is
        a grasp contact point (see _fix_object_height), not a base — so
        adding size[2] to it overshoots the target's true top by however
        far short of that object's own top its grasp point falls. A
        targets.* zone has no such ambiguity any more: its top surface is
        stated outright as bounds.z[1] (scene_mock.json's schema_notes).

        Position (x/y): a freely-computed position (e.g. "arrange the rest
        in a line on the table") can drift into a *different* target
        zone's real footprint without the model intending to place
        anything there — observed on hardware landing inside the tray's
        22x32cm footprint while being 10cm+ from its center, well past
        what point-distance matching would catch, and well past what
        "this is close enough to be the same spot" collision spacing
        (plan_manager's distribute_zone_releases, keyed off a ~3cm radius)
        would ever flag as related to the tray's own, separate release.
        The two cases are told apart by how the position was produced:
          - An *exact* echo of an entry's own registered centre (see
            _TARGET_MATCH_EPS_M) means the LLM read those coordinates
            straight out of the prompt/scene — an intentional placement,
            whether into a target zone or stacked on another object. For
            an objects.* entry that centre is its authored `position`;
            for a targets.* zone it is the midpoint of its `bounds`,
            which is exactly what the prompt tells the model to release
            at (fhp_ffhp_prompts.py). Keep the position, use that
            entry's true top.
          - Anything else that merely falls inside a *target zone's*
            footprint (bounds.x/bounds.y, padded by
            settings.ZONE_PLACEMENT_COLLISION_RADIUS_M) is a computed
            position that drifted somewhere it wasn't meant to be. Nudge
            it back out along whichever axis takes the smaller move, plus
            a small clearance margin, then resolve height normally for
            the corrected position. This nudge only checks target zones,
            not objects — objects are small enough, and already excluded
            via moved_origins, that this class of drift isn't worth
            chasing there too. It's also a single, one-shot nudge, not an
            iterative solver — if the nudge itself lands inside another
            zone/object, that second overlap is left as-is rather than
            chased further.
        The paired `approach` step immediately before a nudged release
        (if its target_position matches the release's pre-nudge x, y) is
        nudged by the same amount — otherwise the arm still flies toward
        the original, occupied spot before sidestepping only at the
        release itself (the same reasoning as
        distribute_zone_releases's approach-nudge, in schemas.py).

        A point can fall inside more than one *footprint* at once —
        notably the "table" zone itself is usually a broad catch-all
        covering most of the workspace, so it contains almost every point
        that also matches a smaller, more specific zone or object sitting
        on it. Among every entry whose footprint contains the (possibly
        nudged) point, the smallest one (by footprint area) wins, so a
        specific match always beats the generic table:
          - A target zone (targets.*): its declared top surface,
            bounds.z[1].
          - Another object (objects.*): its *actual* true top, derived
            from table_surface_z + its size[2] — not its own position.z,
            for the reason above.
          - No match (a bare point on the table): table_surface_z.
        Every object picked up earlier in this same plan (including the
        one currently held) is excluded from the object match set via its
        own pick step's position — none of them are still resting at
        their original scene position, so a later release near any of
        those spots must not be treated as "stacking on it".

        The executor still adds object_height on top of whatever z this
        sets (ur5_skill_executor_node.py's release_pos[2] += object_height,
        unchanged).
        """
        try:
            scene = json.loads(scene_json)
            table_surface_z = scene['workspace']['table']['surface_z']
        except (json.JSONDecodeError, KeyError, TypeError):
            return plan_steps

        targets = list(scene.get('targets', {}).values())
        objects = list(scene.get('objects', {}).values())
        margin = settings.ZONE_PLACEMENT_COLLISION_RADIUS_M
        clearance = settings.ZONE_PLACEMENT_MARGIN_M
        _TARGET_MATCH_EPS_M = 1e-3
        # A zone whose surface is within this much of the bare table isn't
        # a real physical obstacle (e.g. the "table" zone entry itself,
        # flush with table_surface_z) — only something raised meaningfully
        # above it (like a tray) is worth nudging a drifted release away
        # from.
        _NUDGE_HEIGHT_THRESHOLD_M = 0.03
        # Wider than `margin`, deliberately — used only when deciding which
        # zone/object's HEIGHT a release should use (the `candidates` loops
        # in resolve() below), never for the nudge/spacing checks above
        # them. Observed on hardware (ROBOAI-25, pp_hard, 2026-09-04): the
        # model spread 4 same-size cubes onto the tray at ±14 cm from its
        # center, 1 cm past the tray's `margin`-padded footprint on every
        # release, so none of them matched — every release silently fell
        # back to table_surface_z, 8 cm below the tray's real top, and the
        # gripper drove down into the tray trying to release "at the
        # table" while still positioned over it. The two directions of
        # error aren't symmetric: missing a raised zone's height is
        # dangerous (releases too low, into whatever's actually there);
        # using a zone's height for a point that's really just on the
        # table is not (releases a little higher than strictly needed) —
        # so this tolerance is wide on purpose, erring toward "assume the
        # raised thing is still there."
        #
        # 0.04 m, not more: re-tested against the same session's sort_hard
        # data (a genuine "arrange on the table" release 15 cm from the
        # tray's center, which must NOT match it) showed 0.05 m already
        # pulls that point into the tray's footprint too, flipping a
        # correct table release into an incorrect elevated one. The two
        # known real cases (14 cm must match, 15 cm must not) leave only
        # a ~1 cm safe window — this is tuned to those two observations,
        # not a robust general solution. A differently-shaped spread the
        # model produces next could still miss on either side. ROBOAI-19
        # (explicit target bounds) removed the *convention* guesswork
        # here, but not this tolerance: a bounds-aware release *layout*
        # that keeps every release inside the zone in the first place is
        # ROBOAI-18, still open.
        _HEIGHT_LOOKUP_TOLERANCE_M = 0.04

        # Every footprint check below runs against a normalized
        # (x_min, x_max, y_min, y_max) box rather than a raw scene entry,
        # because the two entry kinds are authored differently and only
        # one of them is a box already:
        #   - targets.* declare their footprint outright as `bounds`
        #     (ROBOAI-19) — read the corners, no arithmetic;
        #   - objects.* keep position (a grasp contact point) + size,
        #     deliberately, because a pick needs a single point rather
        #     than an area, so their box is still derived.
        def target_box(entry):
            bounds = entry.get('bounds') or {}
            bx, by = bounds.get('x'), bounds.get('y')
            if not bx or not by:
                return None
            return min(bx), max(bx), min(by), max(by)

        def object_box(entry):
            pos, size = entry.get('position'), entry.get('size')
            if not pos or not size:
                return None
            return (pos[0] - size[0] / 2, pos[0] + size[0] / 2,
                    pos[1] - size[1] / 2, pos[1] + size[1] / 2)

        def target_top_z(entry):
            """A zone's top surface — the height to release onto. Stated
            outright by the schema (bounds.z[1]), no base+height sum."""
            bounds_z = (entry.get('bounds') or {}).get('z')
            return max(bounds_z) if bounds_z else table_surface_z

        def object_top_z(entry):
            """An object's *actual* top: measured up from the table, not
            from its own position.z (a grasp contact point, not a base)."""
            return table_surface_z + entry.get('size', [0, 0, 0])[2]

        def contains(x, y, box, tol=margin):
            if not box:
                return False
            x_min, x_max, y_min, y_max = box
            return (x_min - tol <= x <= x_max + tol
                    and y_min - tol <= y <= y_max + tol)

        def footprint_area(box):
            x_min, x_max, y_min, y_max = box
            return (x_max - x_min) * (y_max - y_min)

        def is_centre_echo(x, y, box):
            if not box:
                return False
            x_min, x_max, y_min, y_max = box
            return (abs((x_min + x_max) / 2 - x) < _TARGET_MATCH_EPS_M
                    and abs((y_min + y_max) / 2 - y) < _TARGET_MATCH_EPS_M)

        def nudge_outside(x, y, box):
            """Push (x, y) just past the nearer padded edge of `box`, along
            whichever axis takes the smaller move."""
            x_min, x_max, y_min, y_max = box
            escape_x = min(x - (x_min - margin), (x_max + margin) - x)
            escape_y = min(y - (y_min - margin), (y_max + margin) - y)
            if escape_x <= escape_y:
                if x < (x_min + x_max) / 2:
                    return x_min - margin - clearance, y
                return x_max + margin + clearance, y
            if y < (y_min + y_max) / 2:
                return x, y_min - margin - clearance
            return x, y_max + margin + clearance

        def resolve(x, y, moved_origins):
            # Exact echo of a target zone's own centre -> intentional target
            # placement (e.g. "release into the tray"). The zone has no
            # `position` any more; its centre is the midpoint of its bounds,
            # which is what the prompt instructs the model to release at.
            for entry in targets:
                if is_centre_echo(x, y, target_box(entry)):
                    return x, y, target_top_z(entry)
            # Exact echo of an (unmoved) object's own position ->
            # intentional stack (e.g. "stack the red cube on the blue
            # cube" — this is the SAME kind of intentional exact-position
            # match as a target, just against an objects.* entry instead;
            # missing this case here previously caused the stacking
            # release itself to be misread as "drifted into the table's
            # footprint" and nudged away, breaking the stack entirely.
            for entry in objects:
                box = object_box(entry)
                if not box or any(contains(ox, oy, box) for ox, oy in moved_origins):
                    continue
                if is_centre_echo(x, y, box):
                    return x, y, object_top_z(entry)

            # Only nudge away from a target whose surface sits meaningfully
            # above the bare table (a real physical obstacle, like the
            # tray) — a broad catch-all zone like "table" itself is also,
            # technically, a targets.* entry with a footprint covering
            # most of the workspace, so almost every legitimate "on the
            # table" point matches it too. That's not a hazard to escape;
            # nudging away from it would just push the object off the
            # table trying to flee the table. Skip zones whose top is
            # within _NUDGE_HEIGHT_THRESHOLD_M of table_surface_z, and
            # among the rest, nudge away from the smallest (most specific)
            # match — mirroring the priority used for final height below.
            obstacle_matches = sorted(
                (
                    (footprint_area(box), box, entry)
                    for entry, box in ((e, target_box(e)) for e in targets)
                    if box and contains(x, y, box)
                    and (target_top_z(entry) - table_surface_z) > _NUDGE_HEIGHT_THRESHOLD_M
                ),
                key=lambda t: t[0],
            )
            if obstacle_matches:
                _, box, entry = obstacle_matches[0]
                self.get_logger().info(
                    f"[LLMPlannerNode] release: ({x:.3f}, {y:.3f}) drifted into "
                    f"{entry.get('label', 'a target')}'s footprint — nudging clear"
                )
                x, y = nudge_outside(x, y, box)

            candidates = []  # (footprint_area, surface_z)
            for entry in targets:
                box = target_box(entry)
                if contains(x, y, box, tol=_HEIGHT_LOOKUP_TOLERANCE_M):
                    candidates.append((footprint_area(box), target_top_z(entry)))
            for entry in objects:
                box = object_box(entry)
                if not box:
                    continue
                # Every object already picked up earlier in this same plan
                # (including the one currently held) is no longer resting
                # at its original scene position — matching against any of
                # them would be a stale false positive. Tight (default)
                # tolerance here on purpose: this is "was this exact spot
                # vacated", not the height-lookup match below it.
                if any(contains(ox, oy, box) for ox, oy in moved_origins):
                    continue
                if contains(x, y, box, tol=_HEIGHT_LOOKUP_TOLERANCE_M):
                    candidates.append((footprint_area(box), object_top_z(entry)))
            if not candidates:
                return x, y, table_surface_z
            return x, y, min(candidates, key=lambda c: c[0])[1]

        moved_origins = []
        for i, step in enumerate(plan_steps):
            action = step.get('action_name')
            if action == 'pick' and isinstance(step.get('target_position'), list):
                moved_origins.append((step['target_position'][0], step['target_position'][1]))
            elif action == 'release' and isinstance(step.get('release_position'), list):
                pos = step['release_position']
                orig_x, orig_y = pos[0], pos[1]
                new_x, new_y, new_z = resolve(orig_x, orig_y, moved_origins)

                if (new_x, new_y) != (orig_x, orig_y):
                    self.get_logger().info(
                        f'[LLMPlannerNode] release: position ({orig_x:.3f}, {orig_y:.3f}) '
                        f'-> ({new_x:.3f}, {new_y:.3f})'
                    )
                    if i > 0 and plan_steps[i - 1].get('action_name') == 'approach':
                        approach_pos = plan_steps[i - 1].get('target_position')
                        if (approach_pos and len(approach_pos) >= 2
                                and abs(approach_pos[0] - orig_x) < margin
                                and abs(approach_pos[1] - orig_y) < margin):
                            approach_pos[0] += new_x - orig_x
                            approach_pos[1] += new_y - orig_y
                if abs(pos[2] - new_z) > 1e-3:
                    self.get_logger().info(
                        f'[LLMPlannerNode] release: target z {pos[2]:.3f} -> '
                        f'{new_z:.3f} m (deterministic surface lookup)'
                    )
                pos[0], pos[1], pos[2] = new_x, new_y, new_z
        return plan_steps

    # ── Mock plan ───────────────────────────────────────────────────────────────

    def _mock_plan(self, user_command: str, scene_json: str) -> dict:
        scene = json.loads(scene_json)
        objects = scene.get('objects', {})
        targets = scene.get('targets', {})

        first_obj = next(
            (obj for obj in objects.values() if obj.get('graspable', False)), None
        )
        first_target = next(iter(targets.values()), None)

        if not first_obj or not first_target:
            return {'task_summary': 'No objects/targets found', 'plan': []}

        obj_pos = first_obj['position']
        obj_height = first_obj.get('size', [0, 0, 0.05])[2]

        # targets.* are axis-aligned bounds (see scene_mock.json's
        # schema_notes): aim at the middle of the zone's footprint, onto
        # its top surface bounds.z[1].
        bounds = first_target.get('bounds') or {}
        try:
            tgt_x = (bounds['x'][0] + bounds['x'][1]) / 2
            tgt_y = (bounds['y'][0] + bounds['y'][1]) / 2
            tgt_top_z = max(bounds['z'])
        except (KeyError, IndexError, TypeError):
            return {'task_summary': 'Target zone has no usable bounds', 'plan': []}

        plan = [
            {'step': 1, 'action_name': 'approach',
             'target_position': obj_pos, 'offset': 0.1, 'approach_direction': 'z'},
            {'step': 2, 'action_name': 'pick',
             'target_position': obj_pos, 'grasp_axis': 'z', 'come_back': True},
            {'step': 3, 'action_name': 'approach',
             'target_position': [tgt_x, tgt_y, tgt_top_z + 0.1],
             'offset': 0.0, 'approach_direction': 'z'},
            {'step': 4, 'action_name': 'release',
             'release_position': [tgt_x, tgt_y, tgt_top_z + obj_height / 2],
             'come_back': False},
            {'step': 5, 'action_name': 'move_home'},
        ]

        return {
            'task_summary': f'[MOCK] {user_command}',
            'reasoning_method': 'mock',
            'plan': plan,
        }


def main(args=None):
    rclpy.init(args=args)
    node = LLMPlannerNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
