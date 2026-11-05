"""Rule memory, deterministic route choice and the checker's primitives.

Rules are per-user, saved only after approval, and can only restrict: they
remove routes or ask for slower, higher or gentler motion. They never relax a
safety check. Route choice is deterministic: drop routes that cross an avoid
zone for the object held, take the first remaining, else return None (ask).

The checker does not trust that by construction: ``permitted_options`` computes
the set of (route, speed cap, grip cap) a carry may use under a rule set, and
``is_restrictive`` proves a proposed rule only ever shrinks that set, never grows
it. ``armed.routing.check_proposal`` runs both ticks (monotone and the counterfactual
replay of the incident) and puts the result on the proposal.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from armed.core import ROOT, now

RESPONSES = frozenset({"route_around", "slow_down", "lift_higher", "grip_gentler"})

Point = tuple[float, float]
Zone = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax


class RulesUnavailable(RuntimeError):
    """The rule file exists but cannot be read. Callers must fail closed (ask the
    person) rather than carry on as if there were no rules."""


@dataclass
class Rule:
    when_holding: list[str]  # object tags that trigger the rule
    avoid: str  # name of the avoid zone / object
    response: str
    incident_id: str | None = None
    clip: str | None = None
    approved: bool = False
    id: str = field(default_factory=lambda: f"r{uuid.uuid4().hex[:8]}")
    created_at: float = field(default_factory=now)

    def __post_init__(self) -> None:
        if self.response not in RESPONSES:
            raise ValueError(f"unknown rule response: {self.response!r}")
        if (
            not isinstance(self.when_holding, list)
            or not self.when_holding
            or not all(isinstance(t, str) and t for t in self.when_holding)
        ):
            raise ValueError("when_holding must be a non-empty list of non-empty strings")
        if not isinstance(self.avoid, str) or not self.avoid:
            raise ValueError("avoid must be a non-empty string")
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a bool")

    def validate(self, zone_names: Collection[str], catalog_tags: Collection[str]) -> list[str]:
        """Problems that make this rule unsafe to save: an avoid target that is not a
        known zone, or when_holding tags that no catalog object carries."""
        return validate_rule(self, zone_names, catalog_tags)


def validate_rule(
    rule: Rule, zone_names: Collection[str], catalog_tags: Collection[str]
) -> list[str]:
    """Empty list = valid. Checks ``avoid`` against known zone names and every
    ``when_holding`` tag against the catalog's tags."""
    problems: list[str] = []
    if rule.avoid not in set(zone_names):
        problems.append(f"unknown avoid zone: {rule.avoid!r}")
    known = set(catalog_tags)
    problems += [f"unknown tag: {t!r}" for t in rule.when_holding if t not in known]
    return problems


class RuleStore:
    """Per-user JSON store under data/rules/."""

    def __init__(self, user: str, root: Path | None = None) -> None:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "user"
        self.path = (root or ROOT / "data" / "rules") / f"{safe}.json"

    def list(self) -> list[Rule]:
        if not self.path.exists():
            return []
        try:
            return [Rule(**r) for r in json.loads(self.path.read_text())]
        except (OSError, ValueError, TypeError, AttributeError, RecursionError) as exc:
            # JSONDecodeError and invalid Rule fields are ValueError; wrong shapes are
            # TypeError/AttributeError. Never read a corrupt store as "no rules".
            raise RulesUnavailable(f"cannot read rules from {self.path}: {exc}") from exc

    def add(self, rule: Rule) -> Rule:
        if rule.approved is not True:
            raise PermissionError("rule must be approved before it is saved")
        rules = [r for r in self.list() if r.id != rule.id] + [rule]
        self._write(rules)
        return rule

    def delete(self, rule_id: str) -> bool:
        rules = self.list()
        kept = [r for r in rules if r.id != rule_id]
        if len(kept) == len(rules):
            return False
        self._write(kept)
        return True

    def _write(self, rules: list[Rule]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in rules], indent=2))
        tmp.replace(self.path)


