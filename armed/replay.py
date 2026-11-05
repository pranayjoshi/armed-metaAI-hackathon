"""Replay of recorded incidents and sessions: no arm, no API key, no network.

Reads what the runtime saved (``data/incidents/<id>/`` meta.json, keyframes, emg.npy,
explanation.json; ``data/sessions/<id>/`` events.jsonl, trials.jsonl) and turns it into
JSON payloads for the replay UI (armed/server.py::create_replay_app, docs/REPLAY_API.md).

Read-only: nothing here writes a file, saves a rule, moves an arm or calls a model.
Every payload says where the data came from (``label``: SIMULATION, RECORDED or
UNKNOWN SOURCE). An incident with no stored explanation gets the fixed text below,
labelled ``canned``; it is never presented as model output. A proposed rule is shown
unapproved, with the problems the runtime would find saving it.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from armed.robot.backend import JOINTS
from armed.rules import Rule

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # folder names only: no path traversal
CAMS = ("overhead", "wrist")
MAX_EMG_POINTS = 2000
MAX_SESSION_EVENTS = 20000
MAX_LIBRARY = 200  # newest incidents listed per scanned folder

CANNED_NOTE = (
    "Canned explanation: fixed demo text from armed/replay.py. No model was called."
)
# What Muse Wonder is asked to return (armed/incident.py SYSTEM), filled in by hand.
CANNED_EXPLANATION: dict[str, Any] = {
    "cause_object": "laptop",
    "relation": "carried_over",
    "held_property": "liquid",
    "say": (
        "You braced as I carried the cup of water over your laptop. "
        "Should I keep liquids away from it?"
    ),
    "confidence": 0.8,
    "rule": {"when_holding": ["liquid"], "avoid": "laptop", "response": "route_around"},
}
CHANNELS_3 = ("flexor", "extensor", "brace")  # incident EMG: the fast brace window


class ReplayError(RuntimeError):
    """A recording is missing or unreadable."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ReplayError(f"{path.name} not found") from exc
    except (OSError, ValueError) as exc:
        raise ReplayError(f"{path.name} unreadable: {exc}") from exc


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


def source_label(meta: Mapping[str, Any]) -> dict[str, Any]:
    """How a recording is labelled. Anything synthetic or from the virtual arm is
    SIMULATION; only provenance proving hardware is RECORDED; else UNKNOWN SOURCE."""
    prov = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else None
    if meta.get("synthetic") is True or (prov is not None and prov.get("simulation") is True):
        return {"label": "SIMULATION", "simulation": True, "provenance": prov}
    if prov is not None and prov.get("simulation") is False:
        return {"label": "RECORDED", "simulation": False, "provenance": prov}
    return {"label": "UNKNOWN SOURCE", "simulation": None, "provenance": prov}


def canned_explanation() -> dict[str, Any]:
    return {
        **CANNED_EXPLANATION,
        "rule": dict(CANNED_EXPLANATION["rule"]),
        "source": "canned",
        "canned": True,
        "stored": False,
        "note": CANNED_NOTE,
    }


def rule_check(
    rule: Any, zone_names: Iterable[str], catalog_tags: Iterable[str]
) -> dict[str, Any] | None:
    """A proposed rule as the replay shows it: never approved, with the problems the
    runtime's validation would raise. None when there is no rule."""
    if not isinstance(rule, dict):
        return None
    try:
        r = Rule(
            when_holding=list(rule.get("when_holding") or []),
            avoid=rule.get("avoid"),  # type: ignore[arg-type]
            response=str(rule.get("response")),
        )
        problems = r.validate(set(zone_names), set(catalog_tags))
    except (TypeError, ValueError) as exc:
        problems = [str(exc)]
    return {
        "rule": {k: rule.get(k) for k in ("when_holding", "avoid", "response")},
        "approved": False,
        "valid": not problems,
        "problems": problems,
        "note": "Proposed only. Replay never saves a rule.",
    }


