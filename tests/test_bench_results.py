"""`armed bench --results`: the results table is measured, self-test only, or not measured."""

from __future__ import annotations

import json
from pathlib import Path

from armed.bench import NOT_MEASURED, load_sessions, main, render_results, results_rows


def write_session(root: Path, name: str, participant: str, events: list[dict]) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps(
            {"session_id": name, "participant": participant, "started": "2026-11-03 14:00:00"}
        )
    )
    start = {"kind": "session.started", "t": 100.0, "participant": participant}
    body = [start, *events]
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps({"seq": i, **e}) for i, e in enumerate(body))
    )
    return d


def task(kind: str, mode: str, t: float, **counts: int) -> dict:
    full = {"twitch": 0, "double_twitch": 0, "brace": 0, "open_hand": 0, "ui_step": 0, **counts}
    data = {"kind": kind, "t": t, "mode": mode, "counts": full, "selections": sum(full.values())}
    if kind == "task.completed":
        data["to_complete_s"] = 4.0 if mode == "intent" else 20.0
    return data


def row(rows: list[tuple[str, str, str, str]], claim: str) -> tuple[str, str, str, str]:
    return next(r for r in rows if r[0] == claim)


def test_no_sessions_every_row_is_not_measured():
    rows = results_rows([])
    assert len(rows) == 7
    assert all(r[2].startswith(NOT_MEASURED) and r[3] == "not measured" for r in rows)
    text = render_results([])
    assert "this does not establish a general result" in text
    assert "TODO (URL)" in text


def test_selftest_stop_latency_is_labelled_self_test(tmp_path):
    events = [
        {"kind": "emg.open_hand", "t": 110.0},
        {"kind": "arm.stopped", "t": 110.003},
    ]
    d = write_session(tmp_path, "s-selftest", "selftest", events)
    rows = results_rows(load_sessions([d]))
    stop = row(rows, "Stops fast and locally")
    assert stop[3] == "self-test only"
    assert "n=1" in stop[2] and "self-test" in stop[2]
    assert "physical stop from 60 fps video: TODO (not measured)" in stop[2]
    assert [r[3] for r in rows].count("self-test only") == 1


def test_real_session_rows_carry_n_and_intervals(tmp_path):
    events = [
        task("task.completed", "intent", 111.0, twitch=2),
        task("task.completed", "intent", 121.0, twitch=3, double_twitch=1),
        task("task.abandoned", "intent", 131.0, brace=1),
        task("task.completed", "scanning", 141.0, twitch=6, ui_step=5),
        {"kind": "emg.brace", "t": 150.0},
        {"kind": "arm.stopped", "t": 150.02},
    ]
    d = write_session(tmp_path, "s-pranay", "pranay", events)
    rows = results_rows(load_sessions([d]))
    effort = row(rows, "Less effort")
    assert effort[3] == "measured"
    assert "Armed" in effort[2] and "scanning" in effort[2] and "n=2" in effort[2]
    assert "baseline" in effort[2] and "TODO" in effort[2]
    first = row(rows, "Proposals are right")
    assert first[3] == "measured"
    assert "1/3 = 33%" in first[2] and "[" in first[2]
    assert "14:00" in first[2]
    stop = row(rows, "Stops fast and locally")
    assert stop[3] == "measured" and "n=1" in stop[2]
    assert row(rows, "Transfers")[3] == "not measured"


def test_one_mode_alone_does_not_claim_a_comparison(tmp_path):
    d = write_session(
        tmp_path, "s-pranay", "pranay", [task("task.completed", "intent", 111.0, twitch=2)]
    )
    effort = row(results_rows(load_sessions([d])), "Less effort")
    assert "needs both modes" in effort[2]


def test_typing_trials_give_false_event_rate(tmp_path):
    events = [
        {"kind": "trial.started", "t": 200.0, "condition": "typing"},
        {"kind": "emg.twitch", "t": 230.0},
        {"kind": "emg.twitch", "t": 260.0},
        {"kind": "emg.brace", "t": 290.0},
        {"kind": "trial.ended", "t": 320.0, "outcome": "success"},
    ]
    d = write_session(tmp_path, "s-pranay", "pranay", events)
    typing = row(results_rows(load_sessions([d])), "Doesn't stop for nothing")
    assert typing[3] == "measured"
    assert "2 false twitches, 1 false braces in 2.0 min" in typing[2]
    assert "n=1 typing trials" in typing[2]


def test_transfer_needs_two_real_participants(tmp_path):
    done = [task("task.completed", "intent", 160.0, twitch=2)]
    a = write_session(tmp_path, "a-pranay", "pranay", done)
    assert row(results_rows(load_sessions([a])), "Transfers")[3] == "not measured"
    b = write_session(tmp_path, "b-second", "second", done)
    transfer = row(results_rows(load_sessions([a, b])), "Transfers")
    assert transfer[3] == "measured" and "second:" in transfer[2] and "n=2" in transfer[2]


def test_main_writes_results_file(tmp_path):
    sessions = tmp_path / "sessions"
    d = write_session(sessions, "s-selftest", "selftest", [])
    target = tmp_path / "RESULTS.md"
    assert main([str(d)], results=True, results_out=str(target)) == 0
    text = target.read_text()
    assert text.startswith("# Results")
    assert "self-test" in text
    assert "| Costs |" in text
