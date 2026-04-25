#!/usr/bin/env python3
"""
parry_haste_detect.py — detect parry-haste in 3.3.5 WoWCombatLog.txt files.

Mechanic under test:
  When a defender PARRIES an incoming melee attack, the defender's NEXT
  auto-attack swing timer is shortened by up to 40% of the base swing time
  (with a floor so remaining time can never drop below 20% of base).

Methodology (v2, per-mob normalized):
  1. Parse SWING_DAMAGE / SWING_MISSED for (mob, player) pairs.
  2. Per pair, derive mob's baseline swing time from the median of
     consecutive non-parry-affected mob-swing intervals.
  3. For every player→mob PARRY at t_p, measure delta = (t_next_mob_swing - t_p),
     and normalize by that mob's baseline → ratio = delta / baseline.
  4. Expected ratio under random parry timing within swing cycle:
        - NO parry-haste:    ratio ≈ 0.50  (uniform mid-cycle hit)
        - parry-haste ON:    ratio ≈ 0.25-0.30  (40% chop, 20% floor)
  5. Aggregate ratios across all pairs and report verdict.

Run:
  python3 parry_haste_detect.py <log-glob> [--per-target] [--min-parries N]
"""

from __future__ import annotations
import argparse
import csv
import glob
import io
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field

TS_RE = re.compile(r'^(\d+)/(\d+)\s+(\d+):(\d+):(\d+)\.(\d+)\s\s(.*)$')
DAYS_PER_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def parse_ts(month, day, h, mi, s, ms) -> float:
    """Seconds within a 365-day window. Differences are what matter."""
    return (sum(DAYS_PER_MONTH[:month]) + day) * 86400.0 \
        + h * 3600 + mi * 60 + s + ms / 1000.0


def is_player(guid: str) -> bool:
    """3.3.5 player GUIDs start with 0x000... ; NPCs/pets with 0xF..."""
    return guid.startswith('0x000')


def parse_event(line: str):
    m = TS_RE.match(line)
    if not m:
        return None
    month, day, h, mi, s, ms, rest = m.groups()
    ts = parse_ts(int(month), int(day), int(h), int(mi), int(s), int(ms))
    try:
        fields = next(csv.reader(io.StringIO(rest)))
    except StopIteration:
        return None
    if not fields:
        return None
    return ts, fields[0], fields


@dataclass
class Pair:
    mob_swings: list = field(default_factory=list)  # list[(ts, was_missed_bool)]
    parries: list = field(default_factory=list)     # list[ts of player→mob PARRY]
    mob_name: str = ''
    player_name: str = ''


def collect(paths: list[str]) -> dict[tuple[str, str], Pair]:
    pairs: dict[tuple[str, str], Pair] = defaultdict(Pair)
    for path in paths:
        with open(path, errors='ignore') as f:
            for line in f:
                ev = parse_event(line)
                if ev is None:
                    continue
                ts, evt, f_ = ev
                if evt == 'SWING_DAMAGE' and len(f_) >= 7:
                    src, sname, dst = f_[1], f_[2], f_[4]
                    if (not is_player(src)) and is_player(dst):
                        p = pairs[(src, dst)]
                        p.mob_swings.append((ts, False))
                        p.mob_name = p.mob_name or sname
                        p.player_name = p.player_name or f_[5]
                elif evt == 'SWING_MISSED' and len(f_) >= 8:
                    src, sname, dst, miss = f_[1], f_[2], f_[4], f_[7]
                    if (not is_player(src)) and is_player(dst):
                        p = pairs[(src, dst)]
                        p.mob_swings.append((ts, True))
                        p.mob_name = p.mob_name or sname
                        p.player_name = p.player_name or f_[5]
                    elif is_player(src) and (not is_player(dst)) and miss == 'PARRY':
                        p = pairs[(dst, src)]
                        p.parries.append(ts)
                        p.mob_name = p.mob_name or f_[5]
                        p.player_name = p.player_name or sname
        print(f'  scanned {path}', file=sys.stderr)
    return pairs


