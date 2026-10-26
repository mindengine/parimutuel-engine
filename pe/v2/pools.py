"""Spec rules 4 and 5: cumulative tier depth, cost-to-capture, and disjoint slips.

Grades are passed as {prog: grade} per race. Winners are passed per leg/slot as a
list, because a dead heat gives a leg more than one winner; a leg counts as captured
if ANY of its winners is covered.
"""
from __future__ import annotations

from itertools import product
from math import perm, prod
from typing import Mapping, Sequence

TIERS = ["A+", "A", "B", "C", "D"]          # S_1 .. S_5 are cumulative prefixes of this
DEAD = {"F", "F-"}
TOP = {"A+", "A"}                           # S_top for tier-keyed verticals
VERTICAL_SLOTS = {"EXA": 2, "TRI": 3, "SUP": 4}
HORIZONTAL_LEGS = {"DD": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}
FMISS = "F_MISS"


def tier_index(grade: str | None) -> int | None:
    """0 for A+ ... 4 for D; None for F, F-, or ungraded."""
    return TIERS.index(grade) if grade in TIERS else None


def in_tier(grade: str | None, m: int) -> bool:
    i = tier_index(grade)
    return i is not None and i <= m


def _leg_tier(grades: Mapping[str, str], winners: Sequence[str]) -> int | None:
    """Shallowest tier covering at least one winner of this leg (dead heats)."""
    idx = [tier_index(grades.get(str(w))) for w in winners]
    idx = [i for i in idx if i is not None]
    return min(idx) if idx else None


def count_in_tier(grades: Mapping[str, str], m: int) -> int:
    return sum(1 for g in grades.values() if in_tier(g, m))


def capture(pool_type: str, legs: Sequence[Mapping[str, str]], winners: Sequence[Sequence[str]]) -> dict:
    """Capture analysis for one settled pool.

    Horizontal: `legs` has one grade map per leg, `winners` one winner list per leg.
    Vertical: `legs` is a single grade map (the race), `winners` one list per slot.
    """
    pool_type = pool_type.upper()
    vertical = pool_type in VERTICAL_SLOTS
    races = [legs[0]] * len(winners) if vertical else list(legs)
    tiers = [_leg_tier(g, w) for g, w in zip(races, winners)]
    out = {"captured_by_playable": 0, "lowest_tier_needed": FMISS,
           "combos_to_capture": None, "combos_to_capture_keyed": None}
    if any(t is None for t in tiers):
        return out
    m = max(tiers)
    out.update(captured_by_playable=1, lowest_tier_needed=TIERS[m])
    if vertical:
        r = VERTICAL_SLOTS[pool_type]
        n = count_in_tier(races[0], m)
        out["combos_to_capture"] = perm(n, r) if n >= r else 0
        # Tier-keyed: 1st restricted to {A+, A} ∩ S_m, 2nd..rth open to S_m.
        top = sum(1 for g in races[0].values() if g in TOP and in_tier(g, m))
        won_top = any(races[0].get(str(w)) in TOP for w in winners[0])
        if won_top and n >= r:
            out["combos_to_capture_keyed"] = top * perm(n - 1, r - 1)
    else:
        out["combos_to_capture"] = prod(count_in_tier(g, m) for g in races)
    return out


def theo_win_parlay(final_odds_by_leg: Sequence[float]) -> float:
    """$1 win parlay through each leg's winner: PRODUCT(odds + 1)."""
    return prod(o + 1.0 for o in final_odds_by_leg)


# ---- Rule 5: disjoint ADW slips -------------------------------------------------

def parse_matrix(s: str) -> list[list[str]]:
    """'1,4,6 / 1,3,4,5 / 2' -> [['1','4','6'], ['1','3','4','5'], ['2']]"""
    return [[x.strip().upper() for x in leg.split(",") if x.strip()] for leg in s.split("/")]


def format_matrix(legs: Sequence[Sequence[str]]) -> str:
    return " / ".join(",".join(map(str, leg)) for leg in legs)


def slip_combos(legs: Sequence[Sequence[str]], vertical: bool = False) -> set[tuple]:
    combos = set(product(*legs))
    if vertical:  # a runner can't fill two finishing slots
        combos = {c for c in combos if len(set(c)) == len(c)}
    return combos


class OverlapError(AssertionError):
    pass


def assert_disjoint(slips: Sequence[Sequence[Sequence[str]]], vertical: bool = False) -> list[set]:
    """Raise OverlapError naming the slips and a shared combo if any two slips overlap."""
    sets = [slip_combos(s, vertical) for s in slips]
    if sum(len(s) for s in sets) != len(set().union(*sets)):
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                shared = sets[i] & sets[j]
                if shared:
                    raise OverlapError(f"OVERLAP DETECTED: slips {i+1} and {j+1} share "
                                       f"{len(shared)} combos, e.g. {'-'.join(sorted(shared)[0])}")
    return sets
