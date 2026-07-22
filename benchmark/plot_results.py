#!/usr/bin/env python3
"""Generate report-ready figures from benchmark/results.csv.

Usage:
    python3 benchmark/plot_results.py

Writes PNG figures (light background, presentation-ready) into
benchmark/figures/. Also prints the same aggregate tables to stdout so the
numbers behind each figure are easy to sanity-check without opening an
image viewer.

Palette and mark choices follow this project's dataviz conventions:
categorical hues assigned in fixed order (LLM = slot 1 blue, VLM = slot 2
aqua, always in that order across every figure), text kept in ink/muted
tones rather than series colors, hairline recessive gridlines, a legend
whenever two series are shown, and no dual-axis charts — different-scale
metrics (percentages vs. AETS vs. step counts) each get their own panel.
"""
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Patch

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_CSV = REPO_ROOT / 'benchmark' / 'results.csv'
FIGURES_DIR = REPO_ROOT / 'benchmark' / 'figures'

TASK_ORDER = ['pp_easy', 'pp_hard', 'sort_easy', 'sort_hard', 'arith_easy', 'arith_hard']
TASK_LABELS = {
    'pp_easy': 'Pick&Place\n(easy)', 'pp_hard': 'Pick&Place\n(hard)',
    'sort_easy': 'Sort/Stack\n(easy)', 'sort_hard': 'Sort/Stack\n(hard)',
    'arith_easy': 'Arithmetic\n(easy)', 'arith_hard': 'Arithmetic\n(hard)',
}
MODELS = ['LLM', 'VLM']

# Explicit, per-run_id classification of every non-empty `notes` entry into
# a primary failure mode — deliberately NOT a keyword classifier (an
# earlier keyword-only pass on this same data misattributed a reasoning
# failure as a vision/color-grounding one; see the arith_easy/arith_hard
# vs. pp_easy/pp_hard contrast in the conversation). Each line is the
# run_id, the assigned category, and the actual note it was read from, so
# any single call can be checked and corrected without re-reading all 31.
# When a note describes more than one issue (e.g. a wrong-object decision
# that *then* causes a collision), it's filed under the root cause, not
# the physical symptom.
REASONING = 'Wrong object chosen\n(reasoning/value-mapping)'
COLLISION = 'Collision / contact'
GRASP = 'Grasp / execution failure'
INCOMPLETE = 'Incomplete\n(object omitted entirely)'
NOTE_CATEGORY = {
    '20260717-125843-5407e6b8': REASONING,   # Picked the white cube instead of the red one
    '20260717-130018-22611c4b': COLLISION,   # only put orange on tray, failed line, contact between cubes
    '20260717-143712-031dfc79': COLLISION,   # end effector pushed the tray a little
    '20260717-144122-b8143ff3': GRASP,       # wasn't able to pick up the red cube
    '20260717-144248-843851d5': COLLISION,   # one cube in the line was over a previous block
    '20260717-145026-8b803eea': REASONING,   # orange correct, but also tried blue/white on tray (colliding)
    '20260717-154139-c1ee38c7': REASONING,   # put blue where white should go instead of line, then collided
    '20260717-155512-f2d31fb2': REASONING,   # Picked up the white cube instead of the red one
    '20260717-160207-0fbf305b': REASONING,   # orange correct, also put red+blue on tray (white correct)
    '20260717-164503-46015211': COLLISION,   # failed last block of line, collision with another block
    '20260717-165013-043657b0': COLLISION,   # small collision between cubes while releasing
    '20260717-165347-a9fc5ad6': REASONING,   # put blue on red instead of red on blue
    '20260717-170318-49f775cb': COLLISION,   # collision between cubes while putting them in line
    '20260717-170648-d712ea36': REASONING,   # Picked up the white cube instead of the red cube
    '20260717-170856-348df57e': REASONING,   # only white correct; blue/red/orange wrong; lots of collisions
    '20260717-172836-646d280c': COLLISION,   # small collision between cubes
    '20260717-173740-c54ef36d': REASONING,   # stacked blue on top of red instead of the reverse
    '20260717-173918-3c7def48': COLLISION,   # red + two line cubes correct, third collided/wrong position
    '20260717-174342-50c47c2e': REASONING,   # positioned blue on tray instead of red
    '20260717-174620-a8026b08': REASONING,   # put red+blue on tray (incorrect), didn't see white, orange left
    '20260717-175522-e38b053d': INCOMPLETE,  # only put red cube on tray, didn't put the rest in line
    '20260720-114719-07394d5c': GRASP,       # last cube fell off the tray
    '20260720-115950-ddf3c8ca': REASONING,   # reversed output: orange on table, red/blue/white on tray
    '20260720-123658-2c6578b0': REASONING,   # placed white cube on tray instead of red
    '20260720-123823-2c0fdc39': REASONING,   # red+blue on tray, orange+white on table, only white correct
    '20260720-124650-6549d255': INCOMPLETE,  # put red on tray but missed every other cube, no sorting
    '20260720-151750-1d0a0c74': REASONING,   # red+blue on tray wrongly, orange kept on table
    '20260720-153451-9d2c9f2a': GRASP,       # red cube slipped after grasping
    '20260720-154140-00b8f5ab': GRASP,       # failed to grasp the blue cube
    '20260720-154520-0a9099b6': REASONING,   # placed white cube on tray instead of red
    '20260720-154726-b9aa78e0': REASONING,   # wrong cube (blue) on tray, failed to grasp red, only white correct
}

