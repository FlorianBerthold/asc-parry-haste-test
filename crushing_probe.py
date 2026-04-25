#!/usr/bin/env python3
"""
crushing_probe.py — detect crushing blows by player type.

Standard WoW: mobs +3 levels above a player get ~15% crushing blow rate
on auto-attacks, doing 150% normal damage. SWING_DAMAGE suffix carries
a "crushing" flag (last suffix field).

Ascension-specific quirks we want to test:
  - Are crushing blows present at all on this realm?
  - Are druid bears immune (creature-type form)?
  - Is Fierce Blow replacing crushing blows entirely (wiki claim)?

Groups player targets by class-ish buckets via name pattern only works for
our guild; instead we group by "seen crushing yes/no" per player and let
the user interpret. Focus on: per-player rates for the players with the
most incoming swings.

Run:
  python3 crushing_probe.py <log-glob>
"""
from __future__ import annotations
import argparse
import statistics
from collections import defaultdict
from common import iter_events, expand_globs, is_player, \
    SUFFIX_AMOUNT, SUFFIX_CRUSHING, is_flag_set


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--top', type=int, default=20)
    args = ap.parse_args()

    paths = expand_globs(args.logs)

    incoming_by_player = defaultdict(int)
    crushing_by_player = defaultdict(int)
    names: dict[str, str] = {}
    normal_damage_samples = []
    crushing_damage_samples = []
    total_swings = 0
    total_crushing = 0

    for path, (ts, evt, f) in iter_events(paths):
        if evt != 'SWING_DAMAGE' or len(f) < 9:
            continue
        src, dst, dname = f[1], f[4], f[5]
        if not ((not is_player(src)) and is_player(dst)):
            continue
        try:
            amount = int(f[SUFFIX_AMOUNT])
        except (ValueError, IndexError):
            continue
        crushing = is_flag_set(f[SUFFIX_CRUSHING])
        total_swings += 1
        incoming_by_player[dst] += 1
        names[dst] = dname
        if crushing:
            total_crushing += 1
            crushing_by_player[dst] += 1
            crushing_damage_samples.append(amount)
        else:
            normal_damage_samples.append(amount)

    print(f'\n=== CRUSHING BLOWS (NPC → player swings) ===')
    print(f'total NPC→player SWING_DAMAGE: {total_swings:,}')
    print(f'marked CRUSHING:               {total_crushing:,}')
    rate = 100.0 * total_crushing / max(1, total_swings)
    print(f'rate:                          {rate:.2f}%')
    print(f'\nStandard WoW would show ~15% vs +3 bosses. '
          f'Ascension wiki claims 0% (replaced by Fierce Blow).')

    if total_crushing == 0:
        print('\nVERDICT: crushing blows ARE REMOVED (wiki confirmed, '
              'OR Fierce Blow fully replaces them).')
    elif rate < 1.0:
        print('\nVERDICT: crushing blows effectively REMOVED '
              '(rate below noise floor).')
    else:
        print(f'\nVERDICT: crushing blows PRESENT at {rate:.1f}% rate — '
              f'some classes/targets may still feed them.')

    print(f'\nTop {args.top} defenders (by incoming swings):')
    print(f'{"player":<22}{"incoming":>10}{"crushing":>10}{"rate%":>8}')
    leaders = sorted(incoming_by_player.items(), key=lambda x: -x[1])[:args.top]
    for guid, n in leaders:
        c = crushing_by_player.get(guid, 0)
        print(f'{names.get(guid, guid[:20]):<22}{n:>10,}'
              f'{c:>10,}{100.0*c/max(1,n):>7.2f}%')

    if normal_damage_samples:
        print(f'\nDamage (normal):   median={statistics.median(normal_damage_samples):,}  '
              f'mean={int(statistics.mean(normal_damage_samples)):,}  '
              f'n={len(normal_damage_samples):,}')
    if crushing_damage_samples:
        print(f'Damage (crushing): median={statistics.median(crushing_damage_samples):,}  '
              f'mean={int(statistics.mean(crushing_damage_samples)):,}  '
              f'n={len(crushing_damage_samples):,}')


if __name__ == '__main__':
    main()
