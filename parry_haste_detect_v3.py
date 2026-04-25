#!/usr/bin/env python3
"""
parry_haste_detect_v3.py — tightened methodology.

Cluster B (~19% of parries showing ratio ~1.0) in v2 was suspected to be a
combat-log resolution artifact: parries landing within ms of a boss swing
firing get logged in unstable order, so the "next swing" measured is one
full cycle away rather than the (already-fired) swing that should have
been hasted.

v3 fixes that by only counting parries that land MID-CYCLE — i.e., where
the time-since-last-mob-swing is between [200ms, baseline_swing - 200ms].
Cycle-boundary parries are reported separately.

Also reports per-mob histograms so the bimodal vs unimodal distinction
shows up in raw form.
"""
from __future__ import annotations
import argparse
import glob
import statistics
import sys
from collections import defaultdict
from common import iter_events, expand_globs, is_player


class Pair:
    def __init__(self):
        self.mob_swings = []
        self.parries = []
        self.mob_name = ''
        self.player_name = ''


def collect(paths):
    pairs = defaultdict(Pair)
    for path, (ts, evt, f) in iter_events(paths):
        if evt == 'SWING_DAMAGE' and len(f) >= 7:
            src, sname, dst, dname = f[1], f[2], f[4], f[5]
            if (not is_player(src)) and is_player(dst):
                p = pairs[(src, dst)]
                p.mob_swings.append(ts)
                p.mob_name = p.mob_name or sname
                p.player_name = p.player_name or dname
        elif evt == 'SWING_MISSED' and len(f) >= 8:
            src, sname, dst, dname, miss = f[1], f[2], f[4], f[5], f[7]
            if (not is_player(src)) and is_player(dst):
                p = pairs[(src, dst)]
                p.mob_swings.append(ts)
                p.mob_name = p.mob_name or sname
                p.player_name = p.player_name or dname
            elif is_player(src) and (not is_player(dst)) and miss == 'PARRY':
                p = pairs[(dst, src)]
                p.parries.append(ts)
                p.mob_name = p.mob_name or dname
                p.player_name = p.player_name or sname
    return pairs


def baseline_swing_time(swings, lo=0.3, hi=6.0):
    intervals = []
    for i in range(1, len(swings)):
        iv = swings[i] - swings[i - 1]
        if lo < iv < hi:
            intervals.append(iv)
    if len(intervals) < 8:
        return None
    return statistics.median(intervals)


def classify(pair, baseline, boundary_ms=200):
    """For each parry, classify as:
        midcycle  — parry landed mid-cycle and we measured a clean ratio
        boundary  — parry landed within boundary_ms of a mob swing (either side)
                    (likely a logging-order artifact)
        no_next   — no mob swing landed within 2× baseline after the parry
                    (CC, kill, fight ended, etc.)
    """
    swings = sorted(pair.mob_swings)
    parries = sorted(pair.parries)
    midcycle = []
    boundary = []
    no_next = []
    boundary_s = boundary_ms / 1000.0
    si = 0  # next-swing pointer
    pi = 0  # prev-swing pointer
    for tp in parries:
        # advance prev pointer to last swing strictly before tp
        while pi < len(swings) and swings[pi] < tp:
            pi += 1
        prev_idx = pi - 1
        # advance next pointer to first swing strictly after tp
        while si < len(swings) and swings[si] <= tp:
            si += 1
        next_idx = si
        if next_idx >= len(swings):
            no_next.append(tp)
            continue
        t_prev = swings[prev_idx] if prev_idx >= 0 else None
        t_next = swings[next_idx]
        delta_next = t_next - tp
        if delta_next > 2.0 * baseline:
            no_next.append(tp)
            continue
        # Cycle-boundary check: parry too close to either side of a swing event
        if t_prev is not None and (tp - t_prev) < boundary_s:
            boundary.append((tp, delta_next / baseline))
            continue
        if delta_next < boundary_s:
            boundary.append((tp, delta_next / baseline))
            continue
        midcycle.append((tp, delta_next / baseline))
    return midcycle, boundary, no_next


