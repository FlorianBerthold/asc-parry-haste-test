#!/usr/bin/env python3
"""
glancing_probe.py — detect whether glancing blows still exist.

Standard WoW: when a player attacks a +3-level mob, ~24% of SWING_DAMAGE
events carry the "glancing" flag (second-to-last suffix field). Damage is
reduced by ~30%.

Ascension wiki claims glancing was REMOVED and replaced by Fierce Blow
(which is on the boss-side). This probe verifies by counting player→NPC
SWING_DAMAGE events with the glancing flag set vs total.

If ratio ≈ 0% → glancing removed (wiki correct).
If ratio > 10% → glancing still present.

Run:
  python3 glancing_probe.py <log-glob>
"""
from __future__ import annotations
import argparse
import statistics
import sys
from collections import defaultdict
from common import iter_events, expand_globs, is_player, \
    SUFFIX_AMOUNT, SUFFIX_GLANCING, is_flag_set


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--top', type=int, default=15, help='show N top players')
    args = ap.parse_args()

    paths = expand_globs(args.logs)

    # Per-player totals
    swings_by_player = defaultdict(int)
    glancing_by_player = defaultdict(int)
    player_names: dict[str, str] = {}
    # Damage distribution for a glancing check
    normal_damage = []
    glancing_damage = []

    total_swings = 0
    total_glancing = 0

    for path, (ts, evt, f) in iter_events(paths):
        if evt != 'SWING_DAMAGE' or len(f) < 9:
            continue
        src, sname, dst = f[1], f[2], f[4]
        if not (is_player(src) and not is_player(dst)):
            continue
        try:
            amount = int(f[SUFFIX_AMOUNT])
        except (ValueError, IndexError):
            continue
        glancing = is_flag_set(f[SUFFIX_GLANCING])
        total_swings += 1
        swings_by_player[src] += 1
        player_names[src] = sname
        if glancing:
            total_glancing += 1
            glancing_by_player[src] += 1
            glancing_damage.append(amount)
        else:
            normal_damage.append(amount)

    print(f'\n=== GLANCING BLOWS (player → NPC swings) ===')
    print(f'total player→NPC SWING_DAMAGE: {total_swings:,}')
    print(f'marked GLANCING:               {total_glancing:,}')
    pct = 100.0 * total_glancing / max(1, total_swings)
    print(f'rate:                          {pct:.2f}%')
    print(f'\nStandard WoW would show ~24% vs +3 mobs. '
          f'Ascension wiki claims 0%.')

    if total_glancing == 0:
        print('\nVERDICT: glancing blows ARE REMOVED (wiki confirmed).')
    elif pct < 2.0:
        print('\nVERDICT: glancing blows appear effectively REMOVED '
              '(rate below noise floor).')
    elif pct < 10.0:
        print('\nVERDICT: glancing blows RARE — maybe sub-level targets only. '
              'Further investigation needed.')
    else:
        print('\nVERDICT: glancing blows STILL PRESENT — wiki claim incorrect.')

    if args.top:
        print(f'\nTop {args.top} player attackers:')
        print(f'{"player":<22}{"swings":>9}{"glancing":>10}{"rate%":>8}')
        leaders = sorted(swings_by_player.items(),
                         key=lambda x: -x[1])[:args.top]
        for guid, n in leaders:
            g = glancing_by_player.get(guid, 0)
            print(f'{player_names.get(guid, guid[:20]):<22}{n:>9,}'
                  f'{g:>10,}{100.0*g/max(1,n):>7.2f}%')

    if normal_damage:
        print(f'\nDamage (non-glancing): median={statistics.median(normal_damage):,}  '
              f'mean={int(statistics.mean(normal_damage)):,}  n={len(normal_damage):,}')
    if glancing_damage:
        print(f'Damage (glancing):     median={statistics.median(glancing_damage):,}  '
              f'mean={int(statistics.mean(glancing_damage)):,}  n={len(glancing_damage):,}')


if __name__ == '__main__':
    main()
