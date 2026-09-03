# Outline B — LLM vs. VLM Grounding Comparison

**Central claim**: on real hardware, camera-grounded (VLM) planning does
*not* uniformly outperform a static symbolic scene description (LLM mode)
the way the "perception is always better" intuition suggests — and the gap
is driven by a specific, previously-unreported failure mode (reasoning
under a stated value mapping) rather than by grounding precision itself.
This is the benchmark-first framing: the 120-trial study is the headline,
the geometry fixes are supporting methodology that ensured a fair
comparison, not the main claim.

**Why choose this one**: it's the most complete and "done" of the three —
the data collection, analysis, and even a full write-up
(`docs/report/sections/05_benchmark.tex`) already exist. Least new writing,
least new experimental work. The risk is that a benchmark paper with n=120
on one embodiment reads thinner to ICRA reviewers than outline A's
method+evidence combination unless the qualitative failure-mode analysis
is pushed hard as the actual novel contribution (it's genuinely the most
interesting single finding across all three outlines).

**Estimated net-new work for Sept 15**: lowest of the three. Mainly
writing + strengthening statistical treatment (currently point estimates
per 10-trial cell — worth at least reporting confidence intervals or exact
binomial tests given the small per-cell n, since ICRA reviewers will ask).
Optionally: add the VLM_LLM hybrid as a third arm if time allows (flagged
in the report as the natural next step) — meaningfully strengthens the
paper's completeness but is real additional data collection (more hardware
trials + annotation time), a genuine scope call for the co-authors.

## Title candidates

1. *Seeing Is Not Always Believing: A Real-Hardware Comparison of
   Camera-Grounded and Scene-Description LLM Task Planning*
2. *Static Scene or Live Camera? An Empirical Study of LLM vs. VLM Robot
   Task Planners on Physical Hardware*
3. *Grounded but Wrong: When Vision-Language Planning Fails on Real
   Manipulation Tasks*

## Section-by-section (ICRA 8pp double-column, ≈ 6,500–7,500 words)

### Abstract (≈200 words)
"Vision-language models (VLMs) are often assumed to outperform text-only
LLM planners on manipulation tasks by grounding plans directly in the
live scene rather than a stale or hand-authored description. We test this
assumption on a real UR5cb manipulator across 120 trials spanning six task
conditions (pick-place, sort/stack, and value-mapping arithmetic tasks, each
at an easy/hard difficulty), comparing a static-scene LLM planner against a
camera-grounded VLM planner under matched reasoning strategy and evaluation
protocol. The LLM planner is both safer (95% vs. 82%) and more successful
(97% vs. 66%) overall, and the gap is not explained by grounding precision:
reading all logged failure notes shows the VLM's errors concentrate
specifically in tasks requiring the model to *compute* which object to act
on from a stated value mapping, not tasks where the object is named
directly — a reasoning failure that resembles, but is not, a perception
failure."

### 1. Introduction (≈900 words)
- Motivate the LLM-vs-VLM question directly: does adding a camera to an
  LLM planner help, on real hardware, under matched conditions? Most prior
  comparisons (cite VLA literature, VoxPoser, Code-as-Policies-style work)
  either don't compare against a non-visual baseline on the *same*
  hardware/task set, or are simulation-only ([RAS] itself never runs a
  camera).
- State upfront that this is an empirical/negative-ish result paper: the
  "more grounding is always better" intuition doesn't hold cleanly, and
  the reason why is itself the interesting part.
- Contributions (3):
  1. A controlled, matched-condition, real-hardware LLM-vs-VLM benchmark
     (120 trials, 6 task conditions × 2 difficulty levels), methodologically
     anchored to [RAS]'s task/complexity taxonomy but adapted to physical
     ground truth (Sec. III–IV).
  2. A qualitative failure-mode analysis (reading, not just counting,
     operator notes) isolating *value-mapping arithmetic* — not vision
     grounding — as the dominant driver of the VLM's underperformance on
     hard tasks (Sec. V).
  3. [Optional, if hybrid arm added] A third VLM→LLM hybrid condition
     testing whether combining live grounding with symbolic-scene planning
     recovers the gap.

### 2. Related Work (≈650 words)
- LLM-only robot planning (SayCan, zero-shot planners, Text2Motion).
- VLM/VLA planning and grounding (cite the general VLA critique already
  present in [RAS]'s §2 — monolithic perception+reasoning, hard to
  diagnose — and lean on it since this paper's evaluation methodology
  *is* that diagnosis, empirically).