def hist(vals, width=0.05, max_bin=21):
    bins = [0] * max_bin
    for v in vals:
        b = min(max_bin - 1, int(v / width))
        bins[b] += 1
    out = []
    maxb = max(bins) or 1
    for i, c in enumerate(bins):
        if c == 0:
            continue
        bar = '#' * int(40 * c / maxb)
        out.append(f'  {i*width:0.2f}-{(i+1)*width:0.2f}  {c:>5}  {bar}')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--min-parries', type=int, default=3)
    ap.add_argument('--boundary-ms', type=int, default=200,
                    help='exclude parries within this many ms of a swing event')
    ap.add_argument('--per-target', action='store_true')
    args = ap.parse_args()

    paths = expand_globs(args.logs)
    print(f'scanning {len(paths)} logs...', file=sys.stderr)
    pairs = collect(paths)
    print(f'{len(pairs)} (mob,player) pairs total', file=sys.stderr)

    all_mid = []
    all_bnd = []
    all_no = []
    rows = []
    for (mob, player), p in pairs.items():
        base = baseline_swing_time(p.mob_swings)
        if base is None:
            continue
        mid, bnd, no = classify(p, base, args.boundary_ms)
        if (len(mid) + len(bnd)) < args.min_parries:
            continue
        rows.append((p, base, mid, bnd, no))
        all_mid.extend(r for _, r in mid)
        all_bnd.extend(r for _, r in bnd)
        all_no.extend(no)

    if args.per_target:
        print(f'\n{"mob":<24}{"player":<14}{"swings":>7}{"base":>7}'
              f'{"mid":>5}{"bnd":>5}{"none":>5}{"med_ratio_mid":>15}')
        for p, base, mid, bnd, no in sorted(rows, key=lambda x: -len(x[2])):
            med = statistics.median(r for _, r in mid) if mid else 0
            print(f'{p.mob_name[:22]:<24}{p.player_name[:12]:<14}'
                  f'{len(p.mob_swings):>7}{base:>7.2f}'
                  f'{len(mid):>5}{len(bnd):>5}{len(no):>5}{med:>15.3f}')

    print(f'\n=== AGGREGATE (boundary cutoff = {args.boundary_ms}ms) ===')
    print(f'  qualifying (mob,player) pairs: {len(rows)}')
    print(f'  mid-cycle parries:    {len(all_mid)}')
    print(f'  boundary parries:     {len(all_bnd)}  (excluded — log resolution artifact)')
    print(f'  no-next-swing:        {len(all_no)}  (excluded — CC/kill/end)')
    print(f'  total parries seen:   {len(all_mid) + len(all_bnd) + len(all_no)}')

    if all_mid:
        print(f'\nMid-cycle ratio stats (the clean parry-haste signal):')
        print(f'  median: {statistics.median(all_mid):.3f}')
        print(f'  mean:   {statistics.mean(all_mid):.3f}')
        if len(all_mid) > 1:
            print(f'  stdev:  {statistics.stdev(all_mid):.3f}')
        # Predicted under classic 40% chop, 20% floor, uniform mid-cycle parry:
        # E[r] = 0.6 * 0.3 + 0.4 * 0.2 = 0.26
        print('\n  Predicted: ~0.26 if classic parry-haste active for ALL mid-cycle parries')
        print('  Predicted: ~0.50 if no parry-haste at all')
        print('\nMid-cycle ratio histogram:')
        print(hist(all_mid))

    if all_bnd:
        print(f'\nBoundary ratio histogram (for inspection — '
              f'these were excluded from the verdict):')
        print(hist(all_bnd))

    print('\nVERDICT:')
    if all_mid:
        med = statistics.median(all_mid)
        if med < 0.32:
            print(f'  PARRY-HASTE ACTIVE (mid-cycle median {med:.3f} ≈ predicted 0.26)')
        elif med > 0.45:
            print(f'  PARRY-HASTE ABSENT (mid-cycle median {med:.3f} ≈ predicted 0.50)')
        else:
            print(f'  AMBIGUOUS — mid-cycle median {med:.3f} between predictions')
            print(f'             possible partial-proc or magnitude differs from classic 40%')


if __name__ == '__main__':
    main()
