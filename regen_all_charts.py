#!/usr/bin/env python3
"""regen_all_charts.py — single source of truth for all parry-haste charts.

Re-renders every chart in combatlog-tools/charts/ with consistent provenance
footer (data source counts, scrape date, methodology).
"""
from __future__ import annotations
import datetime as dt
import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parry_haste_detect_v3 import collect, baseline_swing_time, classify

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mp
import numpy as np

HERE = Path(__file__).parent
CHARTS = HERE / 'charts'
CHARTS.mkdir(exist_ok=True)
CACHE = HERE / 'ascensionlogs_data'

TODAY = dt.date.today().isoformat()

# Official WoW class colors (3.3.5-era palette)
CLASS_COLOR = {
    'Druid':   '#FF7D0A',  # orange
    'Hunter':  '#ABD473',  # green
    'Mage':    '#69CCF0',  # cyan
    'Paladin': '#F58CBA',  # pink
    'Priest':  '#FFFFFF',  # white (use grey edges to keep visible)
    'Rogue':   '#FFF569',  # yellow
    'Shaman':  '#0070DE',  # blue
    'Warlock': '#9482C9',  # purple
    'Warrior': '#C79C6E',  # tan
    '?':       '#7f8c8d',  # neutral grey
}


# ============== load all data + compute provenance ==============

def load_local():
    """Bronzebeard local Combat-Log files."""
    local_paths = sorted(glob.glob('/srv/add01/wow-ascension/Logs/*WoWCombatLog.txt'))
    pairs = collect(local_paths)
    rows = []
    all_mid = []
    all_bnd = []
    no_next_total = 0
    per_boss = defaultdict(list)
    for (mob, player), p in pairs.items():
        base = baseline_swing_time(p.mob_swings)
        if base is None:
            continue
        mid, bnd, no_ = classify(p, base, 200)
        if (len(mid) + len(bnd)) < 3:
            continue
        rows.append({'mob': p.mob_name, 'player': p.player_name,
                     'baseline': base, 'mid': mid, 'bnd': bnd, 'no_': no_})
        all_mid.extend(r for _, r in mid)
        all_bnd.extend(r for _, r in bnd)
        no_next_total += len(no_)
        per_boss[p.mob_name].extend(r for _, r in mid)
    return {
        'log_files': len(local_paths),
        'pairs_qualifying': len(rows),
        'mid_ratios': all_mid,
        'bnd_count': len(all_bnd),
        'no_next_count': no_next_total,
        'per_boss': dict(per_boss),
        'rows': rows,
    }


def load_asclogs():
    """ascensionlogs.gg cached data."""
    enc_index = {}
    for rp in CACHE.glob('report_*.json'):
        try: rep = json.loads(rp.read_text())
        except: continue
        rid = rep.get('report', {}).get('id')
        for e in (rep.get('encounters') or []):
            enc_index[e['id']] = (rid, e)

    timeline_files = list(CACHE.glob('timeline_*_filtered.json'))
    n_reports = len({rid for rid, _ in enc_index.values()})

    all_mid = []; bnd_count = 0; no_next_count = 0
    per_boss = defaultdict(list)
    per_tank = defaultdict(lambda: {'mid': [], 'class': None, 'pulls': 0,
                                    'minutes': 0, 'bosses': set()})
    encounters_done = 0
    for tl in timeline_files:
        try:
            enc_id = int(tl.stem.split('_')[1])
        except:
            continue
        if enc_id not in enc_index:
            continue
        rid, meta = enc_index[enc_id]
        if not meta.get('success'):
            continue
        boss = meta.get('name', '')
        duration_s = float(meta.get('duration_seconds') or 0)
        try: ev = json.loads(tl.read_text()).get('events') or []
        except: continue
        encounters_done += 1
        swings_per = defaultdict(list)
        parries_per = defaultdict(list)
        class_per = {}
        for e in ev:
            if (e.get('source_name') == boss and e.get('target_type') == 'player'
                    and e.get('spell_name') == 'Auto Attack'):
                swings_per[e['target_name']].append(e['timestamp_ms'])
            if (e.get('event_type') == 'parry' and e.get('target_name') == boss
                    and e.get('source_type') == 'player'):
                parries_per[e['source_name']].append(e['timestamp_ms'])
                class_per[e['source_name']] = e.get('source_class') or class_per.get(e['source_name'])
        for tank, swings in swings_per.items():
            sw = sorted(swings); pa = sorted(parries_per.get(tank, []))
            if len(sw) < 8 or not pa: continue
            iv = [(sw[i]-sw[i-1])/1000 for i in range(1, len(sw))
                  if 0.3 < (sw[i]-sw[i-1])/1000 < 6.0]
            if len(iv) < 8: continue
            base = statistics.median(iv)
            mid = []
            for tp in pa:
                nxt = next((s for s in sw if s > tp), None)
                if nxt is None:
                    no_next_count += 1; continue
                delta = (nxt - tp) / 1000
                if delta > 2.0 * base:
                    no_next_count += 1; continue
                prev = next((s for s in reversed(sw) if s < tp), None)
                if (prev is not None and (tp-prev) < 200) or (nxt-tp) < 200:
                    bnd_count += 1; continue
                mid.append(delta / base)
            if not mid: continue
            all_mid.extend(mid)
            per_boss[boss].extend(mid)
            t = per_tank[tank]
            t['mid'].extend(mid)
            t['pulls'] += 1
            t['minutes'] += duration_s / 60
            t['bosses'].add(boss)
            if class_per.get(tank): t['class'] = class_per[tank]
    return {
        'reports': n_reports,
        'encounters_analysed': encounters_done,
        'mid_ratios': all_mid,
        'bnd_count': bnd_count,
        'no_next_count': no_next_count,
        'per_boss': dict(per_boss),
        'per_tank': dict(per_tank),
    }