- [RAS] gets the most space: same task/complexity taxonomy lineage, but
  *never runs a camera* — their "affordance perception" axis is simulated
  via text, not measured via pixels. This paper is the real-camera
  analogue of exactly that axis.
- [Sim] briefly, as the same group's simulation-only systems precedent.

### 3. System and Task Design (≈900 words)
- Brief architecture description (LLM mode: static `scene_mock.json`;
  VLM mode: Orbbec camera → ChArUco-calibrated deprojection → pixel
  grounding), enough for reproducibility, not a full systems section.
- Task matrix table (from `05_benchmark.tex`): `pp_easy/hard`,
  `sort_easy/hard`, `arith_easy/hard` — explain the arithmetic task's
  purpose explicitly: it's the one condition that forces "compute, then
  act," isolating reasoning from grounding.
- Fixed variables: single reasoning method (`cot_sc`, justified by [RAS]'s
  own finding that it's the most safety-robust), single model per
  modality, matched scene (4 color-distinguished cubes + tray).

### 4. Evaluation Protocol (≈500 words)
- TS/TSR/AETS definitions (Eq.-style, matching [RAS]'s notation for direct
  comparability) + the semi-automatic adaptation (2 operator numbers per
  trial + automatic step-count logging) and why full automation isn't
  possible on real hardware (no collision sensor, no vision-based
  final-state check) — state this as a methodological contribution in
  itself, not an apology.

### 5. Results (≈1,700 words) — reuse `05_benchmark.tex` tables/figures directly
- Headline table (overall TS/TSR/AETS by model).
- By-task breakdown table, with the arithmetic-task collapse as the
  centerpiece (30%/25% TSR vs. 100%/97%).
- **Add statistical rigor beyond the existing draft**: exact binomial CIs
  or Fisher's exact test per condition given n=10/cell; report effect
  sizes, not just point percentages — this is the single highest-value
  addition to strengthen this paper specifically for ICRA review.
- Plan-length/AETS comparison (LLM and VLM within ~1 step of each other —
  rules out "VLM just plans worse," sharpens that the gap is about
  correctness of the target, not planning quality).

### 6. Failure-Mode Analysis (≈1,000 words) — the qualitative core, push this hard
- Methodology: read all 31 non-empty operator notes rather than trusting
  aggregate numbers (state this as a deliberate methodological choice).
- The central finding, with the exact evidence chain: 13 "wrong cube"
  notes → 0 in tasks with a directly-named color → 12/13 in the arithmetic
  task → adjacency of red(2)/white(3) in the stated value mapping →
  off-by-one hypothesis, not a color-perception hypothesis.
- Present a full failure taxonomy (the 31 notes' categories, "wrong object
  chosen — reasoning/value-mapping" as 17/31, dominant by a wide margin) —
  reuse `failure_modes.png`.
- Explicit implication: benchmarking "grounding accuracy" by looking only
  at outcome correctness conflates two different failure sources; recommend
  future VLM-planner evaluations separate a grounding-accuracy probe from
  an object-selection/reasoning probe.

### 7. Discussion & Limitations (≈450 words)
- When would a camera help vs. hurt: hypothesize (from the data) that VLM
  grounding helps when the referent is stated directly and the scene is
  otherwise static/known (would predict LLM ≈ VLM there — and indeed
  `pp_easy` shows exactly that: 100%/100% both).
- Limitations per `00_foundation.md`.

### 8. Conclusion (≈150 words)

### References (~30–40; heavier on VLA/grounding literature than outline A,
lighter on the geometry-fix engineering citations)

## Reviewer self-check

| Dimension | Risk | Mitigation |
|---|---|---|
| "Just a benchmark table" read | High | Section 6 (qualitative failure analysis) must be the paper's center of gravity, not an appendix — lead review pass should ask "does this read like a discovery or a report card?" |
| Statistical power (n=10/cell) | High | Add CIs/exact tests (Sec. 5); frame claims at the level the data supports (directional + case-study depth, not "VLM is worse" as a general law) |
| Missing hybrid condition looks like an obvious gap a reviewer will ask for | Medium | Either add it (data-collection cost) or state explicitly in Limitations/Future Work that it's the next experiment, with a one-sentence prediction |
