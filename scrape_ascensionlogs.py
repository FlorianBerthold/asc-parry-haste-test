#!/usr/bin/env python3
"""
scrape_ascensionlogs.py — pull combat events from ascensionlogs.gg and run
the same parry-haste analysis on independent data.

Strategy:
  1. Walk /api/reports/public to enumerate recent reports
  2. For each report, fetch /api/reports/{id} to get encounter list
  3. For each boss encounter on Bronzebeard (or any target realm), fetch
     filtered timeline events (parry / damage / miss / dodge / block / absorb / immune)
  4. Cache each timeline to disk (we don't re-fetch)
  5. Aggregate parry events + boss-swings + compute parry-haste ratios

Run:
  python3 scrape_ascensionlogs.py [--max-reports N] [--max-encounters M]
"""
from __future__ import annotations
import argparse
import json
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120 Safari/537.36')
REFERER = 'https://ascensionlogs.gg/'
ROOT = 'https://ascensionlogs.gg'
HERE = Path(__file__).parent
CACHE = HERE / 'ascensionlogs_data'
CACHE.mkdir(exist_ok=True)

# Bosses we care about (sustained tank-on-boss fights; trash skipped)
INTERESTING_BOSSES = {
    'Lucifron', 'Magmadar', 'Gehennas', 'Garr', 'Shazzrah', 'Geddon',
    'Sulfuron Harbinger', 'Golemagg the Incinerator', 'Majordomo Executus',
    'Ragnaros', 'Onyxia', 'Hakkar', 'High Priest Venoxis',
    'Bloodlord Mandokir', 'Jin\'do the Hexxer', 'Jeklik', 'High Priestess Mar\'li',
}

EVENT_TYPES = 'damage,miss,dodge,parry,block,absorb,immune'


RATE_LIMIT_S = float(__import__('os').environ.get('SCRAPE_RATE_S', '1.0'))


def get_json(url: str, cache_key: str) -> dict | None:
    cache_file = CACHE / cache_key
    if cache_file.exists() and cache_file.stat().st_size > 100:
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass
    print(f'  fetching {url}', file=sys.stderr)
    time.sleep(RATE_LIMIT_S)
    r = subprocess.run(
        ['/usr/bin/curl', '-sL', '--max-time', '60',
         '-A', UA, '-H', f'Referer: {REFERER}', '-H', 'Accept: application/json',
         url],
        capture_output=True)
    if r.returncode != 0:
        print(f'    err: {r.stderr.decode()[:200]}', file=sys.stderr)
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    cache_file.write_bytes(r.stdout)
    return d


def list_reports(pages: int = 1) -> list[dict]:
    out = []
    for p in range(1, pages + 1):
        d = get_json(f'{ROOT}/api/reports/public?page={p}',
                     f'public_reports_p{p}.json')
        if not d:
            break
        reports = d.get('reports', [])
        out.extend(reports)
        if not d.get('pagination', {}).get('hasMore'):
            break
    return out


def report_encounters(report_id: int) -> list[dict]:
    d = get_json(f'{ROOT}/api/reports/{report_id}',
                 f'report_{report_id}.json')
    return (d or {}).get('encounters', [])


def encounter_meta(report_id: int, enc_id: int) -> dict | None:
    d = get_json(f'{ROOT}/api/reports/{report_id}/encounters/{enc_id}',
                 f'encounter_{enc_id}.json')
    return d


def encounter_events(report_id: int, enc_id: int) -> list[dict]:
    d = get_json(
        f'{ROOT}/api/reports/{report_id}/encounters/{enc_id}'
        f'/timeline?event_types={EVENT_TYPES}',
        f'timeline_{enc_id}_filtered.json')
    return (d or {}).get('events', [])


def baseline_swing_time(swing_ts_ms: list[int]) -> float | None:
    intervals = []
    for i in range(1, len(swing_ts_ms)):
        iv = (swing_ts_ms[i] - swing_ts_ms[i - 1]) / 1000.0
        if 0.3 < iv < 6.0:
            intervals.append(iv)
    if len(intervals) < 8:
        return None
    return statistics.median(intervals)


