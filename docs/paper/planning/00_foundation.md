# Paper Planning: Foundation

Shared analysis underpinning all three candidate outlines (`outline_A`,
`outline_B`, `outline_C`). Written 2026-07-29. Source material: this
project's git history (57 commits, 27 May – 22 July 2026), `docs/SESSION_CONTEXT.md`,
`docs/AUDIT_FINDINGS.md`, `docs/GRASP_GEOMETRY_PIPELINE.md`, the already-compiled
`docs/report/` (technical report by Matteo Nini), and two reference papers by
Favali, Sabattini & Villani (DISMI, UniMoRe):

- **[Sim]** *RoboReason Lab: An Accessible Simulation Framework for Foundation
  Models in Robotics* — a systems paper. CoppeliaSim, no real hardware, no
  camera/VLM. Contribution: a modular sim testbed + cost/latency/LoC/IPT
  benchmarking of 6 reasoning methods (Always-Act, FHP, ReAct, Self-Refine,
  ToT, CoT-SC).
- **[RAS]** *LLM-Based Reasoning for Robotic Planning: Robustness to Task and
  Environmental Complexity* — a benchmarking paper, preprint submitted to
  *Robotics and Autonomous Systems* (despite the filename saying "RO-MAN
  2025" — check with the authors/co-authors which venue is actually
  current). Symbolic simulation only (a laptop, no physics engine, no
  camera). Contribution: a task/environment complexity taxonomy (task
  length, goal specificity, affordance/perception load; affordance density,
  four classes of dynamic event) + TS/TSR/AETS metrics (Eqs. 14–16) + a
  720-trial comparison of the same 6 reasoning methods across 3 LLMs.
  Headline finding: CoT-SC is most robust; task complexity and dynamic
  disturbances (not model scale) drive failure.

---

## Venue

**ICRA 2027**: submissions due **15 September 2026** (~7 weeks from today),
conference 24–28 May 2027, Seoul. [IEEE ICRA 2027 Contribute](https://2027.ieee-icra.org/contribute/).
Topically a strong fit — real-hardware LLM/VLM manipulation studies are
squarely in scope, and "what breaks leaving simulation" is a well-received
framing at ICRA. The risk is entirely about time and about differentiation
from [RAS], not about fit.

Alternates if the co-author conversation lands elsewhere:
- **IROS 2027** — same tier, same fit, deadline ~March 2027 (6 more months).
- **RA-L w/ ICRA presentation option** — rolling submission, ~3-month
  review; less all-or-nothing than a hard deadline.
- **An ICRA/IROS LLM-for-robotics workshop** — 4–6 pages, deadlines
  typically Dec–Feb, much lower bar. Strong near-certain-acceptance
  stepping stone given how much diagnosed-failure-mode material already
  exists.
- **Robotics and Autonomous Systems** — the literal venue [RAS] is headed
  to; a "companion paper" submitted there would sit next to it directly.

## Self-plagiarism / redundant-publication note

All three candidate outlines below draw on the **same underlying asset**:
the 120-trial LLM-vs-VLM benchmark (`benchmark/results.csv`,
15–20 July 2026) and the same five sessions of hardware fixes. If more than
one of these papers is eventually submitted (even to different venues),
IEEE's overlap/redundant-publication policy requires each to have a
clearly distinct contribution and non-trivial new content beyond shared
data reuse — a shared-data footnote citing the sibling paper is standard
practice, but the three outlines below are written to have genuinely
different central claims (not just different framing of one result set) so
that this is achievable. Worth raising explicitly with co-authors before
committing to more than one.

## What differentiates this project from [Sim] and [RAS]

Both Favali papers are simulation-only — perception is a symbolic text
proxy in [RAS], a scripted CoppeliaSim scene in [Sim]. Neither paper's
agent ever computes real depth, real grasp width, or a real release height
from noisy sensor data, because none of those quantities exist in either
simulator. This project is the mirror image: a real UR5cb + OnRobot RG2 +
Orbbec camera with ChArUco extrinsic calibration, and five sessions of
hardware-only bugs that a symbolic benchmark structurally cannot surface:

1. **Geometry arithmetic, not reasoning, is the dominant real-hardware
   failure class.** Every LLM-mode prompt asks the model to compute
   `object_height` / `release_position.z` itself; this was repeatedly wrong
   on hardware (a recurring "~3cm+ release overshoot" bug across two
   sessions), root-caused to the model assuming a top-surface grasp when
   the executor actually grasps mid-body. Fixed by removing the arithmetic
   from the model entirely and computing it deterministically in code
   (`_fix_object_height`, `_fix_release_height`, footprint-containment
   matching, width-aware TCP offset calibration — see
   `docs/GRASP_GEOMETRY_PIPELINE.md`). This class of bug is invisible to
   [RAS]'s TS/TSR/AETS metrics because its symbolic simulator has no
   physical grasp geometry to get wrong in the first place.
2. **On real hardware, even the failures that* look* like a vision problem
   often aren't.** The 120-trial LLM-vs-VLM benchmark (already run,
   analyzed, and written up in `docs/report/sections/05_benchmark.tex`)
   found VLM success collapsing specifically on tasks requiring the model
   to *compute* which object to act on (30%/25% on `arith_easy`/`arith_hard`
   vs. 100%/97% for LLM) — and reading all 31 non-empty operator notes
   shows 12 of 13 "wrong cube" errors cluster exactly there, consistent
   with an off-by-one in value-mapping arithmetic (red=2, white=3, adjacent
   in the stated mapping), not camera/color grounding, since **zero** such
   errors occur when color is stated directly. A symbolic benchmark cannot
   distinguish "grounded the wrong pixel" from "computed the wrong target"
   because it never grounds pixels at all.
