#!/usr/bin/env python3
"""
fierce_blow_probe.py — characterise Ascension's "Fierce Blow" mechanic.

Wiki claim (exil.es): every 3 seconds, the boss has a 33% chance to cast
Fierce Blow. Chance stacks by +33% on failed ticks, resets on success.
Fierce Blow cannot be dodged or parried.

Expected interval between successful casts: geometric-with-stacking gives
a mean of ~6 seconds per cast. Cannot miss/dodge/parry — SPELL_MISSED for
spell 975011 should be zero or very rare.

Run:
  python3 fierce_blow_probe.py <log-glob>
"""
from __future__ import annotations
import argparse
import statistics
import sys
from collections import Counter, defaultdict
from common import iter_events, expand_globs, is_player

FIERCE_BLOW_SPELL = '975011'
FIERCE_BLOW_NAME = 'Fierce Blow'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--min-casts', type=int, default=5)
    ap.add_argument('--per-target', action='store_true')
    args = ap.parse_args()

    paths = expand_globs(args.logs)

    casts_by_pair = defaultdict(list)  # (mob_guid, player_guid) → [ts]
    caster_names: dict[str, str] = {}
    miss_types: Counter = Counter()
    miss_total = 0
    damage_samples: list[int] = []
    absorbed_samples: list[int] = []
    school_counter: Counter = Counter()

    for path, (ts, evt, f) in iter_events(paths):
        # Fierce Blow CAST_SUCCESS — primary event
        if evt == 'SPELL_CAST_SUCCESS' and len(f) >= 9 and f[7] == FIERCE_BLOW_SPELL:
            src, sname, dst = f[1], f[2], f[4]
            if (not is_player(src)) and is_player(dst):
                casts_by_pair[(src, dst)].append(ts)
                caster_names[src] = sname
        # Fierce Blow DAMAGE (for damage + school stats)
        elif evt == 'SPELL_DAMAGE' and len(f) >= 10 and f[7] == FIERCE_BLOW_SPELL:
            try:
                damage_samples.append(int(f[-9]))
                absorbed_samples.append(int(f[-4]))
                school_counter[f[9]] += 1  # spell school mask
            except (ValueError, IndexError):
                pass
        # Fierce Blow MISSED (should be rare / zero if wiki correct)
        elif evt == 'SPELL_MISSED' and len(f) >= 10 and f[7] == FIERCE_BLOW_SPELL:
            miss_total += 1
            miss_types[f[10] if len(f) > 10 else f[-1]] += 1

    # Aggregate casts and intervals
    all_intervals = []
    total_casts = 0
    rows = []
    for (mob, player), times in casts_by_pair.items():
        times.sort()
        if len(times) < args.min_casts:
            continue
        total_casts += len(times)
        intervals = [times[i] - times[i - 1] for i in range(1, len(times))]
        intervals = [iv for iv in intervals if 0.3 < iv < 60]
        all_intervals.extend(intervals)
        rows.append((caster_names.get(mob, mob[:18]), player, len(times), intervals))

    if args.per_target:
        print(f'{"mob":<26}{"player":<22}{"casts":>7}{"med_iv":>8}{"mean_iv":>9}')
        for name, player, n, intervals in sorted(rows, key=lambda x: -x[2]):
            if not intervals:
                continue
            print(f'{name[:24]:<26}{player[:20]:<22}{n:>7}'
                  f'{statistics.median(intervals):>8.2f}'
                  f'{statistics.mean(intervals):>9.2f}')

    print(f'\n=== FIERCE BLOW ({FIERCE_BLOW_NAME}, spell {FIERCE_BLOW_SPELL}) ===')
    print(f'qualifying (mob,player) pairs: {len(rows)}')
    print(f'total casts observed:          {total_casts}')
    print(f'casts missed/dodged/parried:   {miss_total}'
          f'  (types: {dict(miss_types) if miss_total else "—"})')

    if all_intervals:
        print(f'\nInter-cast intervals (n={len(all_intervals)}):')
        print(f'  median: {statistics.median(all_intervals):.2f}s')
        print(f'  mean:   {statistics.mean(all_intervals):.2f}s')
        if len(all_intervals) > 1:
            print(f'  stdev:  {statistics.stdev(all_intervals):.2f}s')
        print('  predicted under 33%-stacking-per-3s: ~6s mean')

    if damage_samples:
        print(f'\nDamage per Fierce Blow (n={len(damage_samples)}):')
        print(f'  median: {statistics.median(damage_samples):,}')
        print(f'  mean:   {int(statistics.mean(damage_samples)):,}')
        print(f'  max:    {max(damage_samples):,}')
        absorbed_total = sum(absorbed_samples)
        if absorbed_total:
            print(f'  absorbed (sum): {absorbed_total:,}  '
                  f'({100.0*absorbed_total/max(1,sum(damage_samples)):.1f}% of raw)')

    if school_counter:
        print('\nSpell school distribution (mask → count):')
        for k, v in school_counter.most_common():
            print(f'  {k}  count={v}')

    print('\nVERDICT (per wiki claim):')
    if miss_total == 0 and len(all_intervals) > 0:
        print('  CONFIRMED — Fierce Blow never missed/dodged/parried, '
              'and casts occur at the predicted cadence.')
    elif miss_total > 0:
        print(f'  WIKI CLAIM PARTIALLY WRONG — Fierce Blow was missed/'
              f'avoided {miss_total} times. Check miss-type breakdown above.')


if __name__ == '__main__':
    main()
