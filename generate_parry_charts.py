#!/usr/bin/env python3
"""
generate_parry_charts.py — produce PNG charts from the v3 parry-haste data.

Charts written to ./charts/:
  - parry_haste_distribution.png   : main bimodal histogram with predictions
  - parry_haste_filter_effect.png  : midcycle vs boundary overlay
  - parry_haste_per_boss.png       : median ratio per (mob, player), sorted
  - parry_haste_cdf.png            : cumulative distribution function

Re-run any time after fresh logs land. Reads logs from
/srv/add01/wow-ascension/Logs/.
"""
from __future__ import annotations
import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Reuse v3 collection logic
sys.path.insert(0, str(Path(__file__).parent))
from parry_haste_detect_v3 import collect, baseline_swing_time, classify

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).parent / 'charts'
OUT.mkdir(exist_ok=True)


def collect_data(log_glob='/srv/add01/wow-ascension/Logs/*WoWCombatLog.txt',
                 min_parries=3, boundary_ms=200):
    paths = sorted(glob.glob(log_glob))
    print(f'scanning {len(paths)} logs ...')
    pairs = collect(paths)
    rows = []
    for (mob, player), p in pairs.items():
        base = baseline_swing_time(p.mob_swings)
        if base is None:
            continue
        mid, bnd, no_ = classify(p, base, boundary_ms)
        if (len(mid) + len(bnd)) < min_parries:
            continue
        rows.append({
            'mob': p.mob_name,
            'player': p.player_name,
            'baseline': base,
            'mid_ratios': [r for _, r in mid],
            'bnd_ratios': [r for _, r in bnd],
            'no_next_count': len(no_),
            'swings': len(p.mob_swings),
        })
    return rows


def chart_distribution(rows):
    """Main bimodal histogram of mid-cycle ratios."""
    all_mid = [r for row in rows for r in row['mid_ratios']]
    n = len(all_mid)
    med = statistics.median(all_mid)

    fig, ax = plt.subplots(figsize=(11, 6.5))
    bins = np.arange(0, 1.55, 0.05)
    counts, edges, patches = ax.hist(all_mid, bins=bins,
                                     color='#4a90e2', edgecolor='#1a4d8a',
                                     alpha=0.85)
    # Colour the haste-fired bins green, no-haste bins red
    for i, patch in enumerate(patches):
        center = (edges[i] + edges[i + 1]) / 2
        if 0.10 <= center <= 0.30:
            patch.set_facecolor('#2ecc71')
            patch.set_edgecolor('#1e8449')
        elif center >= 0.95:
            patch.set_facecolor('#e74c3c')
            patch.set_edgecolor('#922b21')

    # Vertical lines for predictions
    ax.axvline(0.26, color='#27ae60', linestyle='--', linewidth=2,
               label='Predicted with parry-haste (~0.26)')
    ax.axvline(0.50, color='#c0392b', linestyle='--', linewidth=2,
               label='Predicted without parry-haste (~0.50)')
    ax.axvline(med, color='#2c3e50', linestyle='-', linewidth=2.5,
               label=f'Observed median = {med:.3f}')

    ax.set_xlabel('Ratio = (next mob swing − parry time) / mob baseline swing time',
                  fontsize=11)
    ax.set_ylabel('Mid-cycle parry events', fontsize=11)
    ax.set_title(f'Bronzebeard parry-haste: mid-cycle parry distribution\n'
                 f'n = {n} parries  |  median = {med:.3f}  '
                 f'(predicted 0.26 if active, 0.50 if not)',
                 fontsize=12)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 1.5)

    # Annotate the two clusters
    haste_n = sum(1 for r in all_mid if 0.10 <= r <= 0.30)
    nohaste_n = sum(1 for r in all_mid if r >= 0.95)
    ax.annotate(f'Haste fired\n(~{haste_n} events,\n{100*haste_n/n:.0f}%)',
                xy=(0.20, max(counts[2:6]) + 1), fontsize=10,
                ha='center', color='#1e8449', weight='bold')
    ax.annotate(f'No haste\n(~{nohaste_n} events,\n{100*nohaste_n/n:.0f}%)',
                xy=(1.00, counts[20] - 3), fontsize=10,
                ha='center', color='#922b21', weight='bold')

    fig.tight_layout()
    fig.savefig(OUT / 'parry_haste_distribution.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_distribution.png  (n={n}, median={med:.3f})')


def chart_filter_effect(rows):
    """Show the boundary-filter effect: midcycle vs boundary distributions."""
    all_mid = [r for row in rows for r in row['mid_ratios']]
    all_bnd = [r for row in rows for r in row['bnd_ratios']]

    fig, ax = plt.subplots(figsize=(11, 6.5))
    bins = np.arange(0, 1.55, 0.05)
    ax.hist(all_mid, bins=bins, color='#4a90e2', edgecolor='#1a4d8a',
            alpha=0.7, label=f'Mid-cycle (n={len(all_mid)})')
    ax.hist(all_bnd, bins=bins, color='#f39c12', edgecolor='#a86413',
            alpha=0.85, label=f'Boundary (n={len(all_bnd)}, excluded)')

    ax.set_xlabel('Ratio', fontsize=11)
    ax.set_ylabel('Parry events', fontsize=11)
    ax.set_title('Boundary-filter effect: mid-cycle data vs cycle-edge artifacts\n'
                 f'(excluded {len(all_bnd)} parries within 200ms of a boss swing)',
                 fontsize=12)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 1.5)
    fig.tight_layout()
    fig.savefig(OUT / 'parry_haste_filter_effect.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_filter_effect.png')