# Categorical palette (fixed order, colorblind-validated — see
# dataviz skill references/palette.md). LLM always slot 1, VLM always
# slot 2, in every figure in this script.
COLOR = {'LLM': '#2a78d6', 'VLM': '#1baf7a'}
INK = '#0b0b0b'
INK_SECONDARY = '#52514e'
INK_MUTED = '#898781'
GRID = '#e1e0d9'
SURFACE = '#fcfcfb'

plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.size': 11,
    'text.color': INK,
    'axes.edgecolor': GRID,
    'axes.labelcolor': INK_SECONDARY,
    'xtick.color': INK_MUTED,
    'ytick.color': INK_MUTED,
    'figure.facecolor': SURFACE,
    'axes.facecolor': SURFACE,
    'savefig.facecolor': SURFACE,
})


def load_rows():
    with open(RESULTS_CSV, newline='') as f:
        return list(csv.DictReader(f))


def grouped(rows, key_fn):
    out = defaultdict(list)
    for r in rows:
        out[key_fn(r)].append(r)
    return out


def add_model_legend(fig):
    """A shared, figure-level legend placed outside the plot area (top
    right), rather than an in-axes legend — several of these charts have
    bars that reach 100%/the axis max in the easy conditions, so there's
    no corner of the plot area guaranteed empty for a per-axes legend to
    sit in without covering a bar.
    """
    handles = [Patch(facecolor=COLOR[m], label=m) for m in MODELS]
    fig.legend(
        handles=handles, loc='upper right', bbox_to_anchor=(0.995, 1.0),
        frameon=False, fontsize=9.5, ncol=1,
    )


def style_axes(ax, ylabel=None):
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['bottom'].set_color(GRID)
    ax.yaxis.grid(True, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis='both', length=0)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)


def grouped_bar(ax, categories, cat_labels, series_values, value_fmt='{:.0f}', ymax=None):
    """series_values: {'LLM': [...], 'VLM': [...]} aligned to `categories`."""
    n = len(categories)
    width = 0.34
    gap = 0.04
    x = range(n)
    overall_max = max(v for vals in series_values.values() for v in vals)
    # Always reserve headroom above the tallest bar (for its value label) and
    # set it explicitly, whether ymax was given (percentage charts pinned to
    # ~110) or not (AETS, whose scale isn't fixed) — the label text needs
    # clearance from both the bar top and the axes title above it.
    top = ymax if ymax else overall_max * 1.22
    for i, model in enumerate(MODELS):
        offset = (i - 0.5) * (width + gap)
        xs = [xi + offset for xi in x]
        vals = series_values[model]
        bars = ax.bar(xs, vals, width=width, color=COLOR[model], label=model, zorder=3)
        for b, v in zip(bars, vals):
            ax.text(
                b.get_x() + b.get_width() / 2, b.get_height() + top * 0.02,
                value_fmt.format(v), ha='center', va='bottom', fontsize=8.5, color=INK_SECONDARY,
            )
    ax.set_xticks(list(x))
    ax.set_xticklabels(cat_labels, fontsize=9)
    ax.set_ylim(0, top)