def _segment_hits_zone(a: Point, b: Point, zone: Zone) -> bool:
    """Liang-Barsky: does segment a-b touch the rectangle?"""
    xmin, ymin, xmax, ymax = zone
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - xmin), (dx, xmax - a[0]), (-dy, a[1] - ymin), (dy, ymax - a[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _zone_ok(zone: object) -> bool:
    """A usable zone is four finite numbers with min <= max on both axes."""
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in zone)  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        return False
    return all(map(math.isfinite, (xmin, ymin, xmax, ymax))) and xmin <= xmax and ymin <= ymax


def crosses(polyline: Sequence[Point], zone: Zone) -> bool:
    # Fail closed: a route with fewer than two points cannot be checked, so it
    # counts as crossing everything.
    if len(polyline) < 2:
        return True
    return any(_segment_hits_zone(a, b, zone) for a, b in zip(polyline, polyline[1:], strict=False))


def matching_rules(held_tags: Sequence[str], rules: Sequence[Rule]) -> list[Rule]:
    """Approved rules whose tags are all carried by the held object."""
    held = set(held_tags)
    return [r for r in rules if r.approved and set(r.when_holding) <= held]


def choose_route(
    held_tags: Sequence[str],
    routes: Mapping[str, Sequence[Point]],
    avoid_zones: Mapping[str, Zone],
    rules: Sequence[Rule],
) -> tuple[str | None, list[str]]:
    """First route (in `routes` order) not crossing a zone avoided by a matching rule.

    Returns (route or None, ids of rules that applied). None means no route is
    left and the caller must ask. Only restricts: with no matching rules the
    first route is returned unchanged. Fails closed: a matching route_around rule
    whose avoid zone is unknown or malformed (e.g. inverted) returns None.
    """
    applied: list[str] = []
    zones: list[Zone] = []
    for rule in matching_rules(held_tags, rules):
        if rule.response != "route_around":
            continue
        zone = avoid_zones.get(rule.avoid)
        if zone is None or not _zone_ok(zone):
            return None, [*applied, rule.id]
        zones.append(zone)
        if any(crosses(poly, zone) for poly in routes.values()):
            applied.append(rule.id)
    for name, poly in routes.items():
        if not any(crosses(poly, z) for z in zones):
            return name, applied
    return None, applied


@dataclass(frozen=True)
class Permitted:
    """What one carry may do under a rule set: the routes still allowed, and the
    caps on speed and grip (shares of the configured maximum, 1.0 = uncapped)."""

    routes: frozenset[str]
    speed_cap: float = 1.0
    grip_cap: float = 1.0

    @property
    def count(self) -> int:
        return len(self.routes)


def permitted_options(
    held_tags: Sequence[str],
    routes: Mapping[str, Sequence[Point]],
    avoid_zones: Mapping[str, Zone],
    rules: Sequence[Rule],
    high_routes: Collection[str] = (),
    slow_speed: float = 0.5,
    gentle_grip: float = 0.6,
) -> Permitted:
    """The permitted set for one carry: every route not removed by a matching rule,
    with the caps the matching rules impose. Pure. Fails closed like ``choose_route``:
    a matching route_around rule whose zone is unknown or malformed leaves no route."""
    matched = matching_rules(held_tags, rules)
    allowed = set(routes)
    for rule in matched:
        if rule.response == "route_around":
            zone = avoid_zones.get(rule.avoid)
            if zone is None or not _zone_ok(zone):
                allowed = set()
                break
            allowed = {n for n in allowed if not crosses(routes[n], zone)}
        elif rule.response == "lift_higher":
            allowed &= set(high_routes)
    mods = modifiers(held_tags, rules)
    speed = min(max(float(slow_speed), 0.0), 1.0) if "slow_down" in mods else 1.0
    grip = min(max(float(gentle_grip), 0.0), 1.0) if "grip_gentler" in mods else 1.0
    return Permitted(frozenset(allowed), speed, grip)


def is_restrictive(
    before: Mapping[Any, Permitted], after: Mapping[Any, Permitted], eps: float = 1e-9
) -> tuple[bool, str]:
    """True when ``after`` is a subset of ``before`` for every carry: no route is
    added, no cap is raised, no carry appears that was not permitted before. The
    detail names the option counts, or what was added."""
    added: list[str] = []
    for key, b in before.items():
        a = after.get(key)
        if a is None:
            continue  # the carry disappeared entirely: a shrink
        extra = a.routes - b.routes
        if extra:
            added.append(f"{key}: route {', '.join(sorted(extra))}")
        if a.speed_cap > b.speed_cap + eps:
            added.append(f"{key}: speed cap {b.speed_cap:g} -> {a.speed_cap:g}")
        if a.grip_cap > b.grip_cap + eps:
            added.append(f"{key}: grip cap {b.grip_cap:g} -> {a.grip_cap:g}")
    for key in after:
        if key not in before:
            added.append(f"{key}: new carry")
    n_before = sum(p.count for p in before.values())
    n_after = sum(p.count for p in after.values())
    if added:
        return False, "adds options: " + "; ".join(added[:3])
    capped = sum(
        1 for k, a in after.items() if k in before and (
            a.speed_cap < before[k].speed_cap - eps or a.grip_cap < before[k].grip_cap - eps
        )
    )
    detail = f"routes {n_before} -> {n_after}"
    if capped:
        detail += f", caps lowered on {capped} carr{'y' if capped == 1 else 'ies'}"
    return True, detail + ", none added"


def modifiers(held_tags: Sequence[str], rules: Sequence[Rule]) -> dict[str, list[str]]:
    """Non-route responses that apply (slow_down, lift_higher, grip_gentler) -> rule ids."""
    out: dict[str, list[str]] = {}
    for rule in matching_rules(held_tags, rules):
        if rule.response != "route_around":
            out.setdefault(rule.response, []).append(rule.id)
    return out