# ============== chart helpers ==============

def add_footer(fig, lines, fontsize=8):
    """Stamp a small provenance footer at bottom of figure."""
    txt = '\n'.join(lines)
    fig.text(0.5, 0.005, txt, ha='center', va='bottom', fontsize=fontsize,
             color='#444', alpha=0.9, fontfamily='monospace')


def base_footer(local, asc):
    return [
        f'Sources: {asc["encounters_analysed"]} encounters from {asc["reports"]} '
        f'public reports on ascensionlogs.gg + {local["log_files"]} local '
        f'Bronzebeard combat-log files (Harvia guild).',
        f'Methodology: v3 — 200ms cycle-boundary filter excludes log-resolution '
        f'artefacts. Generated {TODAY}.',
    ]


# ============== charts ==============

def chart_distribution(local, asc):
    """Local Bronzebeard mid-cycle parry distribution (the original chart)."""
    rs = local['mid_ratios']
    n = len(rs)
    med = statistics.median(rs)
    fig, ax = plt.subplots(figsize=(11, 7))
    bins = np.arange(0, 1.55, 0.05)
    counts, edges, patches = ax.hist(rs, bins=bins, color='#4a90e2',
                                     edgecolor='#1a4d8a', alpha=0.85)
    for i, patch in enumerate(patches):
        c = (edges[i] + edges[i+1]) / 2
        if 0.10 <= c <= 0.30: patch.set_facecolor('#2ecc71')
        elif c >= 0.95: patch.set_facecolor('#e74c3c')
    ax.axvline(0.26, color='#27ae60', ls='--', lw=2, label='Active prediction (0.26)')
    ax.axvline(0.50, color='#c0392b', ls='--', lw=2, label='No-haste prediction (0.50)')
    ax.axvline(med, color='#2c3e50', lw=2.5, label=f'Observed median = {med:.3f}')
    ax.set_xlabel('Ratio = (next mob swing − parry time) / mob baseline swing time',
                  fontsize=11)
    ax.set_ylabel('Mid-cycle parry events', fontsize=11)
    ax.set_title(f'Bronzebeard local logs — mid-cycle parry distribution\n'
                 f'n = {n}  |  median = {med:.3f}  '
                 f'|  excluded {local["bnd_count"]} cycle-boundary, '
                 f'{local["no_next_count"]} no-next-swing', fontsize=12)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.grid(True, alpha=0.3); ax.set_xlim(0, 1.5)
    add_footer(fig, base_footer(local, asc))
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(CHARTS / 'parry_haste_distribution.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_distribution.png')


