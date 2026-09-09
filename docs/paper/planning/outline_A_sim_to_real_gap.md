# Outline A — Sim-to-Real Failure-Mode Gap

**Central claim**: benchmarks that validate LLM/VLM robot planners entirely
in simulation — including [RAS]'s own taxonomy-driven benchmark — cannot
observe the failure mode that dominates on real hardware (untrustworthy
model-computed geometry), because simulation never forces the model to
compute anything a physical sensor could contradict. We name the gap,
diagnose it from real hardware evidence, fix it with a deterministic
grounding layer, and show the fix (plus the residual gap) empirically.

**Why this is the strongest ICRA candidate of the three**: it has an
algorithmic contribution (not just a report), the evidence already exists
(committed fixes + benchmark data), and the framing directly and favorably
engages [RAS] rather than merely citing it — a natural reviewer hook.

**Estimated net-new work for Sept 15**: writing + a validation ablation
(rerun a handful of `sort_hard`/`arith_hard`/`pp_hard` trials with the
deterministic fixes disabled vs. enabled, isolating their effect —
currently the fixes are validated against captured `debug/` logs and unit
tests but not a controlled A/B on hardware, per `session-context.md`'s
Known Open Issues). This ablation is the one piece of *new* experimental
work all three outlines would benefit from, but it's most load-bearing for
this one.

**Update 2026-09-09.** The 120-trial benchmark was rerun in September on a
single unified model (`nebius/kimi-k2.6`, both modes — see ROBOAI-25) after
July's VLM model (`qwen3.6-27b`) left Nebius's catalog. The rerun does
*not* reproduce July's value-mapping-arithmetic-specific VLM collapse
(30%/25% TSR) — TSR is now LLM 97.3% vs VLM 88.7% overall, a real but much
smaller gap, and reading all 22 non-empty operator notes shows the failures
are collisions and mispositioning during multi-object placement, occurring
in **both** arms, not a reasoning error isolated to VLM. This is better
evidence for this outline's actual central claim than July's data was: the
dominant real-hardware failure mode is geometric (object placement/spacing
that a physical arm can get wrong regardless of how the scene was
perceived), not a VLM-specific perception or reasoning weakness — see the
new bug #6 in §3 and the rewritten §5. Outline B, whose central claim
*was* the VLM-specific arithmetic collapse, does not survive this rerun;
this outline does, and comes out of it with a cleaner story.

## Title candidates

1. *When the Model Does the Math: A Sim-to-Real Failure-Mode Gap in
   LLM-Based Robot Task Planning*
2. *Don't Trust the Model's Arithmetic: Deterministic Geometric Grounding
   for LLM/VLM-Driven Manipulation*
3. *What Symbolic Benchmarks Miss: Geometric Grounding as the Dominant
   Real-World Failure Mode for LLM Robot Planners*

## Section-by-section (ICRA 8pp double-column, ≈ 6,500–7,500 words)

### Abstract (≈200 words)
Problem → gap → method → evidence → headline number. Draft skeleton:
"LLM/VLM-based robotic planners are increasingly validated in simulation,
where perception and geometry are either perfectly known or entirely
symbolic. We show, through a real UR5cb manipulator deployment with a
calibrated RGB-D camera, that the dominant class of planning failure on
physical hardware — miscalculated release height, grasp width, object
footprint, and multi-object placement spacing arising from the model (or an
insufficiently grounded planning layer) performing its own geometric
arithmetic — is structurally invisible to symbolic/simulated benchmarks,
including a directly comparable prior taxonomy-driven benchmark [RAS]. We
diagnose six recurring geometry bugs from production hardware logs, replace
model-side arithmetic with a deterministic grounding layer where possible,
and validate the fix via [ablation numbers]. On a 120-trial real-hardware
LLM-vs-VLM study using the same evaluation philosophy as [RAS] adapted to
physical ground truth, we find failures concentrate in multi-object
placement and collision regardless of grounding modality (LLM: 97.3% TSR,
VLM: 88.7% TSR, both 100% safe), a distribution no simulated benchmark of
this kind could produce."

### 1. Introduction (≈900 words)
- Hook: LLM/VLM planners are validated almost exclusively in simulation;
  cite [Sim], [RAS], and 2-3 more (Ahn et al., Huang et al.).