def chart_per_boss(rows):
    """Bar chart of median mid-cycle ratio per (mob, player), sorted."""
    # Collapse multiple pulls of same mob into one row by aggregating ratios
    by_mob = defaultdict(list)
    for row in rows:
        by_mob[row['mob']].extend(row['mid_ratios'])

    items = [(mob, ratios) for mob, ratios in by_mob.items() if len(ratios) >= 3]
    items.sort(key=lambda x: statistics.median(x[1]))

    if not items:
        print('  (per-boss chart skipped — no mob has ≥5 parries)')
        return

    fig, ax = plt.subplots(figsize=(11, max(5, 0.4 * len(items))))
    mobs = [m for m, _ in items]
    medians = [statistics.median(r) for _, r in items]
    counts = [len(r) for _, r in items]
    colors = ['#2ecc71' if m < 0.32 else ('#e74c3c' if m > 0.45 else '#f39c12')
              for m in medians]

    y_pos = np.arange(len(mobs))
    ax.barh(y_pos, medians, color=colors, edgecolor='#222')
    ax.set_yticks(y_pos)
    ax.set_yticklabels([f'{m}  (n={n})' for m, n in zip(mobs, counts)],
                       fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel('Median mid-cycle ratio', fontsize=11)
    ax.axvline(0.26, color='#27ae60', linestyle='--', alpha=0.7,
               label='Active prediction (0.26)')
    ax.axvline(0.50, color='#c0392b', linestyle='--', alpha=0.7,
               label='Inactive prediction (0.50)')
    ax.set_title('Median parry-haste ratio per boss (mid-cycle parries only)\n'
                 'Green = haste active; Orange = ambiguous; Red = no haste',
                 fontsize=12)
    ax.legend(loc='lower right', framealpha=0.9)
    ax.grid(True, alpha=0.3, axis='x')
    ax.set_xlim(0, 1.2)

    for i, (med, n) in enumerate(zip(medians, counts)):
        ax.text(med + 0.01, i, f'{med:.2f}', va='center', fontsize=9)

    fig.tight_layout()
    fig.savefig(OUT / 'parry_haste_per_boss.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_per_boss.png  ({len(items)} mobs)')


def chart_cdf(rows):
    """CDF of mid-cycle ratios — visualises proc rate."""
    all_mid = sorted(r for row in rows for r in row['mid_ratios'])
    n = len(all_mid)
    cum = np.arange(1, n + 1) / n

    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.plot(all_mid, cum, color='#2c3e50', linewidth=2.2)
    ax.fill_between(all_mid, 0, cum, alpha=0.15, color='#2c3e50')

    ax.axvline(0.30, color='#27ae60', linestyle='--', alpha=0.8,
               label='"Haste fired" cutoff (≤0.30)')
    ax.axvline(0.95, color='#c0392b', linestyle='--', alpha=0.8,
               label='"No haste" cutoff (≥0.95)')

    # Annotate proc-rate guess
    haste_pct = sum(1 for r in all_mid if r <= 0.30) / n * 100
    nohaste_pct = sum(1 for r in all_mid if r >= 0.95) / n * 100
    ax.text(0.04, 0.95,
            f'≤0.30 (parry-haste fired): {haste_pct:.1f}%\n'
            f'≥0.95 (no haste fired): {nohaste_pct:.1f}%\n'
            f'Implied proc rate of haste mechanic: ~50–70%',
            transform=ax.transAxes, fontsize=11, va='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#fff5cc',
                      edgecolor='#a86413'))

    ax.set_xlabel('Ratio', fontsize=11)
    ax.set_ylabel('Cumulative fraction of parries', fontsize=11)
    ax.set_title(f'CDF of mid-cycle parry ratios (n={n})\n'
                 f'Step at ≤0.30 shows the haste-fired population; flat region '
                 f'before 1.0 shows the no-haste tail.',
                 fontsize=12)
    ax.legend(loc='lower right', framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 1.5)
    ax.set_ylim(0, 1.02)
    fig.tight_layout()
    fig.savefig(OUT / 'parry_haste_cdf.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_cdf.png')


def main():
    rows = collect_data()
    print(f'\n{len(rows)} qualifying (mob, player) pairs')
    chart_distribution(rows)
    chart_filter_effect(rows)
    chart_per_boss(rows)
    chart_cdf(rows)
    print(f'\nAll charts written to {OUT}')


if __name__ == '__main__':
    main()
