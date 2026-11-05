import pytest

from armed.contract import StepContract, Verb
from armed.rules import Rule, RuleStore, RulesUnavailable, choose_route

ROUTES = {
    "direct": [(0.0, 0.0), (1.0, 0.0)],
    "via_left": [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
    "via_high": [(0.0, 0.0), (0.5, -1.0), (1.0, -1.0)],
}
ZONES = {"laptop": (0.4, -0.1, 0.6, 0.1)}


def rule(tags=("liquid",), response="route_around", approved=True):
    return Rule(list(tags), "laptop", response, approved=approved)


def test_no_rules_changes_nothing():
    assert choose_route(["liquid"], ROUTES, ZONES, []) == ("direct", [])


def test_rule_only_restricts():
    r = rule()
    name, applied = choose_route(["liquid"], ROUTES, ZONES, [r])
    assert name == "via_left" and applied == [r.id]
    # a rule that does not match never changes the choice
    assert choose_route(["block"], ROUTES, ZONES, [r]) == ("direct", [])
    # unapproved rules are ignored
    assert choose_route(["liquid"], ROUTES, ZONES, [rule(approved=False)])[0] == "direct"


def test_no_route_left_returns_none():
    zones = {"laptop": (-5.0, -5.0, 5.0, 5.0)}
    name, applied = choose_route(["liquid"], ROUTES, zones, [rule()])
    assert name is None and applied


def test_tag_generalization():
    cup_rule = Rule(["liquid"], "laptop", "route_around", approved=True)
    bottle_tags = ["liquid"]  # a bottle never seen in the incident
    assert choose_route(bottle_tags, ROUTES, ZONES, [cup_rule])[0] == "via_left"


def test_invalid_response_rejected():
    with pytest.raises(ValueError):
        Rule(["liquid"], "laptop", "ignore_safety")


def test_store_requires_approval_and_roundtrips(tmp_path):
    store = RuleStore("ana", root=tmp_path)
    with pytest.raises(PermissionError):
        store.add(rule(approved=False))
    assert store.list() == []
    saved = store.add(rule())
    assert [r.id for r in store.list()] == [saved.id]
    assert store.delete(saved.id) and not store.delete(saved.id)
    assert store.list() == []


def test_contract_fields():
    d = StepContract(verb=Verb.HOME).as_dict()
    assert d["route"] is None and d["applied_rules"] == []


def test_unknown_zone_fails_closed():
    r = Rule(["liquid"], "ghost_zone", "route_around", approved=True)
    name, applied = choose_route(["liquid"], ROUTES, ZONES, [r])
    assert name is None and applied == [r.id]
    # a non-matching rule with an unknown zone is irrelevant
    assert choose_route(["block"], ROUTES, ZONES, [r]) == ("direct", [])


@pytest.mark.parametrize(
    "zone", [(0.6, -0.1, 0.4, 0.1), (0.4, 0.1, 0.6, -0.1), (0.4, 0.0, float("nan"), 0.1), (1, 2, 3)]
)
def test_malformed_zone_fails_closed(zone):
    name, applied = choose_route(["liquid"], ROUTES, {"laptop": zone}, [rule()])
    assert name is None and applied == [rule().id] or (name is None and len(applied) == 1)


def test_degenerate_route_counts_as_crossing_everything():
    routes = {"stub": [(0.0, 0.0)], "empty": [], **ROUTES}
    assert choose_route(["liquid"], routes, ZONES, [rule()])[0] == "via_left"
    only_stubs = {"stub": [(0.0, 0.0)], "empty": []}
    assert choose_route(["liquid"], only_stubs, ZONES, [rule()])[0] is None


def test_rule_validation_helper():
    r = rule()
    assert r.validate({"laptop"}, {"liquid", "sharp"}) == []
    problems = Rule(["mystery"], "nowhere", "slow_down").validate({"laptop"}, {"liquid"})
    assert any("avoid zone" in p for p in problems) and any("tag" in p for p in problems)


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        '{"a": 1}',
        '[{"nope": 1}]',
        '[{"when_holding": [], "avoid": "x", "response": "slow_down"}]',
    ],
)
def test_corrupt_store_raises_rules_unavailable(tmp_path, content):
    store = RuleStore("ana", root=tmp_path)
    store.path.write_text(content)
    with pytest.raises(RulesUnavailable):
        store.list()
    with pytest.raises(RulesUnavailable):  # add/delete never overwrite a corrupt file
        store.add(rule())
    assert store.path.read_text() == content


