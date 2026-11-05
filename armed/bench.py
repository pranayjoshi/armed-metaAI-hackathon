"""Benchmark report from recorded sessions (`armed bench`).

Each trial is the span of events between ``trial.started`` and ``trial.ended``,
labeled by the operator with the expected action/object, a condition (which
inputs were allowed) and an outcome. Metrics:

- wrong-action rate: the arm moved, but not as intended (headline; lower is better)
- success rate per condition, with Wilson 95% intervals
- ask / clarify rate: how often the system asked instead of acting
- confirmation channel: EMG squeeze vs voice vs click
- latency: request -> decision, ASR time, open hand -> arm stopped
- grip: applied effort and crushed/dropped outcomes per object
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from armed.core import ROOT
from armed.recorder import SESSIONS

WRONG = {"wrong_object", "wrong_action"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct_ci(k: int, n: int) -> str:
    if n == 0:
        return "—"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} [{lo:.0%}, {hi:.0%}]"


def ms_stats(values: list[float]) -> str:
    if not values:
        return "—"
    values = sorted(values)
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return f"p50 {statistics.median(values):.0f} ms · p95 {p95:.0f} ms · n={len(values)}"


def load_session(path: Path) -> tuple[list[dict], list[dict]]:
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines() if line]
    trials_file = path / "trials.jsonl"
    trials = (
        [json.loads(line) for line in trials_file.read_text().splitlines() if line]
        if trials_file.exists()
        else []
    )
    return events, trials


def split_trials(events: list[dict]) -> list[dict[str, Any]]:
    out, current = [], None
    for e in events:
        if e["kind"] == "trial.started":
            current = {"meta": e, "events": []}
        elif e["kind"] == "trial.ended" and current is not None:
            current["end"] = e
            out.append(current)
            current = None
        elif current is not None:
            current["events"].append(e)
    return out


def analyse(trials: list[dict[str, Any]], all_events: list[dict]) -> dict[str, Any]:
    by_cond: dict[str, Counter] = defaultdict(Counter)
    asks = clarifies = moved = wrong = auto_wrong = 0
    channels: Counter = Counter()
    decide_ms: list[float] = []
    asr_ms: list[float] = []
    grips: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for trial in trials:
        meta, outcome = trial["meta"], trial["end"]["outcome"]
        cond = meta.get("condition") or "unspecified"
        by_cond[cond][outcome] += 1
        evs = trial["events"]
        verdicts = [e["decision"] for e in evs if e["kind"] == "intent"]
        asks += any(v["verdict"] == "ask" for v in verdicts)
        clarifies += any(v.get("clarify") for v in verdicts)
        started = [e for e in evs if e["kind"] == "arm.started"]
        if started:
            moved += 1
            first = started[0]
            channels[first.get("confirmed_by", "?")] += 1
            expected_obj = meta.get("expected_object")
            if expected_obj and first.get("object_id") and first["object_id"] != expected_obj:
                auto_wrong += 1
            if first.get("grip"):
                grips[first.get("object_id") or "?"].append((first["grip"]["effort"], outcome))
        wrong += outcome in WRONG
        utter = next((e for e in evs if e["kind"] == "speech.utterance"), None)
        decision = next((e for e in evs if e["kind"] == "intent"), None)
        if utter and decision:
            decide_ms.append((decision["t"] - utter["t"]) * 1000)
        asr_ms += [e["asr_ms"] for e in evs if e["kind"] == "speech.utterance" and e.get("asr_ms")]
    stops = []
    for i, e in enumerate(all_events):
        if e["kind"] == "emg.open_hand":
            nxt = next((x for x in all_events[i + 1 : i + 40] if x["kind"] == "arm.stopped"), None)
            if nxt and nxt["t"] - e["t"] < 1.0:
                stops.append((nxt["t"] - e["t"]) * 1000)
    return {
        "n": len(trials),
        "by_cond": by_cond,
        "asks": asks,
        "clarifies": clarifies,
        "moved": moved,
        "wrong": wrong,
        "auto_wrong": auto_wrong,
        "channels": channels,
        "decide_ms": decide_ms,
        "asr_ms": asr_ms,
        "stop_ms": stops,
        "grips": grips,
    }


def render(result: dict[str, Any], sessions: list[str]) -> str:
    n = result["n"]
    lines = [
        "# Armed benchmark report",
        "",
        f"Sessions: {', '.join(sessions) or 'none'} · trials: {n}",
        "",
        "## Headline",
        "",
        f"- **Wrong-action rate** (arm moved, not as intended): {pct_ci(result['wrong'], n)}",
        f"- Auto-check, executed object ≠ expected object: {result['auto_wrong']} trials",
        f"- Asked before acting: {pct_ci(result['asks'], n)} · needed a choice (which one?): "
        f"{pct_ci(result['clarifies'], n)}",
        f"- Confirmed by: {', '.join(f'{k} {v}' for k, v in result['channels'].most_common()) or '—'}",
        "",
        "## Success by condition",
        "",
        "| Condition | Success (95% CI) | Wrong | No action | Dropped | Crushed |",
        "|---|---|---|---|---|---|",
    ]
    for cond, counts in sorted(result["by_cond"].items()):
        total = sum(counts.values())
        lines.append(
            f"| {cond} | {pct_ci(counts['success'], total)} | "
            f"{counts['wrong_object'] + counts['wrong_action']} | {counts['no_action']} | "
            f"{counts['dropped']} | {counts['crushed']} |"
        )
    lines += [
        "",
        "## Latency",
        "",
        f"- Request (utterance) → decision: {ms_stats(result['decide_ms'])}",
        f"- Speech recognition: {ms_stats(result['asr_ms'])}",
        f"- Open hand (EMG) → arm stopped (software): {ms_stats(result['stop_ms'])}",
        "",
        "## Grip by object",
        "",
        "| Object | Trials | Mean applied grip | Crushed | Dropped |",
        "|---|---|---|---|---|",
    ]
    for obj, rows in sorted(result["grips"].items()):
        efforts = [r[0] for r in rows]
        outcomes = Counter(r[1] for r in rows)
        lines.append(
            f"| {obj} | {len(rows)} | {statistics.mean(efforts):.2f} | "
            f"{outcomes['crushed']} | {outcomes['dropped']} |"
        )
    lines += [
        "",
        "Small n is reported as is. Intervals are Wilson 95%. Stop latency excludes "
        "servo response; measure physical stop time with video for the final number.",
    ]
    return "\n".join(lines) + "\n"


def main(
    paths: list[str] | None = None,
    out: str | None = None,
    results: bool = False,
    results_out: str | None = None,
) -> int:
    dirs = (
        [Path(p) for p in paths] if paths else sorted(p for p in SESSIONS.glob("*") if p.is_dir())
    )
    trials, all_events, names = [], [], []
    for d in dirs:
        if not (d / "events.jsonl").exists():
            continue
        events, _ = load_session(d)
        trials += split_trials(events)
        all_events += events
        names.append(d.name)
    report = render(analyse(trials, all_events), names)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(report)
    if results:
        target = Path(results_out) if results_out else RESULTS
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_results(load_sessions(dirs)))
        print(f"wrote {target}")
        return 0
    print(report)
    return 0


# ── the submission results table (`armed bench --results`) ─────────────────────
#
# One row per claim in the README's numbers table. A row is "measured" only when the
# recorded sessions hold the events it needs; sessions whose participant is
# "selftest" (synthetic EMG, virtual arm, typed speech) are reported apart and never
# count as a result. A row with no data says "not recorded", never a target.

RESULTS = ROOT / "docs" / "RESULTS.md"
NOT_MEASURED = "not recorded"
SELFTEST = "selftest"
TYPING = "typing"  # trial condition for the false-twitch row
CAPTION = "Two people, one day; this does not establish a general result."
VETOES = ("brace", "open_hand", "ui_dismiss")


def load_sessions(dirs: list[Path]) -> list[dict[str, Any]]:
    """Recorded sessions as dicts: name, participant, selftest flag, events, wall start."""
    out = []
    for d in dirs:
        if not (d / "events.jsonl").exists():
            continue
        events, _ = load_session(d)
        meta_file = d / "meta.json"
        meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
        participant = str(meta.get("participant") or d.name.split("-")[-1])
        wall0 = None
        try:
            wall0 = time.mktime(time.strptime(meta["started"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, ValueError):
            pass
        first = next((e["t"] for e in events if e["kind"] == "session.started"), None)
        out.append(
            {
                "name": d.name,
                "participant": participant,
                "selftest": participant == SELFTEST or d.name.endswith(SELFTEST),
                "events": events,
                "wall0": wall0,
                "t0": first if first is not None else (events[0]["t"] if events else 0.0),
            }
        )
    return out


def _stop_ms(sessions: list[dict[str, Any]]) -> list[float]:
    """Brace or open hand -> arm.stopped in the event log (software only)."""
    stops = []
    for s in sessions:
        evs = s["events"]
        for i, e in enumerate(evs):
            if e["kind"] in ("emg.brace", "emg.open_hand"):
                nxt = next((x for x in evs[i + 1 : i + 40] if x["kind"] == "arm.stopped"), None)
                if nxt and nxt["t"] - e["t"] < 1.0:
                    stops.append((nxt["t"] - e["t"]) * 1000)
    return stops


def _tasks(sessions: list[dict[str, Any]], mode: str) -> list[tuple[dict, dict]]:
    return [
        (s, e)
        for s in sessions
        for e in s["events"]
        if e["kind"] in ("task.completed", "task.abandoned") and e.get("mode") == mode
    ]


def _effort(sessions: list[dict[str, Any]], mode: str) -> tuple[str, int] | None:
    done = [e for _, e in _tasks(sessions, mode) if e["kind"] == "task.completed"]
    if not done:
        return None
    inputs = [float(e["selections"]) for e in done]
    secs = [float(e["to_complete_s"]) for e in done if e.get("to_complete_s") is not None]
    text = (
        f"{statistics.median(inputs):g} inputs (median, range {min(inputs):g}-{max(inputs):g}), "
        f"{statistics.median(secs):.1f} s (median) per task, n={len(done)} completed tasks"
        if secs
        else f"{statistics.median(inputs):g} inputs (median), n={len(done)}"
    )
    return text, len(done)


def _first_time(sessions: list[dict[str, Any]]) -> str | None:
    tasks = _tasks(sessions, "intent")
    if not tasks:
        return None
    hours: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    ok_total = 0
    for s, e in tasks:
        c = e.get("counts") or {}
        first = (
            e["kind"] == "task.completed"
            and not c.get("double_twitch")
            and not c.get("ui_step")
            and not any(c.get(v) for v in VETOES)
        )
        ok_total += first
        if s["wall0"] is not None:
            hour = time.strftime("%H:00", time.localtime(s["wall0"] + e["t"] - s["t0"]))
            hours[hour][0] += first
            hours[hour][1] += 1
    by_hour = " · ".join(f"{h} {k}/{n}" for h, (k, n) in sorted(hours.items()))
    return f"{pct_ci(ok_total, len(tasks))} proposals" + (
        f"; by hour: {by_hour}" if by_hour else ""
    )


def _typing(sessions: list[dict[str, Any]]) -> str | None:
    minutes, twitches, braces, n = 0.0, 0, 0, 0
    for s in sessions:
        for trial in split_trials(s["events"]):
            if TYPING not in str(trial["meta"].get("condition") or ""):
                continue
            n += 1
            minutes += (trial["end"]["t"] - trial["meta"]["t"]) / 60
            twitches += sum(e["kind"] == "emg.twitch" for e in trial["events"])
            braces += sum(e["kind"] == "emg.brace" for e in trial["events"])
    if not n or minutes <= 0:
        return None
    per10 = 10 / minutes
    return (
        f"{twitches} false twitches, {braces} false braces in {minutes:.1f} min of typing "
        f"({twitches * per10:.1f} and {braces * per10:.1f} per 10 min), n={n} typing trials"
    )


def _learns(sessions: list[dict[str, Any]]) -> str | None:
    approved = sum(e["kind"] == "rule.approved" for s in sessions for e in s["events"])
    if not approved:
        return None
    applied = sum(
        bool((e.get("contract") or {}).get("applied_rules"))
        for s in sessions
        for e in s["events"]
        if e["kind"] == "intent"
    )
    return (
        f"{approved} rules approved; {applied} later contracts carried applied_rules. "
        "The tea high-route and refusal-recurrence checks are not scored by bench: "
        f"{NOT_MEASURED}"
    )


def _transfer(sessions: list[dict[str, Any]]) -> str | None:
    rows = []
    for s in sessions:
        done = next((e for e in s["events"] if e["kind"] == "task.completed"), None)
        if done is not None:
            rows.append((s["participant"], done["t"] - s["t0"]))
    if len({p for p, _ in rows}) < 2:
        return None
    return "; ".join(f"{p}: {t / 60:.1f} min to first completed task" for p, t in rows) + (
        f", n={len(rows)} sessions (the first refusal is not scored by bench)"
    )


def results_rows(sessions: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """(claim, metric, value, source) rows; source is measured / self-test only / not measured."""
    real = [s for s in sessions if not s["selftest"]]
    test = [s for s in sessions if s["selftest"]]
    rows = []

    def row(claim: str, metric: str, fn: Any, *, suffix: str = "") -> None:
        got, got_test = fn(real), fn(test)
        if got:
            rows.append((claim, metric, got + suffix, "measured"))
        elif got_test:
            rows.append(
                (
                    claim,
                    metric,
                    f"{got_test}{suffix} (self-test: synthetic EMG, virtual arm)",
                    "self-test only",
                )
            )
        else:
            rows.append((claim, metric, NOT_MEASURED, "not measured"))

    def effort(ss: list[dict[str, Any]]) -> str | None:
        armed, scan = _effort(ss, "intent"), _effort(ss, "scanning")
        if armed and scan:
            return f"Armed {armed[0]} · scanning {scan[0]}"
        have = armed or scan
        if have:
            which = "Armed" if armed else "scanning"
            return f"{which} only: {have[0]}; the comparison needs both modes"
        return None

    base = " Published scanning baseline: 4.9 selections per minute at 2.2 switch hits per selection (Bonaker et al., CHI 2022)."
    row(
        "Less effort",
        "inputs per task and seconds per task, Armed vs scanning",
        effort,
        suffix=base,
    )
    row("Proposals are right", "accepted first time / proposals, by hour", _first_time)

    def stop(ss: list[dict[str, Any]]) -> str | None:
        ms = _stop_ms(ss)
        return (
            f"software {ms_stats(ms)}; physical stop from 60 fps video: {NOT_MEASURED}"
            if ms
            else None
        )

    row("Stops fast and locally", "software p50/p95 ms; physical stop from 60 fps video", stop)
    row(
        "Doesn't stop for nothing",
        "false twitches and false braces in ten minutes of typing",
        _typing,
    )
    row(
        "Learns",
        "rule applied on the next carry; tea on the high route; refusal not recurring",
        _learns,
    )
    row("Transfers", "second person: setup time to first task; their first refusal", _transfer)
    rows.append(
        (
            "Costs",
            "API calls and cost for the day",
            f"{NOT_MEASURED}: the runtime does not log per-call cost; copy the provider's usage page",
            "not measured",
        )
    )
    return rows


def render_results(sessions: list[dict[str, Any]]) -> str:
    rows = results_rows(sessions)
    lines = [
        "# Results",
        "",
        "Written by `armed bench --results` from the sessions under `data/sessions/`. Every value "
        "carries its n; a row with no recorded data says `not recorded`. Intervals are "
        "Wilson 95%. Nothing here is a target.",
        "",
        f"_{CAPTION}_",
        "",
        "| Claim | Metric | Value | Source |",
        "|---|---|---|---|",
    ]
    for claim, metric, value, source in rows:
        lines.append(f"| {claim} | {metric} | {value.replace('|', '/')} | {source} |")
    lines += ["", "## Sessions read", ""]
    if not sessions:
        lines.append("None.")
    for s in sessions:
        trials = split_trials(s["events"])
        tag = (
            "self-test: synthetic EMG, virtual arm, typed speech; not a result"
            if s["selftest"]
            else "recorded"
        )
        lines.append(
            f"- `{s['name']}` · participant `{s['participant']}` · {len(trials)} labelled trials · {tag}"
        )
    selftest_trials = [t for s in sessions if s["selftest"] for t in split_trials(s["events"])]
    if selftest_trials:
        events = [e for s in sessions if s["selftest"] for e in s["events"]]
        r = analyse(selftest_trials, events)
        lines += [
            "",
            "## Self-test trials",
            "",
            f"{r['n']} trials on the virtual arm with synthetic EMG, run to check the tooling. "
            "They say nothing about people.",
            "",
            "| Condition | Success (95% CI) | Wrong | No action |",
            "|---|---|---|---|",
        ]
        for cond, counts in sorted(r["by_cond"].items()):
            total = sum(counts.values())
            lines.append(
                f"| {cond} | {pct_ci(counts['success'], total)} | "
                f"{counts['wrong_object'] + counts['wrong_action']} | {counts['no_action']} |"
            )
    lines += [
        "",
        "## Raw takes and failure clips",
        "",
        "Raw takes and the event log are under `data/sessions/`.",
        "",
    ]
    return "\n".join(lines)