def fig_ts_tsr_by_task(rows, task_order=None, filename='ts_tsr_by_task.png', suptitle=None):
    """task_order defaults to all 6 conditions; pass a subset (e.g. just
    pick&place + sort/stack) for a scoped version of this chart — see the
    *_pp_sort variants, generated for the seminar deck, which deliberately
    doesn't cover the arithmetic task in this session.
    """
    task_order = task_order or TASK_ORDER
    g = grouped(rows, lambda r: (r['model_label'], r['task_id']))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, metric, title, ymax in (
        (axes[0], 'TS', 'Task Safety (TS%) — no collision/damage', 110),
        (axes[1], 'TSR', 'Task Success Rate (TSR%)', 110),
    ):
        series = {
            m: [100 * mean(float(r[metric]) for r in g[(m, t)]) for t in task_order]
            for m in MODELS
        }
        grouped_bar(ax, task_order, [TASK_LABELS[t] for t in task_order], series, '{:.0f}', ymax)
        style_axes(ax, ylabel='%')
        ax.set_title(title, fontsize=11.5, color=INK, loc='left', pad=10)
    fig.suptitle(
        suptitle or 'Safety and success rate by task — LLM vs VLM (n=10 per bar)',
        fontsize=13, x=0.02, ha='left',
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    add_model_legend(fig)
    fig.savefig(FIGURES_DIR / filename, dpi=200)
    plt.close(fig)


def fig_aets_by_task(rows):
    g = grouped(rows, lambda r: (r['model_label'], r['task_id']))
    fig, ax = plt.subplots(figsize=(8, 4.2))
    series = {
        m: [mean(float(r['AETS']) for r in g[(m, t)]) for t in TASK_ORDER]
        for m in MODELS
    }
    grouped_bar(ax, TASK_ORDER, [TASK_LABELS[t] for t in TASK_ORDER], series, '{:.3f}', None)
    style_axes(ax, ylabel='AETS (sub-tasks completed per action)')
    ax.set_title('Action Efficiency on Task Success (AETS) by task', fontsize=12, color=INK, loc='left', pad=10)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    add_model_legend(fig)
    fig.savefig(FIGURES_DIR / 'aets_by_task.png', dpi=200)
    plt.close(fig)


def fig_easy_vs_hard(rows):
    g = grouped(rows, lambda r: (r['model_label'], r['difficulty']))
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.2))
    diffs = ['easy', 'hard']
    for ax, metric, title in (
        (axes[0], 'TS', 'Task Safety (TS%)'),
        (axes[1], 'TSR', 'Task Success Rate (TSR%)'),
    ):
        series = {
            m: [100 * mean(float(r[metric]) for r in g[(m, d)]) for d in diffs]
            for m in MODELS
        }
        grouped_bar(ax, diffs, ['Easy', 'Hard'], series, '{:.0f}', 110)
        style_axes(ax, ylabel='%')
        ax.set_title(title, fontsize=11.5, color=INK, loc='left', pad=10)
    fig.suptitle('Effect of task complexity — easy vs. hard (n=30 per bar)', fontsize=13, x=0.02, ha='left')
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    add_model_legend(fig)
    fig.savefig(FIGURES_DIR / 'easy_vs_hard.png', dpi=200)
    plt.close(fig)


