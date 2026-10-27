"""Benchmark report from recorded sessions (`h2m bench`).

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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from h2m.recorder import SESSIONS

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
        "# H2M benchmark report",
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


def main(paths: list[str] | None = None, out: str | None = None) -> int:
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
    print(report)
    return 0
