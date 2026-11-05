"""Route config and carry shaping: what learned rules do to one pick-and-place.

``plan_carry`` is a pure function of (held tags, rules, route config, routes
the arm can execute, pick slot, limits). It only restricts: it removes routes, lowers
the speed cap or the grip cap, and never raises either above 1.0 (the configured
maximum). When the state needed to prove a route is clear is missing or
corrupt it fails closed and returns a ``problem`` the gate turns into HOLD.

Route proof is per (route, slot): each route stores one table polyline per pick slot,
because the path from slot_1 is not the path from slot_3. A slot with no polyline for
a route cannot use that route when a rule needs proving. Polylines carry a
``measured`` flag; ``RouteConfig.for_proof(hardware=True)`` drops unmeasured ones
(placeholders), the way the real arm refuses untaught poses.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from armed.core import clamp
from armed.rules import (
    Permitted,
    Point,
    Rule,
    RulesUnavailable,
    Zone,
    _zone_ok,
    choose_route,
    crosses,
    is_restrictive,
    matching_rules,
    permitted_options,
)

MIN_SPEED = 0.1  # a slow_down cap below this would stall the arm into a timeout fault


class RoutesUnavailable(RuntimeError):
    """configs/routes.yaml is missing or malformed."""


HANDOVER = "handover"  # route name used for place_near_user shaping


@dataclass(frozen=True)
class HandSpot:
    """One handover set's marked hand spot (``Handover.sets``): where the person's hand
    is held for that set's ``<set>_release`` pose, measured at teach time."""

    spot: Point  # table coords under the set's release pose
    clear_radius: float  # no hand landmark may be within this of the spot
    measured: bool = False  # False = placeholder (dropped as proof on hardware)
    camera_sees_past_arm: bool = False  # as Handover.camera_sees_past_arm, for this set


@dataclass(frozen=True)
class Handover:
    """The one taught path to the marked spot near the user (place_near_user)."""

    start_pose: str  # taught pose the arm must be at (measured) before it may go
    polyline: list[Point]  # table path from start_pose to the spot
    spot: Point  # marked spot, table coords (under handover_release)
    clear_radius: float  # no hand landmark may be within this of the spot
    measured: bool = False  # polyline measured at teach time (False = placeholder)
    # The scene camera sees a hand on the spot past the arm at the handover poses
    # (checked at teach time). False: the arm over the spot hides it, so it is not clear.
    camera_sees_past_arm: bool = False
    # Other handover sets (``arm.handover_sets``): pose prefix -> its own hand spot.
    # The default set (prefix "handover") uses the fields above.
    sets: dict[str, HandSpot] = field(default_factory=dict)

    def hand_spot(self, prefix: str) -> HandSpot | None:
        """The hand spot of the set whose poses are ``<prefix>_above`` / ``<prefix>_release``;
        None when that set has no spot (so a check against it fails closed)."""
        if prefix == HANDOVER:
            return HandSpot(self.spot, self.clear_radius, self.measured, self.camera_sees_past_arm)
        return self.sets.get(prefix)


@dataclass(frozen=True)
class RouteConfig:
    tags: dict[str, list[str]] = field(default_factory=dict)
    # route name -> pick slot id -> table polyline from that slot to the drop, in order
    routes: dict[str, dict[str, list[Point]]] = field(default_factory=dict)
    zones: dict[str, Zone] = field(default_factory=dict)
    high_routes: list[str] = field(default_factory=list)  # routes lift_higher may use
    handover: Handover | None = None  # None = no taught handover: place_near_user holds
    unmeasured: frozenset[tuple[str, str]] = frozenset()  # (route, slot) placeholders

    def for_proof(self, hardware: bool) -> RouteConfig:
        """The polylines that may prove a route clear. On hardware, placeholders
        (unmeasured polylines, an unmeasured handover path) are dropped."""
        if not hardware:
            return self
        routes = {
            name: {slot: poly for slot, poly in per.items() if (name, slot) not in self.unmeasured}
            for name, per in self.routes.items()
        }
        handover = self.handover if self.handover and self.handover.measured else None
        if handover is not None and handover.sets:
            handover = replace(
                handover, sets={k: v for k, v in handover.sets.items() if v.measured}
            )
        return replace(self, routes=routes, handover=handover, unmeasured=frozenset())


def _polyline(raw: Any, where: str) -> list[Point]:
    poly = [(float(x), float(y)) for x, y in raw]
    if len(poly) < 2:
        raise ValueError(f"{where} needs a polyline of 2+ points")
    return poly