def fig_steps_by_task(rows):
    g = grouped(rows, lambda r: (r['model_label'], r['task_id']))
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    n = len(TASK_ORDER)
    width = 0.34
    gap = 0.04
    x = range(n)

    per_model = {}
    for model in MODELS:
        vals = [[int(r['steps_executed']) for r in g[(model, t)]] for t in TASK_ORDER]
        per_model[model] = {'means': [mean(v) for v in vals], 'stds': [pstdev(v) for v in vals]}
    # Headroom must clear the tallest error-bar cap (mean + std), not just
    # the tallest bar, or the label/title crowd the whiskers.
    top = max(m + s for d in per_model.values() for m, s in zip(d['means'], d['stds'])) * 1.22

    for i, model in enumerate(MODELS):
        offset = (i - 0.5) * (width + gap)
        xs = [xi + offset for xi in x]
        means, stds = per_model[model]['means'], per_model[model]['stds']
        bars = ax.bar(
            xs, means, width=width, yerr=stds, capsize=3, color=COLOR[model],
            label=model, zorder=3, error_kw={'ecolor': INK_MUTED, 'elinewidth': 1},
        )
        for b, m, s in zip(bars, means, stds):
            ax.text(
                b.get_x() + b.get_width() / 2, m + s + top * 0.02,
                f'{m:.1f}', ha='center', va='bottom', fontsize=8.5, color=INK_SECONDARY,
            )
    ax.set_xticks(list(x))
    ax.set_xticklabels([TASK_LABELS[t] for t in TASK_ORDER], fontsize=9)
    ax.set_ylim(0, top)
    style_axes(ax, ylabel='Steps executed (mean ± std)')
    ax.set_title('Plan length by task — LLM vs VLM', fontsize=12, color=INK, loc='left', pad=10)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    add_model_legend(fig)
    fig.savefig(FIGURES_DIR / 'steps_by_task.png', dpi=200)
    plt.close(fig)


def fig_issue_rate_by_task(rows):
    """Objective, non-interpretive: fraction of trials with a non-empty
    `notes` field (an observed issue was written down), by task/model. No
    attempt is made here to categorize *why* — see the conversation/
    writeup for the qualitative breakdown, which needs a human read of
    each note rather than a keyword classifier.
    """
    g = grouped(rows, lambda r: (r['model_label'], r['task_id']))
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    series = {
        m: [100 * mean(1 if r['notes'].strip() else 0 for r in g[(m, t)]) for t in TASK_ORDER]
        for m in MODELS
    }
    grouped_bar(ax, TASK_ORDER, [TASK_LABELS[t] for t in TASK_ORDER], series, '{:.0f}', 110)
    style_axes(ax, ylabel='% of trials with a recorded issue')
    ax.set_title('Observed-issue rate by task (non-empty operator notes)', fontsize=12, color=INK, loc='left', pad=10)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    add_model_legend(fig)
    fig.savefig(FIGURES_DIR / 'issue_rate_by_task.png', dpi=200)
    plt.close(fig)


def fig_overall_summary(rows, task_filter=None, filename='overall_summary.png', suptitle=None):
    """task_filter: optional set of task_ids to restrict the average to
    (e.g. just pick&place + sort/stack) — see the *_pp_sort variant used
    for the seminar deck.
    """
    if task_filter:
        rows = [r for r in rows if r['task_id'] in task_filter]
    n_per_bar = len(rows) // len(MODELS)
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.8))
    metrics = [('TS', 'Task Safety', '%', 110, '{:.1f}'), ('TSR', 'Task Success Rate', '%', 110, '{:.1f}'),
               ('AETS', 'Action Efficiency', '', None, '{:.3f}')]
    for ax, (metric, title, unit, ymax, fmt) in zip(axes, metrics):
        vals = []
        for m in MODELS:
            xs = [float(r[metric]) for r in rows if r['model_label'] == m]
            v = 100 * mean(xs) if metric != 'AETS' else mean(xs)
            vals.append(v)
        top = ymax if ymax else max(vals) * 1.25
        bars = ax.bar(MODELS, vals, width=0.55, color=[COLOR[m] for m in MODELS], zorder=3)
        for b, v in zip(bars, vals):
            ax.text(
                b.get_x() + b.get_width() / 2, b.get_height() + top * 0.02,
                fmt.format(v) + unit, ha='center', va='bottom', fontsize=10, color=INK, fontweight='bold',
            )
        style_axes(ax)
        ax.set_ylim(0, top)
        ax.set_title(title, fontsize=11.5, color=INK, pad=10)
        ax.tick_params(axis='x', labelsize=10)
    fig.suptitle(
        suptitle or f'Overall headline results — LLM vs VLM (n={n_per_bar} per bar)',
        fontsize=13, x=0.02, ha='left',
    )
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(FIGURES_DIR / filename, dpi=200)
    plt.close(fig)