def baseline_swing_time(swings: list[tuple[float, bool]]) -> float | None:
    """Median consecutive-swing interval, excluding obvious gaps."""
    intervals = []
    for i in range(1, len(swings)):
        iv = swings[i][0] - swings[i - 1][0]
        if 0.3 < iv < 6.0:  # raid bosses swing in 1-4s; tight cap excludes gaps
            intervals.append(iv)
    if len(intervals) < 8:
        return None
    return statistics.median(intervals)


def post_parry_ratios(pair: Pair, baseline: float) -> list[float]:
    """For each parry, ratio of (next_swing - parry_time) / baseline_swing_time.

    Excludes parries with no next-swing within 2× baseline (boss probably CC'd
    or pull ended).
    """
    ratios = []
    swings = sorted(pair.mob_swings)
    si = 0
    for tp in sorted(pair.parries):
        # find first mob swing strictly after tp
        while si < len(swings) and swings[si][0] <= tp:
            si += 1
        if si >= len(swings):
            break
        delta = swings[si][0] - tp
        if 0 < delta < 2.0 * baseline:
            ratios.append(delta / baseline)
        # don't advance si — multiple parries may share next swing window
    return ratios


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--min-parries', type=int, default=3)
    ap.add_argument('--per-target', action='store_true')
    args = ap.parse_args()

    paths = []
    for g in args.logs:
        paths.extend(sorted(glob.glob(g)))
    if not paths:
        print(f'no logs matched: {args.logs}', file=sys.stderr)
        sys.exit(1)

    pairs = collect(paths)
    print(f'\n{len(pairs)} (mob, player) pairs\n', file=sys.stderr)

    all_ratios = []
    rows = []
    for (mob, player), p in pairs.items():
        base = baseline_swing_time(p.mob_swings)
        if base is None:
            continue
        ratios = post_parry_ratios(p, base)
        if len(ratios) < args.min_parries:
            continue
        rows.append((p, base, ratios))
        all_ratios.extend(ratios)

    if args.per_target:
        print(f'{"mob":<22}{"player":<14}{"swings":>7}{"base_s":>8}'
              f'{"parries":>9}{"med_ratio":>11}{"mean_ratio":>11}')
        for p, base, ratios in sorted(rows, key=lambda x: -len(x[2])):
            print(f'{p.mob_name[:20]:<22}{p.player_name[:12]:<14}'
                  f'{len(p.mob_swings):>7}{base:>8.2f}'
                  f'{len(ratios):>9}'
                  f'{statistics.median(ratios):>11.3f}'
                  f'{statistics.mean(ratios):>11.3f}')

    print('\n=== AGGREGATE: parry → next-swing ratio ===')
    print(f'qualifying (mob,player) pairs: {len(rows)}')
    print(f'total parry events analysed:   {len(all_ratios)}')
    if not all_ratios:
        print('no qualifying data')
        return
    med = statistics.median(all_ratios)
    mean = statistics.mean(all_ratios)
    print(f'\nratio = (next_mob_swing_t - parry_t) / mob_baseline_swing_t')
    print(f'  median: {med:.3f}')
    print(f'  mean:   {mean:.3f}')
    if len(all_ratios) > 1:
        print(f'  stdev:  {statistics.stdev(all_ratios):.3f}')

    print('\nExpected medians under hypotheses:')
    print('  NO parry-haste     →  ~0.50  (random mid-cycle)')
    print('  Parry-haste ACTIVE →  ~0.25-0.30  (-40% chop, 20% floor)')

    print('\nVERDICT:')
    if med < 0.36:
        print(f'  PARRY-HASTE LIKELY ACTIVE (median {med:.2f} ≈ 0.25-0.30)')
    elif med > 0.44:
        print(f'  PARRY-HASTE LIKELY INACTIVE (median {med:.2f} ≈ 0.50 baseline)')
    else:
        print(f'  AMBIGUOUS (median {med:.2f} between predicted modes)')

    # Distribution histogram
    print('\nDistribution of ratios (bin width 0.05):')
    bins = [0] * 21
    for r in all_ratios:
        b = min(20, int(r / 0.05))
        bins[b] += 1
    maxb = max(bins) or 1
    for i, c in enumerate(bins):
        if c == 0 and i not in (10,):
            continue
        bar = '#' * int(40 * c / maxb)
        print(f'  {i*0.05:0.2f}-{(i+1)*0.05:0.2f}  {c:>5}  {bar}')


if __name__ == '__main__':
    main()