def _routes(raw: Any) -> tuple[dict[str, dict[str, list[Point]]], frozenset[tuple[str, str]]]:
    """``routes: {name: {slot_id: {measured: bool, path: [[x, y], ...]}}}``. A flat
    polyline (the old one-path-for-every-slot format) is rejected."""
    routes: dict[str, dict[str, list[Point]]] = {}
    unmeasured: set[tuple[str, str]] = set()
    for name, per_slot in (raw or {}).items():
        if not isinstance(per_slot, dict):
            raise TypeError(f"routes.{name} must map slot ids to polylines")
        routes[str(name)] = {}
        for slot, entry in per_slot.items():
            if not isinstance(entry, dict):
                raise TypeError(f"routes.{name}.{slot} must be {{measured, path}}")
            routes[str(name)][str(slot)] = _polyline(entry["path"], f"routes.{name}.{slot}")
            if entry.get("measured") is not True:  # missing or not exactly true: placeholder
                unmeasured.add((str(name), str(slot)))
    return routes, frozenset(unmeasured)


def _handover(raw: Any) -> Handover | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("handover must be a mapping")
    poly = [(float(x), float(y)) for x, y in raw["polyline"]]
    sx, sy = raw["spot"]
    radius = float(raw["clear_radius"])
    if len(poly) < 2 or not radius > 0:
        raise ValueError("handover needs a polyline of 2+ points and clear_radius > 0")
    sets: dict[str, HandSpot] = {}
    for name, entry in (raw.get("sets") or {}).items():
        if not isinstance(entry, dict):
            raise TypeError(f"handover.sets.{name} must be a mapping")
        ex, ey = entry["spot"]
        r = float(entry["clear_radius"])
        if not r > 0:
            raise ValueError(f"handover.sets.{name} needs clear_radius > 0")
        sets[str(name)] = HandSpot(
            (float(ex), float(ey)), r,
            measured=entry.get("measured") is True,
            camera_sees_past_arm=entry.get("camera_sees_past_arm") is True,
        )
    return Handover(
        str(raw["from"]),
        poly,
        (float(sx), float(sy)),
        radius,
        measured=raw.get("measured") is True,
        camera_sees_past_arm=raw.get("camera_sees_past_arm") is True,
        sets=sets,
    )