# ── incidents ────────────────────────────────────────────────────────────────
@dataclass
class IncidentReplay:
    id: str
    path: Path
    meta: dict[str, Any]
    explanation: dict[str, Any] | None  # stored explanation.json, if readable
    emg: np.ndarray | None  # (n, 1 + channels), column 0 = time
    emg_error: str | None = None

    @property
    def t_freeze(self) -> float:
        return _num(self.meta.get("t_freeze")) or 0.0

    def rel(self, t: Any) -> float | None:
        v = _num(t)
        return None if v is None else round(v - self.t_freeze, 3)

    # frames
    def frames(self, url_prefix: str = "") -> list[dict[str, Any]]:
        out = []
        for i, key in enumerate(self.meta.get("keyframes") or []):
            if not isinstance(key, dict):
                continue
            cams = {
                cam: f"{url_prefix}/frame/{i}/{cam}.jpg"
                for cam in CAMS
                if self.frame_path(i, cam) is not None
            }
            if cams:
                out.append({"index": i, "label": key.get("label"), "t": self.rel(key.get("t")),
                            "cams": cams})
        return out

    def frame_path(self, index: int, cam: str) -> Path | None:
        """The keyframe file, only if it is a .jpg inside this incident's folder."""
        keys = self.meta.get("keyframes") or []
        if cam not in CAMS or not 0 <= index < len(keys) or not isinstance(keys[index], dict):
            return None
        name = keys[index].get(cam)
        if not isinstance(name, str) or not name.endswith(".jpg"):
            return None
        root = self.path.resolve()
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target

    # EMG
    def emg_series(self, brace_effort: float = 0.35, brace_calm: float = 0.12) -> dict[str, Any]:
        data = self.emg
        base: dict[str, Any] = {
            "t": [], "channels": {}, "brace_effort": brace_effort, "brace_calm": brace_calm,
            "onset": self.brace_onset(brace_effort, brace_calm), "error": self.emg_error,
            "units": "normalized effort (0..1) on the fast brace window; t in s from the freeze",
        }
        if data is None or data.ndim != 2 or data.shape[0] == 0 or data.shape[1] < 2:
            return base
        step = max(1, math.ceil(data.shape[0] / MAX_EMG_POINTS))
        rows = data[::step]
        names = CHANNELS_3 if data.shape[1] == 4 else tuple(
            f"ch{i}" for i in range(1, data.shape[1])
        )
        base["t"] = [round(float(t) - self.t_freeze, 3) for t in rows[:, 0]]
        base["channels"] = {
            name: [round(float(v), 4) for v in rows[:, i + 1]] for i, name in enumerate(names)
        }
        base["downsampled_by"] = step
        return base

    def brace_onset(self, brace_effort: float, brace_calm: float) -> dict[str, Any] | None:
        """Brace onset relative to the freeze: as recorded with the trigger, else derived
        from the brace column (last calm sample before the crossing nearest the freeze)."""
        trig = self.meta.get("trigger") if isinstance(self.meta.get("trigger"), dict) else {}
        recorded = _num(trig.get("onset_t"))
        if recorded is not None:
            return {"t": self.rel(recorded), "source": "recorded", "trigger": trig.get("source")}
        data = self.emg
        if data is None or data.ndim != 2 or data.shape[1] != 4 or data.shape[0] < 2:
            return None
        t = data[:, 0] - self.t_freeze
        brace = data[:, 3]
        crossings = [i for i in range(len(t)) if brace[i] >= brace_effort and t[i] <= 0.5]
        if not crossings:
            return None
        # the last rise into a crossing at or before the freeze
        cross = crossings[0]
        for i in crossings:
            if i > 0 and brace[i - 1] < brace_effort:
                cross = i
        onset = cross
        while onset > 0 and brace[onset] > brace_calm:
            onset -= 1
        return {
            "t": round(float(t[onset]), 3),
            "cross_t": round(float(t[cross]), 3),
            "source": "derived",
            "trigger": trig.get("source"),
        }

    # joints and stages
    def joint_path(self) -> dict[str, Any]:
        ts, qs = [], []
        for row in self.meta.get("joints") or []:
            if not isinstance(row, dict):
                continue
            q = row.get("q")
            t = self.rel(row.get("t"))
            if t is None or not isinstance(q, list) or len(q) != len(JOINTS):
                continue
            vals = [_num(v) for v in q]
            if any(v is None for v in vals):
                continue
            ts.append(t)
            qs.append([round(v, 2) for v in vals])  # type: ignore[arg-type]
        return {"names": list(JOINTS), "t": ts, "q": qs, "units": "degrees; gripper % open"}

    def stages(self) -> list[dict[str, Any]]:
        """Stage changes of the plan that was running, from the saved contracts."""
        out: list[dict[str, Any]] = []
        last = None
        for row in self.meta.get("contracts") or []:
            c = row.get("contract") if isinstance(row, dict) else None
            if not isinstance(c, dict):
                continue
            key = (c.get("stage"), c.get("route"), c.get("contract_id"))
            if key != last:
                out.append({
                    "t": self.rel(row.get("t")),
                    "stage": c.get("stage"),
                    "route": c.get("route"),
                    "verb": c.get("verb"),
                    "object": c.get("object_name") or c.get("object_id"),
                    "tags": c.get("tags") or [],
                    "applied_rules": c.get("applied_rules") or [],
                })
                last = key
        return out

    # explanation and rule
    def explanation_payload(self) -> dict[str, Any]:
        stored = self.explanation
        if isinstance(stored, dict) and isinstance(stored.get("say"), str):
            source = str(stored.get("source") or "unknown")
            exp = {**stored, "source": source, "stored": True, "canned": source == "canned"}
            if source == "canned":
                exp["note"] = CANNED_NOTE
            return exp
        return canned_explanation()

    def timeline(self, zone_names: Iterable[str] = (), catalog_tags: Iterable[str] = (),
                 brace_effort: float = 0.35, brace_calm: float = 0.12) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        times = [t for t in self.joint_path()["t"]]
        if self.emg is not None and self.emg.ndim == 2 and self.emg.shape[0]:
            times += [float(self.emg[0, 0]) - self.t_freeze, float(self.emg[-1, 0]) - self.t_freeze]
        if times:
            items.append({"t": round(min(times), 3), "kind": "clip.start", "label": "clip starts"})
        for s in self.stages():
            route = f" ({s['route'] or 'direct'} route)" if s.get("verb") == "pick_place" else ""
            items.append({"t": s["t"], "kind": "stage", "label": f"{s['stage']}{route}",
                          "stage": s["stage"], "route": s["route"]})
        onset = self.brace_onset(brace_effort, brace_calm)
        if onset is not None:
            items.append({"t": onset["t"], "kind": "brace.onset",
                          "label": f"brace onset ({onset['source']})"})
        trig = self.meta.get("trigger") if isinstance(self.meta.get("trigger"), dict) else {}
        items.append({"t": 0.0, "kind": "freeze",
                      "label": f"arm frozen ({trig.get('source') or 'brace or open hand'})"})
        if times:
            items.append({"t": round(max(times), 3), "kind": "clip.end", "label": "clip ends"})
        exp = self.explanation_payload()
        items.append({"t": None, "kind": "explanation", "label": exp.get("say"),
                      "source": exp.get("source"), "canned": exp.get("canned")})
        proposal = rule_check(exp.get("rule"), zone_names, catalog_tags)
        if proposal is not None:
            r = proposal["rule"]
            items.append({"t": None, "kind": "rule.proposed",
                          "label": f"when holding {', '.join(r.get('when_holding') or [])}: "
                                   f"{r.get('response')} {r.get('avoid')}",
                          "valid": proposal["valid"]})
        return sorted(items, key=lambda e: (e["t"] is None, e["t"] if e["t"] is not None else 0))

    def summary(self) -> dict[str, Any]:
        exp = self.explanation_payload()
        return {
            "id": self.id,
            "kind": "incident",
            **source_label(self.meta),
            "synthetic": self.meta.get("synthetic") is True,
            "held": self.meta.get("held"),
            "trigger": (self.meta.get("trigger") or {}).get("source")
            if isinstance(self.meta.get("trigger"), dict) else None,
            "frames": len(self.frames()),
            "say": exp.get("say"),
            "explanation_source": exp.get("source"),
        }

    def payload(
        self,
        url_prefix: str,
        zone_names: Iterable[str] = (),
        catalog_tags: Iterable[str] = (),
        brace_effort: float = 0.35,
        brace_calm: float = 0.12,
    ) -> dict[str, Any]:
        exp = self.explanation_payload()
        zones, tags = list(zone_names), list(catalog_tags)
        return {
            **self.summary(),
            "note": self.meta.get("synthetic_note"),
            "t_freeze": 0.0,
            "objects": self.meta.get("objects") or [],
            "frames": self.frames(url_prefix),
            "joints": self.joint_path(),
            "stages": self.stages(),
            "emg": {
                "url": f"{url_prefix}/emg",
                "onset": self.brace_onset(brace_effort, brace_calm),
                "samples": 0 if self.emg is None or self.emg.ndim != 2 else int(self.emg.shape[0]),
                "error": self.emg_error,
            },
            "explanation": exp,
            "proposed_rule": rule_check(exp.get("rule"), zones, tags),
            "timeline": self.timeline(zones, tags, brace_effort, brace_calm),
        }