3. **A semi-automatic evaluation methodology was necessary and is itself a
   contribution.** [RAS]'s TS/TSR/AETS metrics are fully automatic because
   ground truth is always exactly known in a symbolic sim. On real
   hardware there is no collision sensor and no vision-based final-state
   check anywhere in this codebase (see `AUDIT_FINDINGS.md`), so the same
   metrics had to be adapted to a human-in-the-loop annotation protocol
   (2 operator-supplied numbers per trial: safe? / sub-tasks completed?)
   while keeping everything else (step counts, configs, errors) logged
   automatically. This adaptation — and its limits — is worth stating
   explicitly rather than glossing over.
4. **An audited, safety-relevant gap between "plans" and "acts safely."**
   `AUDIT_FINDINGS.md` documents dry-run-green-but-hardware-unsafe issues
   (trajectory failure reported as success, gripper commands that cannot
   fail, uncalibrated deprojection returning wrong-frame points silently)
   that only exist because this is a real actuated system. Neither Favali
   paper's evaluation touches actuator-level failure at all.

## Related work sketch (shared across outlines, adapt per framing)

Reuse citation targets already present in [Sim]'s reference list where
relevant (their numbering, re-verify before submission):
- **Foundation-model planning surveys**: Kim et al. 2024, Wang et al. 2025,
  Huang et al. 2024 (survey of LLM agent planning).
- **Grounding language in affordances**: Ahn et al. (SayCan) 2022, Huang
  et al. (zero-shot planners) 2022 — both symbolic/simulated or
  limited-real-world; useful contrast points for "affordance grounding
  assumed vs. measured."
- **Reasoning strategies**: ReAct (Yao et al. 2022), Tree-of-Thoughts (Yao
  et al. 2023), Self-Refine (Madaan et al. 2023; Zhou et al. ISR-LLM,
  ICRA 2024 — a directly relevant *ICRA* precedent worth citing explicitly
  for outline A/B), CoT-SC (Wang et al. 2022).
- **VLA / end-to-end**: cite as the alternative architecture this project
  deliberately avoids (same argument [RAS] makes in its §2 — decoupled
  Sense-Plan-Act preserves diagnosability), e.g. RT-2-class models,
  general VLA surveys.
- **Sim-to-real gap literature**: general robotics sim-to-real transfer
  work (not LLM-specific) is worth 2-3 citations to root the framing in an
  established concern rather than inventing the concept from scratch.
- **Direct comparison**: [Sim] and [RAS] themselves, cited explicitly and
  discussed at paragraph length in Related Work — not just listed.

A real literature search (Google Scholar / Semantic Scholar / arXiv,
15–20 more papers on real-hardware LLM manipulation specifically —
e.g. Code as Policies, VoxPoser, Inner Monologue, ProgPrompt, Text2Motion)
should be run by whoever drafts Related Work; this sketch is a starting
scaffold, not a completed search.

## Honest limitations (apply to all three framings)

- **n=120 trials, one robot, one gripper, one camera rig, one LLM
  (`nemotron-120b`), one VLM (`qwen3.6-27b`), one reasoning method
  (`cot_sc`).** Generalizability across embodiments/models is unproven —
  say so plainly; ICRA reviewers will ask if you don't.
- **No VLM_LLM hybrid arm in the benchmark yet** — flagged in the report as
  "the most direct extension, not started." Whether to add it before
  submission is a real scope/time tradeoff for the 7-week window (see each
  outline's effort note).
- **Semi-automatic annotation** introduces operator-observation as a
  dependency; inter-rater reliability (a second annotator on a subsample)
  is not currently measured and would strengthen any of the three papers
  if time allows.
