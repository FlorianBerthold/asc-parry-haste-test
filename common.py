"""Shared 3.3.5 WoWCombatLog.txt parsing helpers."""
from __future__ import annotations
import csv
import glob
import io
import re
import sys

TS_RE = re.compile(r'^(\d+)/(\d+)\s+(\d+):(\d+):(\d+)\.(\d+)\s\s(.*)$')
DAYS_PER_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def parse_ts(month: int, day: int, h: int, mi: int, s: int, ms: int) -> float:
    """Seconds within a 365-day window; differences are what matter."""
    return (sum(DAYS_PER_MONTH[:month]) + day) * 86400.0 \
        + h * 3600 + mi * 60 + s + ms / 1000.0


def is_player(guid: str) -> bool:
    """3.3.5 player GUIDs start 0x000...; NPCs/pets with 0xF..."""
    return guid.startswith('0x000')


def parse_event(line: str):
    """Yield (ts, event_name, fields) or None."""
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


def iter_events(paths):
    """Iterate parsed events across multiple files."""
    for path in paths:
        with open(path, errors='ignore') as f:
            for line in f:
                ev = parse_event(line)
                if ev is not None:
                    yield path, ev


def expand_globs(patterns: list[str]) -> list[str]:
    paths = []
    for p in patterns:
        paths.extend(sorted(glob.glob(p)))
    if not paths:
        print(f'no logs matched: {patterns}', file=sys.stderr)
        sys.exit(1)
    return paths


# SWING_DAMAGE / SPELL_DAMAGE suffix indices (from end of fields).
# Suffix layout: amount, overkill, school, resisted, blocked, absorbed,
# critical, glancing, crushing   (9 fields)
SUFFIX_AMOUNT = -9
SUFFIX_CRITICAL = -3
SUFFIX_GLANCING = -2
SUFFIX_CRUSHING = -1


def is_flag_set(field_value: str) -> bool:
    """A suffix flag is 'nil' when unset and '1' (or truthy) when set."""
    return field_value not in ('nil', '', '0')