def load_emg(path: Path) -> tuple[np.ndarray | None, str | None]:
    if not path.exists():
        return None, "emg.npy not found"
    try:
        data = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        return None, f"emg.npy unreadable: {exc}"
    if data.size == 0:
        return np.zeros((0, 0)), None
    if data.ndim != 2 or not np.issubdtype(data.dtype, np.number):
        return None, f"emg.npy has shape {data.shape}, expected (n, 1 + channels)"
    data = data.astype(np.float64)
    if not np.isfinite(data).all():
        return None, "emg.npy has non-finite values"
    return data, None


def load_incident(path: str | Path) -> IncidentReplay:
    folder = Path(path)
    meta = _read_json(folder / "meta.json")
    if not isinstance(meta, dict) or _num(meta.get("t_freeze")) is None:
        raise ReplayError("meta.json is not an incident (no t_freeze)")
    try:
        exp = json.loads((folder / "explanation.json").read_text())
    except (OSError, ValueError):
        exp = None
    emg, err = load_emg(folder / "emg.npy")
    iid = str(meta.get("id") or folder.name)
    return IncidentReplay(iid if SAFE_ID.fullmatch(iid) else folder.name, folder, meta,
                          exp if isinstance(exp, dict) else None, emg, err)


# ── sessions ─────────────────────────────────────────────────────────────────
def _brief(event: dict[str, Any], t0: float) -> dict[str, Any]:
    """An event for the timeline: relative time, bulky fields reduced."""
    out = {k: v for k, v in event.items() if k not in ("trace", "seq", "t")}
    t = _num(event.get("t"))
    out["t"] = None if t is None else round(t - t0, 3)
    c = event.get("contract")
    if isinstance(c, dict):
        out["contract"] = {k: c.get(k) for k in ("id", "verb", "object_id", "object_name",
                                                 "route", "applied_rules", "speed")}
    d = event.get("decision")
    if isinstance(d, dict):
        out["decision"] = {k: d.get(k) for k in ("verdict", "reason", "slot_id")}
    return out