def analyze_encounter(events: list[dict], boss_name: str) -> dict:
    """Return per-tank parry-haste stats for this encounter."""
    # Collect per-tank: boss-swing timestamps + player-parries-by-boss timestamps
    swings_by_target = defaultdict(list)
    parries_by_target = defaultdict(list)
    for e in events:
        et = e.get('event_type')
        sname = e.get('source_name')
        tname = e.get('target_name')
        spell = e.get('spell_name')
        # Boss auto-attacks against players (any outcome)
        if (sname == boss_name and e.get('target_type') == 'player'
                and spell == 'Auto Attack'):
            swings_by_target[tname].append(e['timestamp_ms'])
        # Player attacks the boss → boss parries → event_type='parry',
        # source_type=player, target=boss
        if (et == 'parry' and tname == boss_name
                and e.get('source_type') == 'player'):
            parries_by_target[sname].append(e['timestamp_ms'])

    out = []
    for tank, swings in swings_by_target.items():
        swings = sorted(swings)
        parries = sorted(parries_by_target.get(tank, []))
        if len(swings) < 8 or not parries:
            continue
        baseline = baseline_swing_time(swings)
        if baseline is None:
            continue
        ratios = []
        for tp in parries:
            # find next swing strictly after tp
            for ts in swings:
                if ts > tp:
                    delta = (ts - tp) / 1000.0
                    if 0 < delta < 2.0 * baseline:
                        ratios.append(delta / baseline)
                    break
        out.append({
            'tank': tank,
            'baseline': baseline,
            'parries': len(parries),
            'analysed_ratios': ratios,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-reports', type=int, default=20,
                    help='max reports to walk (default 20)')
    ap.add_argument('--max-encounters', type=int, default=200,
                    help='max boss encounters to fetch')
    ap.add_argument('--pages', type=int, default=2,
                    help='pages of /api/reports/public to enumerate')
    args = ap.parse_args()

    reports = list_reports(pages=args.pages)
    print(f'enumerated {len(reports)} reports across {args.pages} pages',
          file=sys.stderr)

    all_ratios = []
    per_boss = defaultdict(list)
    encounters_done = 0
    for r in reports[:args.max_reports]:
        rid = r['id']
        encs = report_encounters(rid)
        for e in encs:
            if not e.get('success'):
                continue  # skip wipes
            name = e.get('name', '')
            if name not in INTERESTING_BOSSES:
                continue
            enc_id = e['id']
            evs = encounter_events(rid, enc_id)
            if not evs:
                continue
            stats = analyze_encounter(evs, name)
            for s in stats:
                if not s['analysed_ratios']:
                    continue
                per_boss[name].extend(s['analysed_ratios'])
                all_ratios.extend(s['analysed_ratios'])
                med = statistics.median(s['analysed_ratios'])
                print(f'  rep {rid} enc {enc_id:>7} {name:<28} '
                      f'tank={s["tank"]:<14} parries={len(s["analysed_ratios"]):>3} '
                      f'baseline={s["baseline"]:.2f}s med_ratio={med:.3f}',
                      file=sys.stderr)
            encounters_done += 1
            if encounters_done >= args.max_encounters:
                break
        if encounters_done >= args.max_encounters:
            break

    print(f'\n=== AGGREGATE (ascensionlogs.gg sample) ===')
    print(f'  encounters analysed: {encounters_done}')
    print(f'  total parry events:  {len(all_ratios)}')
    if all_ratios:
        med = statistics.median(all_ratios)
        mean = statistics.mean(all_ratios)
        print(f'  median ratio: {med:.3f}')
        print(f'  mean ratio:   {mean:.3f}')
        haste_pct = 100.0 * sum(1 for r in all_ratios if r <= 0.30) / len(all_ratios)
        nohaste_pct = 100.0 * sum(1 for r in all_ratios if r >= 0.95) / len(all_ratios)
        print(f'  ≤0.30 (haste fired): {haste_pct:.1f}%')
        print(f'  ≥0.95 (no haste):    {nohaste_pct:.1f}%')

    print('\nPer-boss breakdown (≥3 parries):')
    for boss, ratios in sorted(per_boss.items(), key=lambda x: -len(x[1])):
        if len(ratios) < 3:
            continue
        med = statistics.median(ratios)
        print(f'  {boss:<32}  n={len(ratios):>4}  median={med:.3f}')

    # Save raw data for chart generation
    out_path = HERE / 'ascensionlogs_ratios.json'
    out_path.write_text(json.dumps({
        'all_ratios': all_ratios,
        'per_boss': dict(per_boss),
        'encounters': encounters_done,
    }, indent=2))
    print(f'\nraw ratios saved to {out_path}')


if __name__ == '__main__':
    main()
