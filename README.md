# combatlog-tools

Statistical analysis of MMO combat-log mechanics on
[Project Ascension](https://ascension.gg) — a heavily-modified 3.3.5 server
where the standard rulebook does not apply. The tools here treat the live
server as a black box and probe it empirically.

What's measured:

- **parry-haste** — does the boss's next swing actually advance after a parry?
- **crushing blows** — do they exist on the server, or have they been removed?
- **glancing blows** — same question
- **fierce blow** — the server's replacement attack mechanic

Results power the charts in [`charts/`](charts/) and the JSON files at the
repo root.

## Layout

```
common.py                    parsing helpers (3.3.5 combat-log format)
parry_haste_detect.py        v1 parry-haste probe
parry_haste_detect_v3.py     v3 with cycle-boundary filter
parry_haste_per_boss.py      per-boss breakdown using the cached scrape
crushing_probe.py            crushing-blow rate per swing-source
glancing_probe.py            glancing-blow rate
fierce_blow_probe.py         fierce-blow rate
scrape_ascensionlogs.py      pull public timelines from ascensionlogs.gg
generate_parry_charts.py     small chart set from local logs
regen_all_charts.py          full chart set, local + scraped data
charts/                      generated PNGs (committed)
ascensionlogs_data/          scraped JSON cache (gitignored, ~9 GB)
```

## Requirements

```
python >= 3.9
matplotlib
numpy
curl    # used by scrape_ascensionlogs.py
```

No package installer / `requirements.txt` yet — `pip install matplotlib numpy`
is enough.

## Running it

### 1. Probes against your own combat logs

Point the tools at a directory of combat-log files via the
`COMBATLOG_GLOB` env var:

```sh
export COMBATLOG_GLOB='/path/to/Logs/*CombatLog.txt'

python3 crushing_probe.py
python3 glancing_probe.py
python3 fierce_blow_probe.py
python3 parry_haste_detect_v3.py
```

Each probe prints a one-screen summary table.

### 2. Scrape ascensionlogs.gg for an independent dataset

```sh
python3 scrape_ascensionlogs.py --max-reports 20
```

Cached JSON lands in `ascensionlogs_data/` and is reused on subsequent runs.
Rate limit is 1 second per request by default; override with `SCRAPE_RATE_S`.

### 3. Render charts

After both data sources are populated:

```sh
python3 regen_all_charts.py
```

Writes every chart in `charts/` with a consistent provenance footer.

## Methodology — parry-haste, briefly

Server claims about parry-haste are often wrong, so we measure it. For each
`(boss, tank)` pair:

1. Compute the boss's baseline swing interval (median of clean swing gaps).
2. For every parry the tank generates, look at the next boss swing.
3. Ratio = `(t_next_swing - t_parry) / baseline_interval`.
4. If parry-haste fired, the swing comes early; ratio clusters near `0.26`.
   If not, it stays on schedule; ratio sits near `0.50` (random midpoint of
   a cycle).
5. Drop parries within 200 ms of a cycle boundary — those are log-resolution
   artefacts.

`parry_haste_detect_v3.py` is the reference implementation. v3 adds the
boundary filter and the no-next-swing exclusion.

## Output files

| File | What |
|---|---|
| `ascensionlogs_ratios.json`        | aggregate ratios from the scraped corpus (v1) |
| `ascensionlogs_ratios_v3.json`     | same with v3 filtering |
| `ascensionlogs_per_tank.json`      | per-tank ratios from public reports |
| `per_class_summary.json`           | aggregate by tank class |
| `per_boss_parry_haste.json`        | aggregate by boss |
| `charts/parry_haste_*.png`         | rendered charts |

## License

MIT.