@dataclass
class SessionReplay:
    id: str
    path: Path
    meta: dict[str, Any]
    events: list[dict[str, Any]]
    trials: list[dict[str, Any]]
    truncated: bool = False
    incident_paths: list[Path] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": "session",
            **source_label(self.meta),
            "participant": self.meta.get("participant"),
            "events": len(self.events),
            "trials": len(self.trials),
            "incidents": [p.name for p in self.incident_paths],
        }

    def payload(self) -> dict[str, Any]:
        times = [t for e in self.events if (t := _num(e.get("t"))) is not None]
        t0 = min(times) if times else 0.0
        braces = []
        for e in self.events:
            if e.get("kind") != "emg.brace" or not isinstance(e.get("trace"), list):
                continue
            rows = [r for r in e["trace"] if isinstance(r, list) and len(r) == 4
                    and all(_num(v) is not None for v in r)]
            braces.append({
                "t": _brief(e, t0)["t"],
                "onset_t": None if _num(e.get("onset_t")) is None
                else round(float(e["onset_t"]) - t0, 3),
                "trace": {"t": [round(r[0] - t0, 3) for r in rows],
                          **{n: [r[i + 1] for r in rows] for i, n in enumerate(CHANNELS_3)}},
            })
        return {
            **self.summary(),
            "meta": self.meta,
            "trials": self.trials,
            "timeline": [_brief(e, t0) for e in self.events],
            "truncated": self.truncated,
            "braces": braces,
        }