def load_routes(path: Path) -> RouteConfig:
    """Parse routes.yaml. Raises RoutesUnavailable on any malformed entry."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise TypeError("top level must be a mapping")
        tags = {str(k): [str(t) for t in (v or [])] for k, v in (data.get("tags") or {}).items()}
        routes, unmeasured = _routes(data.get("routes"))
        zones: dict[str, Zone] = {}
        for name, box in (data.get("zones") or {}).items():
            if not _zone_ok(box):
                raise ValueError(f"zone {name!r} is not [xmin, ymin, xmax, ymax]")
            zones[str(name)] = tuple(float(v) for v in box)  # type: ignore[assignment]
        high = [str(r) for r in (data.get("high_routes") or [])]
        handover = _handover(data.get("handover"))
    except (OSError, yaml.YAMLError, TypeError, ValueError, AttributeError, KeyError) as exc:
        raise RoutesUnavailable(f"cannot read routes from {path}: {exc}") from exc
    return RouteConfig(tags, routes, zones, high, handover, unmeasured)


@dataclass(frozen=True)
class Shaping:
    route: str | None  # None with no problem = the plain taught plan, no via poses
    applied: list[str]  # ids of rules that shaped this carry
    speed_cap: float = 1.0  # share of the configured max speed, never above 1
    grip_cap: float = 1.0  # share of the object's max_grip, never above 1
    problem: str | None = None  # set = the gate must HOLD, with this as the spoken reason


def plan_carry(
    held_tags: Sequence[str],
    rules: Sequence[Rule] | RulesUnavailable,
    routes: RouteConfig | None,
    executable: Collection[str],
    slow_speed: float = 0.5,
    gentle_grip: float = 0.6,
    held_name: str = "it",
    *,
    slot_id: str | None = None,
) -> Shaping:
    """Apply approved rules that match the held object's tags to one carry from
    ``slot_id``. Only routes with a polyline for that slot are candidates, so a rule
    is never proven against another slot's path; no slot means no candidate.

    ``routes is None`` means routes.yaml could not be read, so the held object's
    tags are unknown too: any approved rule (or unreadable rules) then holds.
    """
    if routes is None:
        if isinstance(rules, RulesUnavailable) or any(r.approved for r in rules):
            return Shaping(
                None,
                [],
                problem=f"My route map is unavailable, so I won't carry the {held_name}.",
            )
        return Shaping(None, [])
    if isinstance(rules, RulesUnavailable):
        if held_tags:  # a rule might cover this object and we cannot read it
            return Shaping(
                None, [], problem=f"I can't read my saved rules, so I won't carry the {held_name}."
            )
        rules = []  # no rule can match an untagged object
    matched = matching_rules(held_tags, rules)
    candidates = {
        n: per[slot_id]
        for n, per in routes.routes.items()
        if n in executable and slot_id is not None and slot_id in per
    }
    if not matched:
        return Shaping(next(iter(candidates), None), [])
    if not candidates:
        return Shaping(
            None,
            [r.id for r in matched],
            problem=f"I have no measured route from {slot_id or 'there'} to check my rules "
            f"for the {held_name}, so I won't carry it.",
        )

    applied: list[str] = []
    by_response: dict[str, list[str]] = {}
    for r in matched:
        by_response.setdefault(r.response, []).append(r.id)
    if "lift_higher" in by_response:
        high = set(routes.high_routes)
        candidates = {n: p for n, p in candidates.items() if n in high}
        applied += by_response["lift_higher"]
    route, route_ids = choose_route(held_tags, candidates, routes.zones, matched)
    applied = route_ids + applied
    if route is None:
        avoided = sorted({r.avoid for r in matched if r.response == "route_around"})
        why = f"avoid the {', '.join(avoided)}" if avoided else "keep it high"
        return Shaping(
            None,
            applied,
            problem=f"Every route I know breaks a rule ({why}) for the {held_name}. "
            "Should I leave it, or will you move something?",
        )
    speed_cap = grip_cap = 1.0
    if "slow_down" in by_response:
        speed_cap = clamp(slow_speed, MIN_SPEED, 1.0)
        applied += by_response["slow_down"]
    if "grip_gentler" in by_response:
        grip_cap = clamp(gentle_grip, 0.0, 1.0)
        applied += by_response["grip_gentler"]
    return Shaping(route, list(dict.fromkeys(applied)), speed_cap, grip_cap)


def plan_handover(
    held_tags: Sequence[str],
    rules: Sequence[Rule] | RulesUnavailable,
    routes: RouteConfig | None,
    slow_speed: float = 0.5,
    gentle_grip: float = 0.6,
    held_name: str = "it",
) -> Shaping:
    """Shape a place_near_user carry like any other carry, over the single taught
    handover path. Fails closed: no route map or no taught handover holds, and a
    rule that removes the path (avoid zone crossed, lift_higher without the path in
    ``high_routes``) holds. The arm's start pose is checked by the caller."""
    if routes is None:
        return Shaping(None, [], problem=f"My route map is unavailable, so I won't move the {held_name}.")
    if routes.handover is None:
        return Shaping(None, [], problem="I have no taught path to the marked spot.")
    only = RouteConfig(
        routes.tags,
        {HANDOVER: {HANDOVER: routes.handover.polyline}},
        routes.zones,
        [HANDOVER] if HANDOVER in routes.high_routes else [],
    )
    shaping = plan_carry(
        held_tags, rules, only, {HANDOVER}, slow_speed, gentle_grip, held_name, slot_id=HANDOVER
    )
    if shaping.problem is None and shaping.route != HANDOVER:
        return Shaping(None, shaping.applied, problem="I can't prove the path to the spot is clear.")
    return shaping


def plan_held_move(
    held_tags: Sequence[str] | None,
    rules: Sequence[Rule] | RulesUnavailable,
    routes: RouteConfig | None,
    slow_speed: float = 0.5,
    held_name: str = "what I'm holding",
    where: str = "there",
) -> Shaping:
    """Home or point while the gripper is not proven empty: a carry with no taught
    table path. ``held_tags`` None means what is held is unknown. Pure and
    restrict-only: slow_down caps the speed; any rule that needs a route proof
    (route_around, lift_higher) holds, since this path has no polyline; unreadable
    rules, or rules that might match an unknown object, hold. grip_gentler has nothing
    to cap (these plans never command the gripper)."""
    if isinstance(rules, RulesUnavailable):
        return Shaping(None, [], problem=f"I can't read my saved rules, so I won't move "
                       f"{held_name} {where}.")
    approved = [r for r in rules if r.approved]
    if held_tags is None or routes is None:
        if approved:
            why = "my route map is unavailable" if routes is None else "I don't know what I'm holding"
            return Shaping(None, [], problem=f"{why[0].upper()}{why[1:]}, so I can't check my "
                           f"rules to move {held_name} {where}.")
        return Shaping(None, [])
    matched = matching_rules(held_tags, approved)
    proof = sorted({r.id for r in matched if r.response in ("route_around", "lift_higher")})
    if proof:
        avoided = sorted({r.avoid for r in matched if r.response == "route_around"})
        why = f"keeps it away from the {', '.join(avoided)}" if avoided else "keeps it high"
        return Shaping(None, proof, problem=f"I'm holding the {held_name} and I can't prove the "
                       f"way {where} {why}, so I'll stay here. Take it from me first.")
    slow = [r.id for r in matched if r.response == "slow_down"]
    speed_cap = clamp(slow_speed, MIN_SPEED, 1.0) if slow else 1.0
    return Shaping(None, slow, speed_cap=speed_cap)