@pytest.mark.parametrize(
    "bad",
    [
        {"when_holding": "liquid", "avoid": "laptop", "response": "route_around"},
        {"when_holding": [["liquid"]], "avoid": "laptop", "response": "route_around"},
        {"when_holding": [""], "avoid": "laptop", "response": "route_around"},
        {"when_holding": ["liquid"], "avoid": ["laptop"], "response": "route_around"},
        {"when_holding": ["liquid"], "avoid": "", "response": "route_around"},
    ],
)
def test_wrong_typed_fields_are_rules_unavailable(tmp_path, bad):
    import json

    store = RuleStore("ana", root=tmp_path)
    store.path.write_text(json.dumps([{**bad, "approved": True}]))
    with pytest.raises(RulesUnavailable):
        store.list()
    with pytest.raises(ValueError):
        Rule(**bad)

# imports added with the tests below
from armed.rules import Permitted, is_restrictive, permitted_options  # noqa: E402


# added alongside today's changes
def test_permitted_options_shrinks_under_rules_and_fails_closed():
    none = permitted_options(["liquid"], ROUTES, ZONES, [])
    assert none == Permitted(frozenset(ROUTES), 1.0, 1.0)
    around = permitted_options(["liquid"], ROUTES, ZONES, [rule()])
    assert around.routes == frozenset({"via_left", "via_high"})  # direct crosses the laptop
    high = permitted_options(["liquid"], ROUTES, ZONES, [rule(response="lift_higher")], ["via_high"])
    assert high.routes == frozenset({"via_high"})
    slow = permitted_options(["liquid"], ROUTES, ZONES, [rule(response="slow_down")], (), 0.5, 0.6)
    assert slow == Permitted(frozenset(ROUTES), 0.5, 1.0)
    gentle = permitted_options(["liquid"], ROUTES, ZONES, [rule(response="grip_gentler")], (), 0.5, 0.6)
    assert gentle.grip_cap == 0.6 and gentle.routes == frozenset(ROUTES)
    # a rule that does not match the held object changes nothing
    assert permitted_options(["block"], ROUTES, ZONES, [rule()]) == none
    # a matching rule with an unknown zone leaves no route (fail closed), like choose_route
    ghost = Rule(["liquid"], "ghost", "route_around", approved=True)
    assert permitted_options(["liquid"], ROUTES, ZONES, [ghost]).routes == frozenset()

def test_is_restrictive_accepts_shrinks_and_rejects_any_growth():
    before = {("cup", "slot_1"): Permitted(frozenset(ROUTES), 1.0, 1.0),
              ("block", "slot_1"): Permitted(frozenset(ROUTES), 1.0, 1.0)}
    after = {("cup", "slot_1"): Permitted(frozenset({"via_left"}), 0.5, 1.0),
             ("block", "slot_1"): Permitted(frozenset(ROUTES), 1.0, 1.0)}
    ok, detail = is_restrictive(before, after)
    assert ok and "routes 6 -> 4" in detail and "none added" in detail
    assert is_restrictive(before, before)[0]  # equal is fine
    assert is_restrictive(before, {})[0]  # everything removed is a shrink
    grown = {**after, ("cup", "slot_1"): Permitted(frozenset(ROUTES) | {"teleport"}, 0.5, 1.0)}
    ok, detail = is_restrictive(before, grown)
    assert not ok and "teleport" in detail
    raised = {**after, ("cup", "slot_1"): Permitted(frozenset({"via_left"}), 1.0, 1.0)}
    assert is_restrictive({**before, ("cup", "slot_1"): Permitted(frozenset(ROUTES), 0.5, 1.0)},
                          raised)[0] is False
    new_carry = {**after, ("tea", "slot_1"): Permitted(frozenset(ROUTES), 1.0, 1.0)}
    assert is_restrictive(before, new_carry)[0] is False
