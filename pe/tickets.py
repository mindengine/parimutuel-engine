"""Ticket combinatorics and validation.

Every ticket the engine (or the LLM layer) proposes is recomputed here.
Nothing downstream trusts a claimed combination count or cost.

Two ticket shapes:
  * Horizontal (DD, P3, P4, P5, P6): `legs` = list of selections per race.
    Combos = product of leg sizes.
  * Vertical (EX, TRI, SUP, and WIN/PLC/SHW): `positions` = list of selections
    per finishing slot. Combos = number of ordered tuples with distinct runners.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from math import prod
from typing import Iterable, Sequence

HORIZONTAL_POOLS = {"DD": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}
VERTICAL_POOLS = {"WIN": 1, "PLC": 1, "SHW": 1, "EX": 2, "TRI": 3, "SUP": 4}

# Minimum base wagers commonly offered in North America. Tracks vary;
# override per track in config later if needed.
DEFAULT_MIN_BASE = {"WIN": 2.0, "PLC": 2.0, "SHW": 2.0, "EX": 1.0, "TRI": 0.5,
                    "SUP": 0.1, "DD": 1.0, "P3": 0.5, "P4": 0.5, "P5": 0.5, "P6": 0.2}


def _norm(sel: Iterable) -> list[str]:
    out: list[str] = []
    for s in sel:
        s = str(s).strip().lstrip("#").upper()
        if s and s not in out:
            out.append(s)
    return out


def horizontal_combos(legs: Sequence[Iterable]) -> int:
    return prod(len(_norm(l)) for l in legs) if legs else 0


def vertical_combos(positions: Sequence[Iterable]) -> int:
    """Count ordered finishes where no runner fills two slots."""
    sets = [_norm(p) for p in positions]
    if not sets or any(not s for s in sets):
        return 0
    return sum(1 for t in product(*sets) if len(set(t)) == len(t))


def expand_vertical(positions: Sequence[Iterable]) -> list[tuple[str, ...]]:
    sets = [_norm(p) for p in positions]
    return [t for t in product(*sets) if len(set(t)) == len(t)]


def box(runners: Iterable, slots: int) -> list[list[str]]:
    r = _norm(runners)
    return [r[:] for _ in range(slots)]


@dataclass
class Ticket:
    pool: str
    base: float
    legs: list[list[str]] | None = None        # horizontal
    positions: list[list[str]] | None = None   # vertical
    race: int | None = None                    # vertical race / first leg of horizontal
    label: str = ""
    claimed_combos: int | None = None
    claimed_cost: float | None = None
    issues: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.pool = self.pool.upper()
        if self.legs is not None:
            self.legs = [_norm(l) for l in self.legs]
        if self.positions is not None:
            self.positions = [_norm(p) for p in self.positions]

    @property
    def is_horizontal(self) -> bool:
        return self.pool in HORIZONTAL_POOLS

    @property
    def combos(self) -> int:
        if self.is_horizontal:
            return horizontal_combos(self.legs or [])
        return vertical_combos(self.positions or [])

    @property
    def cost(self) -> float:
        return round(self.combos * self.base, 2)

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.is_horizontal:
            want = HORIZONTAL_POOLS[self.pool]
            n = len(self.legs or [])
            if n != want:
                issues.append(f"{self.pool} needs {want} legs, ticket has {n}")
            if any(len(l) == 0 for l in self.legs or []):
                issues.append("empty leg")
        elif self.pool in VERTICAL_POOLS:
            want = VERTICAL_POOLS[self.pool]
            n = len(self.positions or [])
            if n != want:
                issues.append(f"{self.pool} needs {want} positions, ticket has {n}")
        else:
            issues.append(f"unknown pool {self.pool}")
        if self.base < DEFAULT_MIN_BASE.get(self.pool, 0):
            issues.append(f"base ${self.base:.2f} below typical minimum "
                          f"${DEFAULT_MIN_BASE[self.pool]:.2f}")
        if self.claimed_combos is not None and self.claimed_combos != self.combos:
            issues.append(f"claimed {self.claimed_combos} combos, actual {self.combos}")
        if self.claimed_cost is not None and abs(self.claimed_cost - self.cost) > 0.005:
            issues.append(f"claimed ${self.claimed_cost:.2f}, actual ${self.cost:.2f}")
        self.issues = issues
        return issues

    def hits(self, winners: Sequence[str]) -> bool:
        """winners: winning pgm per leg (horizontal) or finish order (vertical)."""
        w = [str(x).strip().lstrip("#").upper() for x in winners]
        if self.is_horizontal:
            legs = self.legs or []
            return len(w) >= len(legs) and all(w[i] in legs[i] for i in range(len(legs)))
        pos = self.positions or []
        return len(w) >= len(pos) and all(w[i] in pos[i] for i in range(len(pos)))

    def to_dict(self) -> dict:
        return {"pool": self.pool, "base": self.base, "legs": self.legs,
                "positions": self.positions, "race": self.race, "label": self.label,
                "claimed_combos": self.claimed_combos, "claimed_cost": self.claimed_cost,
                "combos": self.combos, "cost": self.cost, "issues": self.validate()}