def catalog_tags(routes: RouteConfig | None, objects: Sequence[dict[str, Any]]) -> set[str]:
    """Every tag any catalog object carries (routes.yaml tags plus armed.yaml ``tags``)."""
    tags = {t for ts in (routes.tags.values() if routes else []) for t in ts}
    return tags | {str(t) for o in objects for t in (o.get("tags") or [])}


def tags_for(
    object_id: str | None, routes: RouteConfig | None, catalog: dict[str, Any]
) -> list[str]:
    if object_id is None:
        return []
    tags = list(routes.tags.get(object_id, [])) if routes else []
    tags += [str(t) for t in (catalog.get(object_id, {}).get("tags") or []) if t not in tags]
    return tags


# ── the checker (docs/ARMED_IDEA.md section 4: "proven monotone, replayed against the incident")


@dataclass(frozen=True)
class Checks:
    """The two ticks on a proposed rule. ``restrictive``: the permitted set of
    (route, speed cap, grip cap) over every catalog object and pick slot shrank or
    stayed equal, never grew. ``prevented``: the incident's carry, replayed through
    ``plan_carry`` with the proposed rule, no longer crosses the zone the
    investigation named (or the cap applies, or the carry holds)."""

    restrictive: bool
    prevented: bool
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.restrictive and self.prevented

    def as_dict(self) -> dict[str, Any]:
        return {"restrictive": self.restrictive, "prevented": self.prevented,
                "detail": dict(self.detail)}

    def problems(self) -> list[str]:
        out = []
        if not self.restrictive:
            out.append(f"checker: not restrict-only ({self.detail.get('restrictive', '')})")
        if not self.prevented:
            out.append(f"checker: incident not prevented ({self.detail.get('prevented', '')})")
        return out


def _candidates(routes: RouteConfig, executable: Collection[str], slot_id: str | None):
    return {
        n: per[slot_id]
        for n, per in routes.routes.items()
        if n in executable and slot_id is not None and slot_id in per
    }


def permitted_table(
    routes: RouteConfig,
    rules: Sequence[Rule],
    executable: Collection[str],
    objects_tags: Mapping[str, Sequence[str]],
    slow_speed: float = 0.5,
    gentle_grip: float = 0.6,
) -> dict[tuple[str, str], Permitted]:
    """The permitted set of every (catalog object, pick slot) carry under ``rules``."""
    slots = sorted({slot for per in routes.routes.values() for slot in per})
    table: dict[tuple[str, str], Permitted] = {}
    for obj, tags in objects_tags.items():
        for slot in slots:
            table[(obj, slot)] = permitted_options(
                tags, _candidates(routes, executable, slot), routes.zones, rules,
                routes.high_routes, clamp(slow_speed, MIN_SPEED, 1.0),
                clamp(gentle_grip, 0.0, 1.0),
            )
    return table


