"""Database access and ingestion (tail blocks, results)."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from .tickets import Ticket

SCHEMA = Path(__file__).with_name("schema.sql")
DEFAULT_DB = Path("data/engine.db")

BUCKETS = {"A+", "A", "B", "C", "D", "F-", "F"}
PACE = {"LONE_SPEED", "HONEST", "MELTDOWN"}


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA.read_text())
    cols = {r[1] for r in con.execute("PRAGMA table_info(pre_race_grades)")}
    if "reason" not in cols:   # databases made before the column existed
        con.execute("ALTER TABLE pre_race_grades ADD COLUMN reason TEXT")
        con.executescript(SCHEMA.read_text())
    return con


def flag(con, card_id, race, severity, message):
    con.execute("INSERT INTO data_quality_flags(card_id, race, severity, message) VALUES (?,?,?,?)",
                (card_id, race, severity, message))


def insert_ticket(con, card_id: str, t: Ticket, structure: str | None = None) -> list[str]:
    issues = t.validate()
    sel = t.legs if t.is_horizontal else t.positions
    con.execute(
        "INSERT INTO tickets(card_id,label,structure,pool,race,base,selections,combos,cost,"
        "claimed_combos,claimed_cost,issues) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (card_id, t.label, structure, t.pool, t.race or 0, t.base, json.dumps(sel),
         t.combos, t.cost, t.claimed_combos, t.claimed_cost, json.dumps(issues)))
    for msg in issues:
        flag(con, card_id, t.race, "warn", f"ticket '{t.label}': {msg}")
    return issues


def _mods(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        v = [x for x in v.replace("[", " ").replace("]", " ").replace(",", " ").split() if x]
    return ",".join(sorted({x.strip().upper() for x in v}))


def ingest_tail_block(con, data: dict | str | Path, replace: bool = True) -> str:
    """Load a tail-block JSON (see docs/tail_block_spec.md). Returns card_id."""
    if isinstance(data, (str, Path)):
        data = json.loads(Path(data).read_text())
    track = data["track"].upper()
    date = data["race_date"]
    card_id = data.get("card_id") or f"{track}-{date}"
    if replace:
        con.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
        con.execute("DELETE FROM data_quality_flags WHERE card_id=?", (card_id,))
    con.execute("INSERT INTO cards(card_id,track,race_date,source,schema_version) VALUES (?,?,?,?,?)",
                (card_id, track, date, "tail_block", data.get("schema_version")))
    for r in data.get("races", []):
        rn = int(r["race"])
        pace = (r.get("pace_scenario") or "").upper() or None
        if pace and pace not in PACE:
            flag(con, card_id, rn, "warn", f"unknown pace_scenario {pace}")
        runners = r.get("runners", [])
        active = [x for x in runners if not x.get("scratched")]
        complete = int(bool(active) and all(x.get("bucket") for x in active))
        con.execute(
            "INSERT INTO races(card_id,race,surface,distance_yds,race_type,field_size,qsp_sum,"
            "pace_scenario,par_spd,par_lp,grades_complete) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (card_id, rn, r.get("surface"), r.get("distance_yds"), r.get("race_type"),
             r.get("field_size", len(active)), r.get("qsp_sum"), pace, r.get("par_spd"),
             r.get("par_lp"), complete))
        if not complete:
            flag(con, card_id, rn, "warn", "not every active runner has a bucket")
        for x in runners:
            b = x.get("bucket")
            if b and b not in BUCKETS:
                flag(con, card_id, rn, "error", f"#{x['pgm']} invalid bucket {b}")
                b = None
            con.execute(
                "INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,modifiers,tags,ml_odds,"
                "scratched,p_est,prime_power,last_fig,last_e1,last_lp,qsp) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (card_id, rn, str(x["pgm"]).upper(), x.get("name"), b, _mods(x.get("modifiers")),
                 _mods(x.get("tags")), x.get("ml_odds"), int(bool(x.get("scratched"))),
                 x.get("p_est"), x.get("prime_power"), x.get("last_fig"), x.get("last_e1"),
                 x.get("last_lp"), x.get("qsp")))
    for t in data.get("tickets", []):
        tk = Ticket(pool=t["pool"], base=float(t["base"]), legs=t.get("legs"),
                    positions=t.get("positions"), race=t.get("race") or t.get("start_race"),
                    label=t.get("label", ""), claimed_combos=t.get("claimed_combos"),
                    claimed_cost=t.get("claimed_cost"))
        insert_ticket(con, card_id, tk, t.get("structure"))
    con.commit()
    return card_id


def import_results_csv(con, path: str | Path) -> int:
    """CSV columns: track,race_date,race,pgm,finish_pos,final_odds[,scratched]"""
    n = 0
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            con.execute(
                "INSERT OR REPLACE INTO post_race_official VALUES (?,?,?,?,?,?,?)",
                (row["track"].upper(), row["race_date"], int(row["race"]), row["pgm"].upper(),
                 int(row["finish_pos"]) if row.get("finish_pos") else None,
                 float(row["final_odds"]) if row.get("final_odds") else None,
                 int(row.get("scratched") or 0)))
            n += 1
    con.commit()
    return n


def import_payouts_csv(con, path: str | Path) -> int:
    """CSV columns: track,race_date,race,pool,winning_combo,base,payout"""
    n = 0
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            con.execute("INSERT OR REPLACE INTO payouts VALUES (?,?,?,?,?,?,?)",
                        (row["track"].upper(), row["race_date"], int(row["race"]),
                         row["pool"].upper(), row["winning_combo"], float(row["base"]),
                         float(row["payout"])))
            n += 1
    con.commit()
    return n