def fig_failure_modes(rows):
    """What kind of issue was actually observed, for every trial with a
    written note — see NOTE_CATEGORY above for the per-run_id assignment
    and the reasoning behind each one.
    """
    counts = defaultdict(lambda: defaultdict(int))  # category -> model -> count
    for r in rows:
        cat = NOTE_CATEGORY.get(r['run_id'])
        if cat:
            counts[cat][r['model_label']] += 1

    categories = [REASONING, COLLISION, GRASP, INCOMPLETE]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    y = range(len(categories))
    height = 0.34
    gap = 0.04
    max_count = max(counts[c][m] for c in categories for m in MODELS)
    top = max_count * 1.2
    for i, model in enumerate(MODELS):
        offset = (0.5 - i) * (height + gap)
        ys = [yi + offset for yi in y]
        vals = [counts[c][model] for c in categories]
        bars = ax.barh(ys, vals, height=height, color=COLOR[model], zorder=3)
        for b, v in zip(bars, vals):
            if v == 0:
                continue
            ax.text(
                b.get_width() + top * 0.015, b.get_y() + b.get_height() / 2,
                str(v), ha='left', va='center', fontsize=9, color=INK_SECONDARY,
            )
    ax.set_yticks(list(y))
    ax.set_yticklabels(categories, fontsize=9.5)
    ax.invert_yaxis()
    ax.set_xlim(0, top)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.xaxis.grid(True, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis='both', length=0)
    ax.set_xlabel('Number of trials (out of 31 with a recorded note)', fontsize=10)
    ax.set_title(
        'What actually went wrong, by category (from operator notes)',
        fontsize=12, color=INK, loc='left', pad=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    add_model_legend(fig)
    fig.savefig(FIGURES_DIR / 'failure_modes.png', dpi=200)
    plt.close(fig)


def print_tables(rows):
    g = grouped(rows, lambda r: (r['model_label'], r['task_id']))
    print(f"{'Model':<5} {'Task':<10} {'N':>3} {'TS%':>6} {'TSR%':>6} {'AETS':>7} {'steps':>7}")
    for m in MODELS:
        for t in TASK_ORDER:
            grp = g[(m, t)]
            n = len(grp)
            ts = 100 * mean(float(r['TS']) for r in grp)
            tsr = 100 * mean(float(r['TSR']) for r in grp)
            aets = mean(float(r['AETS']) for r in grp)
            steps = mean(int(r['steps_executed']) for r in grp)
            print(f'{m:<5} {t:<10} {n:>3} {ts:>5.1f}% {tsr:>5.1f}% {aets:>7.4f} {steps:>7.2f}')
    print()
    for m in MODELS:
        xs = [r for r in rows if r['model_label'] == m]
        n = len(xs)
        ts = 100 * mean(float(r['TS']) for r in xs)
        tsr = 100 * mean(float(r['TSR']) for r in xs)
        aets = mean(float(r['AETS']) for r in xs)
        print(f'{m} overall: N={n} TS%={ts:.1f} TSR%={tsr:.1f} AETS={aets:.4f}')


def main():
    rows = load_rows()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    print_tables(rows)
    fig_ts_tsr_by_task(rows)
    fig_aets_by_task(rows)
    fig_easy_vs_hard(rows)
    fig_steps_by_task(rows)
    fig_issue_rate_by_task(rows)
    fig_overall_summary(rows)
    fig_failure_modes(rows)

    # Seminar-scoped variants: pick&place + sort/stack only, no arithmetic
    # task — used for the general-audience talk, which doesn't cover the
    # arithmetic benchmark. The full figures above are for the lab-internal
    # deck; these are additional, not replacements.
    pp_sort_tasks = ['pp_easy', 'pp_hard', 'sort_easy', 'sort_hard']
    fig_ts_tsr_by_task(
        rows, task_order=pp_sort_tasks, filename='ts_tsr_by_task_pp_sort.png',
        suptitle='Safety and success rate — Pick&Place and Sort/Stack (n=10 per bar)',
    )
    fig_overall_summary(
        rows, task_filter=set(pp_sort_tasks), filename='overall_summary_pp_sort.png',
        suptitle='Headline results — Pick&Place and Sort/Stack tasks',
    )
    print(f'\nFigures written to {FIGURES_DIR}')


if __name__ == '__main__':
    main()