def check_proposal(
    rule: Rule,
    rules: Sequence[Rule],
    routes: RouteConfig | None,
    executable: Collection[str],
    objects_tags: Mapping[str, Sequence[str]],
    held_tags: Sequence[str],
    slot_id: str | None,
    old_route: str | None = None,
    avoid: str | None = None,
    slow_speed: float = 0.5,
    gentle_grip: float = 0.6,
) -> Checks:
    """Both ticks for ``rule`` (taken as approved for the replay). Pure. ``old_route``
    is the route the incident's carry was on; ``avoid`` the zone the investigation
    named (defaults to the rule's). Fails closed: no route map = both ticks false."""
    if routes is None:
        return Checks(False, False, {"restrictive": "no route map", "prevented": "no route map"})
    approved = [r for r in rules if r.approved and r.id != rule.id]
    proposed = replace(rule, approved=True)
    before = permitted_table(routes, approved, executable, objects_tags, slow_speed, gentle_grip)
    after = permitted_table(
        routes, [*approved, proposed], executable, objects_tags, slow_speed, gentle_grip
    )
    restrictive, r_detail = is_restrictive(before, after)
    zone_name = avoid or rule.avoid
    zone = routes.zones.get(zone_name)
    held_str = ", ".join(held_tags) or "no tags"
    if not matching_rules(held_tags, [proposed]):
        return Checks(restrictive, False, {
            "restrictive": r_detail,
            "prevented": f"the rule needs {', '.join(rule.when_holding)}; what was held "
                         f"carried {held_str}, so the incident would recur",
        })
    shaping = plan_carry(
        held_tags, [*approved, proposed], routes, executable, slow_speed, gentle_grip,
        slot_id=slot_id,
    )
    if shaping.problem is not None:
        return Checks(restrictive, True, {
            "restrictive": r_detail,
            "prevented": "every route is removed: the carry would hold and ask",
        })
    candidates = _candidates(routes, executable, slot_id)
    if rule.response in ("route_around", "lift_higher"):
        if zone is None:
            return Checks(restrictive, False, {
                "restrictive": r_detail, "prevented": f"unknown zone {zone_name!r}"
            })
        old_poly = candidates.get(old_route or "")
        if old_poly is not None and not crosses(old_poly, zone):
            return Checks(restrictive, False, {
                "restrictive": r_detail,
                "prevented": f"the {old_route} route never crosses the {zone_name} as mapped; "
                             "the rule would not change this carry",
            })
        new_poly = candidates.get(shaping.route or "")
        if new_poly is None:
            return Checks(restrictive, False, {
                "restrictive": r_detail,
                "prevented": f"no polyline for {shaping.route} from {slot_id}",
            })
        what = f"{shaping.route} from {slot_id}"
        if rule.response == "lift_higher":
            # Over the zone, but high: the map is top-down, so a high route may pass over
            # it (the drink reaches a mouth behind the laptop). Prevented when the carry
            # moved from a route that is not high to one that is.
            high = set(routes.high_routes)
            ok = shaping.route in high and (old_route or "") not in high and shaping.route != old_route
            return Checks(restrictive, ok, {
                "restrictive": r_detail,
                "prevented": f"{what} is a high route over the {zone_name}" if ok
                else f"{what} is not a higher route than {old_route}",
            })
        clear = not crosses(new_poly, zone)
        return Checks(restrictive, clear, {
            "restrictive": r_detail,
            "prevented": f"{what} clears the {zone_name}" if clear
            else f"{what} still crosses the {zone_name}",
        })
    if rule.response == "slow_down":
        ok = shaping.speed_cap < 1.0
        note = ""
        if zone is not None and shaping.route in candidates and crosses(candidates[shaping.route], zone):
            note = f"; the {shaping.route} route still passes the {zone_name}, slower"
        return Checks(restrictive, ok, {
            "restrictive": r_detail,
            "prevented": f"speed capped at {round(shaping.speed_cap * 100)}%{note}" if ok
            else "no speed cap applied",
        })
    ok = shaping.grip_cap < 1.0
    return Checks(restrictive, ok, {
        "restrictive": r_detail,
        "prevented": f"grip capped at {round(shaping.grip_cap * 100)}% of the object's maximum"
        if ok else "no grip cap applied",
    })


def rehearsal_routes(
    routes: RouteConfig | None, slot_id: str | None, old_route: str | None, new_route: str | None
) -> dict[str, Any]:
    """The twin's picture: the incident's polyline and the proposed one from the same
    pick slot, in table coordinates, with whether they were measured or are placeholders."""
    out: dict[str, Any] = {"old_route": None, "new_route": None,
                           "source": {"file": "configs/routes.yaml", "measured": False}}
    if routes is None or slot_id is None:
        return out
    measured = True
    for key, name in (("old_route", old_route), ("new_route", new_route)):
        per = routes.routes.get(name or "")
        if per and slot_id in per:
            out[key] = [[round(x, 4), round(y, 4)] for x, y in per[slot_id]]
            out[f"{key}_name"] = name
            measured = measured and (name, slot_id) not in routes.unmeasured
    out["source"]["measured"] = bool(measured and (out["old_route"] or out["new_route"]))
    return out


def applied_detail(
    applied: Sequence[str], rules: Sequence[Rule], held_tags: Sequence[str]
) -> list[dict[str, Any]]:
    """Why each applied rule applied: its 1-based index in the store, the held tags
    that matched it, and what it does. For the stage view's "rule 1 applied - liquid"."""
    held = set(held_tags)
    by_id = {r.id: (i + 1, r) for i, r in enumerate(rules)}
    out = []
    for rid in applied:
        if rid not in by_id:
            continue
        index, r = by_id[rid]
        out.append({
            "id": r.id, "index": index, "tags": [t for t in r.when_holding if t in held],
            "response": r.response, "avoid": r.avoid,
        })
    return out