def chart_local_vs_asclogs(local, asc):
    """Side-by-side: local vs asclogs distribution."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.5), sharey=True)
    bins = np.arange(0, 1.55, 0.05)
    for ax, data, src, color, edge in [
        (ax1, local['mid_ratios'], 'Bronzebeard local logs', '#2ecc71', '#1e8449'),
        (ax2, asc['mid_ratios'], 'ascensionlogs.gg public reports', '#3498db', '#1a5585'),
    ]:
        n = len(data); med = statistics.median(data)
        ax.hist(data, bins=bins, color=color, edgecolor=edge, alpha=0.85,
                weights=np.ones(n)/n)
        ax.axvline(0.26, color='#27ae60', ls='--', lw=2, label='Active (0.26)')
        ax.axvline(0.50, color='#c0392b', ls='--', lw=2, label='No-haste (0.50)')
        ax.axvline(med, color='#2c3e50', lw=2.5, label=f'Median = {med:.3f}')
        ax.set_title(f'{src}\nn = {n}  |  median = {med:.3f}', fontsize=12)
        ax.set_xlabel('Ratio'); ax.legend(loc='upper right', fontsize=9, framealpha=0.9)
        ax.grid(True, alpha=0.3); ax.set_xlim(0, 1.5)
    ax1.set_ylabel('Fraction of parries')
    plt.suptitle('Parry-haste distribution: Bronzebeard local vs ascensionlogs',
                 fontsize=13, y=1.0)
    add_footer(fig, base_footer(local, asc))
    fig.tight_layout(rect=[0, 0.05, 1, 0.97])
    fig.savefig(CHARTS / 'parry_haste_local_vs_asclogs.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_local_vs_asclogs.png')


def chart_by_class(local, asc):
    """5-panel class histogram."""
    by_class = defaultdict(list)
    for tank, d in asc['per_tank'].items():
        if not d['mid']: continue
        cls = d['class'] or '?'
        by_class[cls].extend(d['mid'])
    panels = [
        ('Druid (Subd local)', local['mid_ratios'], CLASS_COLOR['Druid']),
        ('Paladin', by_class.get('Paladin', []), CLASS_COLOR['Paladin']),
        ('Shaman',  by_class.get('Shaman',  []), CLASS_COLOR['Shaman']),
        ('Druid (asclogs)', by_class.get('Druid', []), CLASS_COLOR['Druid']),
        ('Warrior', by_class.get('Warrior', []), CLASS_COLOR['Warrior']),
    ]
    # Use ONLY Subd from local for the leftmost panel
    subd_only = []
    for r in local['rows']:
        if r['player'] == 'Subd':
            subd_only.extend(rr for _, rr in r['mid'])
    panels[0] = ('Druid (Subd local)', subd_only, CLASS_COLOR['Druid'])

    fig, axes = plt.subplots(1, 5, figsize=(20, 5.5), sharey=True)
    bins = np.arange(0, 1.55, 0.05)
    tank_counts = {'Paladin': 0, 'Shaman': 0, 'Druid': 0, 'Warrior': 0}
    for tank, d in asc['per_tank'].items():
        if len(d['mid']) >= 10 and d.get('class') in tank_counts:
            tank_counts[d['class']] += 1

    for ax, (label, data, color) in zip(axes, panels):
        n = len(data)
        if n == 0:
            ax.set_visible(False); continue
        med = statistics.median(data)
        ax.hist(data, bins=bins, color=color, edgecolor='black', alpha=0.85,
                weights=np.ones(n)/n)
        ax.axvline(0.26, color='#27ae60', ls='--', alpha=0.6)
        ax.axvline(0.50, color='#c0392b', ls='--', alpha=0.6)
        ax.axvline(med, color='#2c3e50', lw=2)
        # tank count in title
        cls_short = label.split(' ')[0]
        if 'Subd' in label:
            tlabel = '1 tank'
        else:
            tlabel = f'{tank_counts.get(cls_short, "?")} tanks'
        ax.set_title(f'{label}\n{tlabel}, n={n} parries\nmedian={med:.3f}', fontsize=11)
        ax.set_xlabel('Ratio'); ax.grid(True, alpha=0.3); ax.set_xlim(0, 1.5)
    axes[0].set_ylabel('Fraction of parries')
    plt.suptitle('Parry-haste signal by tank class — Bronzebeard\n'
                 '(Active prediction green dashed at 0.26, '
                 'No-haste prediction red dashed at 0.50)',
                 fontsize=13, y=1.04)
    add_footer(fig, base_footer(local, asc))
    fig.tight_layout(rect=[0, 0.07, 1, 0.95])
    fig.savefig(CHARTS / 'parry_haste_by_class.png', dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f'  → parry_haste_by_class.png')


def chart_per_tank(local, asc):
    """Sorted bar of all qualifying tanks."""
    tanks = []
    for name, d in asc['per_tank'].items():
        if len(d['mid']) >= 10:
            tanks.append({'name': name, 'class': d.get('class') or '?',
                          'mid': d['mid']})
    # Add Subd local
    subd_only = []
    for r in local['rows']:
        if r['player'] == 'Subd':
            subd_only.extend(rr for _, rr in r['mid'])
    if len(subd_only) >= 10:
        tanks.append({'name': 'Subd (LOCAL)', 'class': 'Druid', 'mid': subd_only})

    tanks.sort(key=lambda t: statistics.median(t['mid']))
    fig, ax = plt.subplots(figsize=(13, max(8, 0.22 * len(tanks))))
    y = np.arange(len(tanks))
    medians = [statistics.median(t['mid']) for t in tanks]
    ns = [len(t['mid']) for t in tanks]
    colors = [CLASS_COLOR.get(t['class'], CLASS_COLOR['?']) for t in tanks]
    ax.barh(y, medians, color=colors, edgecolor='black', linewidth=0.5)
    ax.axvline(0.26, color='#27ae60', ls='--', alpha=0.7)
    ax.axvline(0.50, color='#c0392b', ls='--', alpha=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels([f'{t["name"]} ({t["class"][:3]}, n={ns[i]})'
                        for i, t in enumerate(tanks)], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel('Median mid-cycle parry-haste ratio')
    ax.set_title(f'Per-tank parry-haste signal — {len(tanks)} tanks (n≥10 each), '
                 f'sorted by median, coloured by class',
                 fontsize=12)
    ax.legend(handles=[mp.Patch(facecolor=CLASS_COLOR[c], edgecolor='black', label=c)
                       for c in ['Paladin','Shaman','Druid','Warrior']] +
             [plt.Line2D([0],[0], color='#27ae60', ls='--', label='Active (0.26)'),
              plt.Line2D([0],[0], color='#c0392b', ls='--', label='No-haste (0.50)')],
             loc='lower right', fontsize=9, framealpha=0.92)
    ax.grid(True, alpha=0.3, axis='x'); ax.set_xlim(0, 1.3)
    add_footer(fig, base_footer(local, asc))
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(CHARTS / 'parry_haste_per_tank.png', dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f'  → parry_haste_per_tank.png')


def chart_cdf(local, asc):
    rs = sorted(local['mid_ratios'])
    n = len(rs); cum = np.arange(1, n+1) / n
    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.plot(rs, cum, color='#2c3e50', lw=2.2)
    ax.fill_between(rs, 0, cum, alpha=0.15, color='#2c3e50')
    ax.axvline(0.30, color='#27ae60', ls='--', alpha=0.8, label='"Haste fired" cutoff (≤0.30)')
    ax.axvline(0.95, color='#c0392b', ls='--', alpha=0.8, label='"No haste" cutoff (≥0.95)')
    h = 100*sum(1 for r in rs if r <= 0.30)/n
    nh = 100*sum(1 for r in rs if r >= 0.95)/n
    ax.text(0.04, 0.95,
            f'≤0.30 (parry-haste fired): {h:.1f}%\n≥0.95 (no haste fired): {nh:.1f}%',
            transform=ax.transAxes, fontsize=11, va='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#fff5cc', edgecolor='#a86413'))
    ax.set_xlabel('Ratio'); ax.set_ylabel('Cumulative fraction of parries')
    ax.set_title(f'CDF of mid-cycle parry ratios — Bronzebeard local (n={n})', fontsize=12)
    ax.legend(loc='lower right', framealpha=0.9)
    ax.grid(True, alpha=0.3); ax.set_xlim(0, 1.5); ax.set_ylim(0, 1.02)
    add_footer(fig, base_footer(local, asc))
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(CHARTS / 'parry_haste_cdf.png', dpi=130)
    plt.close(fig)
    print(f'  → parry_haste_cdf.png')


def main():
    print('Loading local Bronzebeard logs…')
    local = load_local()
    print(f'  {local["log_files"]} log files, {local["pairs_qualifying"]} pairs, '
          f'{len(local["mid_ratios"])} mid-cycle parries')

    print('Loading ascensionlogs cache…')
    asc = load_asclogs()
    print(f'  {asc["reports"]} reports, {asc["encounters_analysed"]} encounters, '
          f'{len(asc["mid_ratios"])} mid-cycle parries, {len(asc["per_tank"])} tanks')

    print('\nRendering charts…')
    chart_distribution(local, asc)
    chart_local_vs_asclogs(local, asc)
    chart_by_class(local, asc)
    chart_per_tank(local, asc)
    chart_cdf(local, asc)


if __name__ == '__main__':
    main()