- The core claim, stated directly, by end of paragraph 2: symbolic
  ground-truth benchmarks cannot see geometry-arithmetic failures because
  they never force the model to compute a quantity a sensor could
  contradict.
- Contribution list (aim for 3, each one sentence + forward-reference):
  1. A taxonomy of six recurring real-hardware geometry-grounding bugs,
     diagnosed from production debug logs across six development
     sessions (Sec. III).
  2. A deterministic grounding layer that removes model arithmetic from
     the geometry-critical path, validated by [ablation] (Sec. IV).
  3. A 120-trial real-hardware benchmark, methodologically anchored to
     [RAS]'s TS/TSR/AETS metrics but adapted for a physical rig with no
     collision sensor or vision-based state check, showing the residual
     failure distribution is dominated by multi-object placement geometry
     regardless of grounding modality, not by a single planner's weakness
     (Sec. V).

### 2. Related Work (≈700 words)
- LLM/VLM planning surveys + reasoning strategies (ReAct, ToT, Self-Refine,
  CoT-SC — cite ISR-LLM/ICRA'24 explicitly, it's a real ICRA precedent for
  self-refinement in long-horizon planning).
- [Sim] and [RAS] get a full paragraph each, not a single citation —
  state precisely what ground truth means in each (scripted CoppeliaSim
  scene vs. hand-authored symbolic text) and why grasp/release geometry
  literally cannot be wrong in either.
- Sim-to-real transfer literature (general robotics, not LLM-specific) —
  root the framing in an established concern.
- VLA/end-to-end approaches as the road not taken (same argument [RAS]
  already makes: decoupling preserves diagnosability) — brief, since this
  paper's contribution is about the decoupled pipeline's own weak point,
  not a VLA critique.

### 3. Geometry-Grounding Failure Taxonomy (≈1,600 words) — the empirical core
Organize as a table (bug × symptom × root cause × real-hardware trigger)
plus prose for the two most instructive cases, drawn directly from
`docs/GRASP_GEOMETRY_PIPELINE.md` and `docs/report/sections/04_technical_decisions.tex`:
1. **Top-vs-mid-body grasp/release mismatch** — release-lift set to full
   object height, assuming a top-surface grasp; the executor actually
   grasps mid-body (`descent = min(0.5×height, max_descent)`). Net effect:
   systematic ~half-height release overshoot. Recurred *twice*
   independently (VLM mode fixed Session 4, LLM mode fixed Session 5) —
   worth stating plainly that fixing it once did not fix it everywhere,
   itself evidence the bug class is structural, not incidental.
2. **Double-counted stacking height** (VLM→LLM hybrid) — a depth-measured
   true top surface plus a second, blind VLM size estimate added on top by
   the LLM's own stacking formula.
3. **Footprint vs. point-distance target matching** — a release genuinely
   inside a tray's real bounding box but far from its registered center
   point was being treated as "missed the zone."
4. **Grasp-width sampled from the silhouette edge** — the single worst
   pixel for valid depth; one bad sample aborted an entire otherwise-valid
   plan.
5. **Width-dependent TCP offset** — RG2 fingers pivot, so flange-to-contact
   distance is a function of grasp width, not a constant; wrong for any
   object whose width diverges from the calibration midpoint.
6. **Multi-object sequential-placement collision** (new, September rerun) —
   spacing multiple releases inside a target zone (ROBOAI-19's bounds-aware
   `distribute_zone_releases`) keeps final positions from overlapping, but
   does not prevent the arm clipping an already-placed object while
   approaching or releasing the next one, nor a released object rolling
   into a neighbor from residual momentum or a few cm of height error.
   Dominant failure mode in the September 120-trial rerun (22 of 22
   non-empty operator notes describe a collision or a fallen/displaced
   object during a multi-cube task, in **both** LLM and VLM arms) —
   identified, partially mitigated, **not yet fixed**: state this plainly
   as an open problem the taxonomy surfaces, not a claimed contribution.
Frame the throughline explicitly: **every one of these bugs required a real
sensor (depth camera), a real actuator (pivoting gripper), or real
multi-body physical contact to exist at all.**

