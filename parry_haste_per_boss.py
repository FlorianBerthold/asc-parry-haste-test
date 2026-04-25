#!/usr/bin/env python3
"""
parry_haste_per_boss.py — per-MC-boss parry-haste rate using v3 methodology
on cached ascensionlogs.gg timelines.

For each Molten Core boss (and Onyxia for context):
  - aggregate every (boss, tank) pair across all encounters
  - apply mid-cycle / boundary / no-next classification (v3)
  - report median ratio + sample size + verdict

Output: stdout table + JSON to per_boss_parry_haste.json
"""
from __future__ import annotations
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'ascensionlogs_data'
OUT = HERE / 'per_boss_parry_haste.json'

MC_BOSSES = [
    'Lucifron', 'Magmadar', 'Gehennas', 'Garr', 'Shazzrah',
    'Baron Geddon', 'Sulfuron Harbinger', 'Golemagg the Incinerator',
    'Majordomo Executus', 'Ragnaros',
]
EXTRA = ['Onyxia']
ALL_BOSSES = MC_BOSSES + EXTRA

BOUNDARY_S = 0.200


def baseline(swings):
    iv = []
    for i in range(1, len(swings)):
        d = (swings[i] - swings[i - 1]) / 1000.0
        if 0.3 < d < 6.0:
            iv.append(d)
    if len(iv) < 8:
        return None
    return statistics.median(iv)


def classify(swings, parries, base):
    """Return (midcycle_ratios, boundary_count, no_next_count)."""
    swings = sorted(swings)
    parries = sorted(parries)
    mid, bnd, no = [], 0, 0
    si = pi = 0
    for tp in parries:
        while pi < len(swings) and swings[pi] < tp:
            pi += 1
        prev_idx = pi - 1
        while si < len(swings) and swings[si] <= tp:
            si += 1
        next_idx = si
        if next_idx >= len(swings):
            no += 1
            continue
        t_next = swings[next_idx]
        delta = (t_next - tp) / 1000.0
        if delta > 2.0 * base:
            no += 1
            continue
        t_prev = swings[prev_idx] if prev_idx >= 0 else None
        if t_prev is not None and (tp - t_prev) / 1000.0 < BOUNDARY_S:
            bnd += 1
            continue
        if delta < BOUNDARY_S:
            bnd += 1
            continue
        mid.append(delta / base)
    return mid, bnd, no


def main():
    # Build encounter_id -> boss_name map from cached report_*.json
    enc_to_boss = {}
    for rp in CACHE.glob('report_*.json'):
        try:
            d = json.loads(rp.read_text())
        except Exception:
            continue
        for e in d.get('encounters', []):
            if e.get('name') in ALL_BOSSES:
                enc_to_boss[e['id']] = e['name']

    print(f'mapped {len(enc_to_boss)} encounter IDs to bosses', file=sys.stderr)

    # For each boss, aggregate (encounter_id, tank_name) -> swings/parries
    per_boss_pairs = defaultdict(lambda: defaultdict(lambda: {'swings': [], 'parries': []}))
    enc_count = defaultdict(int)

    for tlp in CACHE.glob('timeline_*_filtered.json'):
        # filename: timeline_{enc_id}_filtered.json
        try:
            enc_id = int(tlp.stem.split('_')[1])
        except Exception:
            continue
        boss = enc_to_boss.get(enc_id)
        if not boss:
            continue
        try:
            d = json.loads(tlp.read_text())
        except Exception:
            continue
        events = d.get('events', [])
        if not events:
            continue
        enc_count[boss] += 1
        for e in events:
            sname = e.get('source_name')
            tname = e.get('target_name')
            et = e.get('event_type')
            spell = e.get('spell_name')
            if (sname == boss and e.get('target_type') == 'player'
                    and spell == 'Auto Attack'):
                per_boss_pairs[boss][(enc_id, tname)]['swings'].append(e['timestamp_ms'])
            if (et == 'parry' and tname == boss
                    and e.get('source_type') == 'player'):
                per_boss_pairs[boss][(enc_id, sname)]['parries'].append(e['timestamp_ms'])

    # Now classify each (boss, encounter, tank) and aggregate
    rows = []
    for boss in ALL_BOSSES:
        all_mid = []
        total_bnd = 0
        total_no = 0
        n_pairs = 0
        n_tanks = set()
        for (enc, tank), p in per_boss_pairs[boss].items():
            if len(p['swings']) < 8 or not p['parries']:
                continue
            base = baseline(p['swings'])
            if base is None:
                continue
            mid, bnd, no = classify(p['swings'], p['parries'], base)
            if (len(mid) + bnd) < 1:
                continue
            n_pairs += 1
            n_tanks.add(tank)
            all_mid.extend(mid)
            total_bnd += bnd
            total_no += no
        rows.append({
            'boss': boss,
            'encounters': enc_count.get(boss, 0),
            'tank_pairs': n_pairs,
            'unique_tanks': len(n_tanks),
            'midcycle_n': len(all_mid),
            'boundary_n': total_bnd,
            'no_next_n': total_no,
            'midcycle_median': statistics.median(all_mid) if all_mid else None,
            'midcycle_mean': statistics.mean(all_mid) if all_mid else None,
            'midcycle_ratios': all_mid,
        })

    # Print table
    print()
    print(f'{"Boss":<28}{"encs":>6}{"tanks":>7}{"pairs":>7}'
          f'{"mid":>6}{"bnd":>5}{"none":>6}{"median":>9}{"mean":>8}  verdict')
    print('-' * 100)
    for r in rows:
        med = r['midcycle_median']
        if med is None:
            verdict = 'no data'
            med_s = '   --'
            mean_s = '   --'
        else:
            mean_s = f'{r["midcycle_mean"]:.3f}'
            med_s = f'{med:.3f}'
            if med < 0.32:
                verdict = 'HASTE FIRES'
            elif med > 0.45:
                verdict = 'no haste'
            else:
                verdict = 'partial / mixed'
        print(f'{r["boss"]:<28}{r["encounters"]:>6}{r["unique_tanks"]:>7}'
              f'{r["tank_pairs"]:>7}{r["midcycle_n"]:>6}{r["boundary_n"]:>5}'
              f'{r["no_next_n"]:>6}{med_s:>9}{mean_s:>8}  {verdict}')

    # Strip ratios from saved rows to keep file small (or keep if you want raw)
    OUT.write_text(json.dumps(rows, indent=2))
    print(f'\nsaved → {OUT}')


if __name__ == '__main__':
    main()
