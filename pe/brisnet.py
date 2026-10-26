"""Importer for Brisnet Comprehensive Charts (one ZIP per track/date, six CSVs).

Layout per Brisnet's "Data File Structure for Comprehensive Charts File".
Fields are positional (1-indexed in the spec; 0-indexed below). Every file
starts with: track, date (YYYYMMDD), race, day/evening flag.

Assumptions to confirm on the first real file (see VERIFY notes):
  * Start field 31 'odds' is final tote odds.
  * Exotic wager-type text maps to our pool codes via POOL_PATTERNS.
  * Horizontal payoffs are keyed to the race the pool closed.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

# 0-indexed positions
K_TRACK, K_DATE, K_RACE = 0, 1, 2
S_POST, S_PGM, S_ODDS, S_DQ, S_OFF_FIN, S_VOID = 7, 8, 30, 35, 60, 87
X_WAGER, X_BET, X_PAYOFF, X_NCORRECT, X_NUMBERS, X_POOL, X_CARRY = 4, 5, 6, 7, 8, 9, 10
R_DIST, R_UNIT, R_SURF, R_FIELD, R_COND = 4, 5, 8, 36, 37

POOL_PATTERNS = [  # order matters: most specific first. Verified names on BEL 2026-10-25:
    # Exacta, Trifecta, Superfecta, Quinella, Daily Double, Pick Three/Four/Five/Six,
    # Consolation Double, Consolation Pick Three, Grand Slam.
    (r"consolation|grand\s*slam", None),    # not main pools
    (r"super\s*hi|hi\s*5|pentafecta", "SH5"),
    (r"superfecta", "SUP"),
    (r"trifecta", "TRI"),
    (r"exacta|perfecta", "EX"),
    (r"quinella", "QU"),
    (r"pick\s*(?:6|six)", "P6"),
    (r"pick\s*(?:5|five)|big\s*5", "P5"),
    (r"pick\s*(?:4|four)", "P4"),
    (r"pick\s*(?:3|three)", "P3"),
    (r"double", "DD"),
]
HORIZONTAL_LEGS = {"DD": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}


def pool_code(wager_text: str) -> str | None:
    t = wager_text.lower()
    for pat, code in POOL_PATTERNS:
        if re.search(pat, t):
            return code
    return None


def is_ignored_wager(wager_text: str) -> bool:
    return bool(re.search(r"consolation|grand\s*slam", wager_text.lower()))


def _num(v):
    try:
        return float(v) if str(v).strip() != "" else None
    except ValueError:
        return None


def _iso(d: str) -> str:
    d = d.strip()
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else d


def _get(row, i, default=""):
    return row[i].strip() if i < len(row) else default


SUFFIX_KIND = {"1": "race", "2": "start", "3": "itm", "4": "exotic", "5": "breeding", "6": "footnotes"}


def _classify(name: str, rows: list[list[str]]) -> str | None:
    # Real Brisnet ZIPs name members TRKMMDDYYYY.1 ... .6 (verified on BEL 2026-10-25).
    m = re.search(r"\.(\d)$", name)
    if m and m.group(1) in SUFFIX_KIND:
        return SUFFIX_KIND[m.group(1)]
    n = name.lower()
    for key, kind in [("exotic", "exotic"), ("itm", "itm"), ("start", "start"), ("breed", "breeding"),
                      ("foot", "footnotes"), ("race", "race")]:
        if key in n:
            return kind
    r = next((x for x in rows if len(x) > 5), None)
    if not r:
        return None
    if len(r) >= 60 and _get(r, S_PGM):
        return "start"
    if pool_code(_get(r, X_WAGER)) and _num(_get(r, X_PAYOFF)) is not None:
        return "exotic"
    if _get(r, R_UNIT) in {"Y", "M", "F"} and _num(_get(r, R_DIST)):
        return "race"
    if re.fullmatch(r"\d{8}", _get(r, 10)):
        return "breeding"
    if re.fullmatch(r"\d{1,2}", _get(r, 4)) and len(r) <= 12:
        return "footnotes"
    return "itm"


def read_zip(path: str | Path) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(path) as z:
        for m in z.namelist():
            if m.endswith("/"):
                continue
            text = z.read(m).decode("latin-1")
            rows = [r for r in csv.reader(io.StringIO(text)) if r and any(c.strip() for c in r)]
            kind = _classify(Path(m).name, rows)
            if kind:
                out.setdefault(kind, []).extend(rows)
    return out


def _norm_combo(nums: str, pool: str) -> str:
    """Canonical form (same as Brisnet): positions/legs joined by '-', and
    alternative winners within a leg (dead heats, scratch substitutions) by '/'."""
    legs = [l for l in nums.strip().upper().replace(" ", "").split("-") if l]
    return "-".join("/".join(sorted(set(l.split("/")), key=lambda x: (len(x), x))) for l in legs)


def import_brisnet_zip(con, path: str | Path) -> dict:
    """Load finish order, final odds and payouts. Returns counts plus warnings."""
    files = read_zip(path)
    stats = {"starts": 0, "payouts": 0, "skipped_payouts": 0, "warnings": []}

    for r in files.get("start", []):
        track, date, race = _get(r, K_TRACK).upper(), _iso(_get(r, K_DATE)), int(_get(r, K_RACE) or 0)
        pgm = _get(r, S_PGM).upper()
        scratched = int(pgm == "SCR" or _get(r, S_POST) == "99")
        fin = _get(r, S_OFF_FIN)
        con.execute("INSERT OR REPLACE INTO post_race_official VALUES (?,?,?,?,?,?,?)",
                    (track, date, race, pgm, int(fin) if fin.isdigit() and not scratched else None,
                     _num(_get(r, S_ODDS)), scratched))
        stats["starts"] += 1

    seen_unknown = set()
    for r in files.get("exotic", []):
        wager = _get(r, X_WAGER)
        pool = pool_code(wager)
        if not pool:
            stats["skipped_payouts"] += 1
            if not is_ignored_wager(wager) and wager not in seen_unknown:
                seen_unknown.add(wager)
                stats["warnings"].append(f"unmapped wager type '{wager}'")
            continue
        legs = HORIZONTAL_LEGS.get(pool)
        ncorrect = _get(r, X_NCORRECT)
        if legs and ncorrect.isdigit() and int(ncorrect) not in (0, legs):
            stats["skipped_payouts"] += 1          # consolation payoff (e.g. 5 of 6)
            continue
        con.execute("INSERT OR REPLACE INTO payouts VALUES (?,?,?,?,?,?,?)",
                    (_get(r, K_TRACK).upper(), _iso(_get(r, K_DATE)), int(_get(r, K_RACE) or 0), pool,
                     _norm_combo(_get(r, X_NUMBERS), pool), _num(_get(r, X_BET)) or 0.0,
                     _num(_get(r, X_PAYOFF)) or 0.0))
        stats["payouts"] += 1

    # Win payoffs from the ITM file, so WIN tickets grade too.
    for r in files.get("itm", []):
        win = _num(_get(r, 8))
        if win:
            con.execute("INSERT OR REPLACE INTO payouts VALUES (?,?,?,?,?,?,?)",
                        (_get(r, K_TRACK).upper(), _iso(_get(r, K_DATE)), int(_get(r, K_RACE) or 0),
                         "WIN", _get(r, 7).upper(), 2.0, win))
    con.commit()
    if not files.get("start"):
        stats["warnings"].append("no Start file found in ZIP")
    if not files.get("exotic"):
        stats["warnings"].append("no Exotic Payoff file found in ZIP")
    return stats