def _jsonl(path: Path, limit: int) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows, False
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if len(rows) >= limit:
                    return rows, True
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a torn last line from a crash: skip it
                if isinstance(row, dict):
                    rows.append(row)
    except OSError as exc:
        raise ReplayError(f"{path.name} unreadable: {exc}") from exc
    return rows, False


def load_session(path: str | Path, root: Path | None = None) -> SessionReplay:
    folder = Path(path)
    meta = _read_json(folder / "meta.json")
    if not isinstance(meta, dict) or not (folder / "events.jsonl").exists():
        raise ReplayError("not a session folder (meta.json + events.jsonl)")
    events, truncated = _jsonl(folder / "events.jsonl", MAX_SESSION_EVENTS)
    trials, _ = _jsonl(folder / "trials.jsonl", MAX_SESSION_EVENTS)
    found: list[Path] = []
    for e in events:
        if e.get("kind") == "incident.saved" and isinstance(e.get("path"), str):
            p = Path(e["path"])
            if not p.is_absolute() and root is not None:
                p = root / p
            if (p / "meta.json").is_file() and SAFE_ID.fullmatch(p.name):
                found.append(p)
    sid = str(meta.get("session_id") or folder.name)
    return SessionReplay(sid if SAFE_ID.fullmatch(sid) else folder.name, folder, meta, events,
                         trials, truncated, found)


# ── library ──────────────────────────────────────────────────────────────────
def kind_of(path: Path) -> str | None:
    """incident, session, folder (of incidents / sessions) or None."""
    if (path / "meta.json").is_file():
        if (path / "events.jsonl").is_file():
            return "session"
        return "incident"
    if path.is_dir():
        return "folder"
    return None


class ReplayLibrary:
    """The recordings a replay server may serve, looked up by folder id only."""

    def __init__(self, items: Iterable[Path] = (), scan_dirs: Iterable[Path] = (),
                 root: Path | None = None) -> None:
        self.items = [Path(p) for p in items]
        self.scan_dirs = [Path(p) for p in scan_dirs]
        self.root = root

    @classmethod
    def from_path(cls, path: str | Path, root: Path | None = None) -> ReplayLibrary:
        p = Path(path)
        kind = kind_of(p)
        if kind is None:
            raise ReplayError(f"{p} is not an incident, a session or a folder of them")
        if kind == "folder":
            return cls(scan_dirs=[p], root=root)
        if kind == "session":
            session = load_session(p, root)
            return cls([p, *session.incident_paths], root=root)
        load_incident(p)  # fail now, not on the first request
        return cls([p], root=root)

    def _all(self) -> list[Path]:
        paths = list(self.items)
        for d in self.scan_dirs:
            try:
                children = sorted(
                    (c for c in d.iterdir() if c.is_dir() and (c / "meta.json").is_file()),
                    key=lambda c: c.name,
                )
            except OSError:
                continue
            paths += children[-MAX_LIBRARY:]
        return paths

    def _index(self, kind: str) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for p in self._all():
            if SAFE_ID.fullmatch(p.name) and kind_of(p) == kind:
                out.setdefault(p.name, p)
        return out

    def incident_ids(self) -> dict[str, Path]:
        return self._index("incident")

    def session_ids(self) -> dict[str, Path]:
        return self._index("session")

    def incident(self, iid: str) -> IncidentReplay:
        path = self.incident_ids().get(iid) if SAFE_ID.fullmatch(iid) else None
        if path is None:
            raise KeyError(iid)
        return load_incident(path)

    def session(self, sid: str) -> SessionReplay:
        path = self.session_ids().get(sid) if SAFE_ID.fullmatch(sid) else None
        if path is None:
            raise KeyError(sid)
        return load_session(path, self.root)

    def summaries(self) -> dict[str, list[dict[str, Any]]]:
        incidents, sessions = [], []
        for iid, path in self.incident_ids().items():
            try:
                incidents.append(load_incident(path).summary())
            except ReplayError as exc:
                incidents.append({"id": iid, "kind": "incident", "error": str(exc)})
        for sid, path in self.session_ids().items():
            try:
                sessions.append(load_session(path, self.root).summary())
            except ReplayError as exc:
                sessions.append({"id": sid, "kind": "session", "error": str(exc)})
        return {"incidents": incidents, "sessions": sessions}
