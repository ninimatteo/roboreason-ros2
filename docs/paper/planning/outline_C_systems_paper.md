# Outline C — Systems Paper (RoboReason Lab's real-hardware counterpart)

**Central claim**: a modular, decoupled Sense-Plan-Act architecture
(LLM / VLM / VLM→LLM hybrid planning, swappable reasoning strategies, a
supervised web control panel, a per-run debug/benchmark harness) can be
deployed and operated on real hardware with the same "swap reasoning
strategies without touching the rest of the pipeline" modularity [Sim]
demonstrates in simulation — while surfacing, and needing to solve, an
entirely different class of engineering problem: process supervision,
concurrency, actuator-level safety, and calibration drift, none of which
exist in a simulator.

**Why choose this one**: mirrors [Sim]'s own paper structure and title
almost directly ("RoboReason Lab" → "RoboReason ROS2"), which is exactly
what you asked for as a model. Reviewer risk is the opposite of outlines A
and B: ICRA is generally *less* receptive to pure systems papers without a
sharp scientific claim, unless framed carefully as "engineering findings,"
not just "we built X." Strongest if it explicitly foregrounds the
audited safety gap (`AUDIT_FINDINGS.md`) as its own empirical contribution
— "here is what dry-run-green-but-hardware-unsafe looks like, catalogued
across a real deployment" is a legitimate, ICRA-relevant finding, not just
a features list.

**Estimated net-new work for Sept 15**: writing is the largest share (no
existing report section is structured this way — closest is
`docs/report/sections/02_architecture.tex`, which needs significant
compression/reframing, not reuse). No new experiments strictly required,
but the paper is weaker without at least one quantitative validation
akin to [Sim]'s Table I/II (token/latency/cost, or LoC/IPT-style
integration-effort metrics) — currently no such benchmarking exists in
this codebase for the real-hardware pipeline and would need to be
produced, which is nontrivial for the 7-week window.

## Title candidates

1. *RoboReason ROS2: A Modular LLM/VLM Task-Planning Stack for Real-World
   Manipulation*
2. *From Simulation to the Workshop: Engineering an LLM-Driven Manipulation
   Stack on Real Hardware*
3. *What Simulation Doesn't Test: Process Supervision, Concurrency, and
   Safety in a Real-Hardware LLM Planning System*

## Section-by-section (ICRA 8pp double-column, ≈ 6,500–7,500 words)