### 4. Deterministic Grounding Layer (≈1,100 words) — the method
- Design principle: pull geometric arithmetic *out* of the model and into
  code wherever a sensor or CAD-known quantity makes the answer
  computable — the model still chooses *what*/*where symbolically*, code
  computes *how much*.
- `_fix_object_height`: derive from the paired pick's actual grasp Z minus
  table surface Z (not the model's `size[2]`).
- `_fix_release_height`: footprint-containment matching (bounding box, not
  point-distance) + "exact echo = intentional, drift = nudge back" rule.
- Width-aware TCP offset (`grasp_geometry.py`, piecewise-linear
  interpolation) + capped pick descent.
- Best-effort, exception-isolated grasp-width sampling inset from the
  silhouette edge.
- **This is where the ablation result goes**: report the deterministic
  layer's effect on TS/TSR (with vs. without, on the geometry-sensitive
  task subset — `sort_hard`/`arith_hard`/`pp_hard` are the natural targets
  since they involve stacking/tray placement).

### 5. Real-Hardware Evaluation (≈1,600 words) — the 120-trial benchmark
- Use the September rerun (`benchmark/results_2026-09_3arm.csv`, ROBOAI-25),
  not the July dataset: task matrix (6 conditions × 10 reps × 2 models, one
  unified model per modality, `nebius/kimi-k2.6`), TS/TSR/AETS adapted to
  semi-automatic annotation, headline numbers TS 100%/100%, TSR 97.3%
  [93.3,99.0]% LLM vs. 88.7% [82.6,92.8]% VLM (Wilson 95% CI). Report the
  one pre-registered comparison (Fisher's exact test, LLM vs. VLM,
  `arith_hard` TSR) honestly: p=0.74, no detectable effect — state this as
  a finding, not a shortfall, since it is itself evidence against a
  VLM-specific reasoning weakness.
- Reframe the failure-mode evidence around what the September notes
  actually show: reading all 22 non-empty operator notes, every one
  describes a collision, a fallen object, or a mispositioned release during
  a multi-cube task (`pp_hard`/`sort_hard`/`arith_hard`), split across
  **both** arms roughly in proportion to their trial counts — present this
  as **evidence for the paper's central claim**: the dominant real-hardware
  failure is multi-object placement geometry (bug #6, §3), invisible to a
  symbolic benchmark that never simulates two real bodies occupying space
  at once, and orthogonal to which planner grounded the scene.
- State the semi-automatic methodology and its limitation plainly (no
  collision sensor, no vision-based final-state check — contrast directly
  with [RAS]'s fully-automatic metrics, which are only possible *because*
  their world is symbolic).
- Note in Limitations: no `VLM_LLM` hybrid arm this cycle either — dropped
  mid-collection when a real object in the camera's field of view (a
  cardboard flap propped on a tray-like container, out of the intended
  workspace) returned no valid depth at its viewing angle and crashed the
  hybrid grounding call every time it was detected; a physical-setup
  problem, not evidence about the hybrid architecture itself, but honestly
  it means this outline still has no `VLM_LLM` data point either.

### 6. Discussion & Limitations (≈500 words)
- What this means for anyone benchmarking LLM robot planners: symbolic/sim
  validation is necessary but not sufficient; a geometry-grounding audit
  belongs in the evaluation protocol before hardware deployment.
- Honest limitations from `00_foundation.md` (n=120, one rig, one model per
  modality, no VLM_LLM hybrid arm, no inter-rater reliability check).

### 7. Conclusion (≈150 words)

### References (~35–45 — [Sim], [RAS], ISR-LLM, ReAct/ToT/Self-Refine/CoT-SC
originals, SayCan/zero-shot-planner grounding papers, 2–3 sim-to-real
transfer papers, 10–15 more from a real literature pass on real-hardware
LLM manipulation)

## Reviewer self-check (informal, before submitting)

| Dimension | Risk | Mitigation already in this outline |
|---|---|---|
| Is the claim falsifiable/testable, not just narrative? | Medium | Section 4's ablation makes it a measured effect, not just a diagnosis |
| n=120, one rig — generalizes? | High | State explicitly in Limitations; consider a second embodiment/gripper as future work, not a promise |
| Overlaps with [RAS] too much? | Medium | Central claim is about what [RAS]'s *method class* structurally cannot see, not a re-run of their taxonomy — differentiate in Related Work explicitly, not just in results |
| Contribution is "just bug fixes"? | Medium-High | Frame Section 3 as a *taxonomy* (generalizable claim: these bug classes recur across grounding pipelines) not a changelog; Section 4 needs the ablation to read as a validated method, not a war story |