### Abstract (≈200 words)
Frame as: modular architecture (mirrors [Sim]'s pitch) + real-hardware
deployment reveals a distinct engineering problem set (supervision,
concurrency, actuator safety) + an audited catalogue of safety-relevant
gaps found and fixed + validation via the 120-trial benchmark as evidence
the system actually works end-to-end, not just runs.

### 1. Introduction (≈900 words)
- Open with the same gap [Sim] opens with (simulation lowers the barrier
  to entry for LLM-robotics research) — then pivot: that same modularity,
  once pointed at a real actuator, needs an entirely different supporting
  stack (process supervision, e-stop, calibration, debug capture) that a
  simulator never requires, because nothing in a simulator can leave a
  gripper closed on a person's hand or leave a UR driver in a half-started
  state.
- Contributions (3):
  1. A modular real-hardware LLM/VLM/hybrid planning architecture (ROS2,
     decoupled Sense-Plan-Act, 7 reasoning methods, swappable at the same
     interface boundary [Sim] uses in simulation) (Sec. III).
  2. A supervised operator stack (web GUI, process supervision with
     duplicate-executor guards and driver auto-retry, cancellable
     execution, per-run debug capture) engineered specifically for the
     failure modes real hardware introduces (Sec. IV).
  3. An audited catalogue of dry-run-green/hardware-unsafe gaps found via
     systematic code review post-deployment (trajectory failure reported
     as success, gripper commands that cannot fail, silent wrong-frame
     deprojection), each with root cause and fix direction (Sec. V) —
     framed as generalizable lessons for anyone deploying an LLM planner
     on an actuated system, not just this codebase's bug list.

### 2. Related Work (≈600 words)
- [Sim] gets the most space here, as the direct simulation-side
  counterpart — the comparison should be structural (same modularity
  principle, same "swap reasoning methods behind one interface" idea) with
  the paper's contribution being what changes when the abstraction
  boundary meets a real actuator.
- Other real-hardware LLM-manipulation systems (SayCan, Code-as-Policies
  real deployments, RT-2-class VLA systems as the alternative
  architecture) — contrast decoupled vs. end-to-end again, briefly.
- ROS2 systems/architecture papers generally (concurrency patterns,
  supervision) — lighter section, mostly grounding vocabulary.

### 3. Architecture (≈1,300 words) — condensed from `docs/report/sections/02_architecture.tex`
- Node pipeline diagram: task_interface/GUI → planner (LLM|VLM|VLM_LLM) →
  plan_manager → skill executor (fake|UR5), with the VLM camera-service
  branch.
- The abstraction boundary mirrored against [Sim]'s
  `CoppeliaRobotAbstraction` family: here it's `EmbodiedAgent` +
  `ReasoningMethod` base class + `_select_prompts()` — same "swap the
  strategy without touching the pipeline" property, demonstrated by the
  fact 7 reasoning methods share one interface.
- `ReentrantCallbackGroup` + `MultiThreadedExecutor` as the one structural
  rule preventing deadlock across every service-calling node — worth a
  full paragraph, it's a genuinely reusable lesson for ROS2 + LLM-latency
  service chains specifically (services routinely block for seconds on an
  LLM call, which is unlike typical ROS2 service latency assumptions).
- Configuration consolidation (`pydantic-settings`, one source of truth) —
  brief, as an engineering-hygiene note tied to the drift bugs it prevents
  (Session 2/3's board-geometry and stale-model-name mismatches).

### 4. The Operator Stack (≈1,100 words)
- Web GUI (`robo_reason_gui`): live chat/plan/execute, streamed execution
  log, camera feed with pixel/ChArUco overlay, benchmark annotation form.
- Process supervision as a first-class design concern: `StackSupervisor`,
  `UrDriverSupervisor` (retry-with-backoff against an explicit readiness
  probe — "the UR driver is treated as inherently flaky, not fixed," a
  deliberate framing worth stating explicitly), `CameraServiceSupervisor`;
  the duplicate-executor guard as a concrete example of a failure mode
  that has *no equivalent* in simulation (a leftover process cannot exist
  in a simulator the way a leftover ROS2 node can).
- Emergency stop (`CancelExecution.srv`): design (event-checked between
  steps, deliberately not lock-gated so it's callable mid-block) and its
  known limitation (un-cancellable `wait`/gripper-settle sleeps — cite
  `AUDIT_FINDINGS.md`'s cancel/resume race directly as an open, honestly
  reported issue, not swept under the rug).
- Debug recorder: per-run artifact capture (command, config, response,
  logs, camera frame+overlay) as the infrastructure that made both the
  geometry-bug diagnoses and the benchmark study possible — tie this
  explicitly to Section 5/6, it's the connective tissue of the whole paper.

### 5. Audited Safety Gap (≈1,000 words) — the empirical spine of this framing
- Present as a structured catalogue (severity-tiered, matching
  `AUDIT_FINDINGS.md`'s own Critical/Correctness/Minor split), 4-6 of the
  most illustrative entries with root cause + fix direction:
  - Trajectory abort reported as skill success (tolerance violation not
    checked against `GoalStatus`/`error_code`).
  - Gripper `SetIO` command that cannot fail (ignores `future.result()`,
    a dead driver "succeeds").
  - Uncalibrated deprojection returning `success=True` in camera-frame
    coordinates with no consumer-side frame check.
  - Cross-goal mutable state (tool offset not restored on a
    cancelled/aborted pick).
- Explicit framing: **none of these are LLM/VLM-reasoning bugs** — they
  are systems-integration bugs that exist purely because the plan reaches
  an actuator. State this as the paper's generalizable lesson: an
  LLM-planner architecture's correctness claims are only as strong as its
  weakest actuator-feedback path, and that path needs its own audit
  independent of planning-quality evaluation.

### 6. Validation (≈900 words)
- The 120-trial LLM-vs-VLM benchmark, presented here as *system
  validation evidence* (does the whole stack work end-to-end, safely,
  across real task diversity) rather than as the paper's primary
  scientific question (contrast with outline B's framing) — headline
  numbers + one supporting figure.
- If time allows: a [Sim]-style integration-effort table (LoC / setup
  steps / config surface for adding a new reasoning method or a new
  robot skill to this stack) — directly comparable to [Sim]'s Table II,
  and the single best way to make this paper legible as "the same kind of
  contribution [Sim] makes, for real hardware."

### 7. Discussion & Limitations (≈450 words)
- What transfers from simulation-first development to real deployment
  (the modular reasoning-method interface, largely unchanged) vs. what
  had to be built new (everything in Sec. 4–5) — this contrast is the
  paper's real takeaway for anyone planning the same sim→real move.
- Limitations per `00_foundation.md`, plus: single embodiment (no
  mobile base, unlike [Sim]'s Pioneer P3DX variants) — state as scope,
  not apology.

### 8. Conclusion (≈150 words)

### References (~30–35; [Sim] and [RAS] both prominent, general ROS2/systems
citations, less VLA-comparison literature than outline B)

## Reviewer self-check

| Dimension | Risk | Mitigation |
|---|---|---|
| "Systems paper, no scientific claim" pushback (ICRA's classic objection) | High | Section 5 (audited safety gap) must read as a *finding*, not a bug list — lead with the generalizable claim ("actuator-feedback correctness is a distinct failure axis from planning correctness") before the catalogue |
| No quantitative integration-effort table (unlike [Sim]'s Table II) | Medium | Either produce one (real new work, time-boxed) or explicitly note its absence and why (real-hardware "integration effort" is dominated by supervision/safety work that doesn't reduce to LoC the way [Sim]'s sim-only skill additions do) |
| Reads as a technical report, not a paper | Medium-High | This is the real risk vs. outlines A/B — the existing `docs/report/` is chronological/exhaustive; this outline must be rewritten around claims, not sessions, from scratch |
