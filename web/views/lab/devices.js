// Stage 2: the bench as a constellation. Every status line is a field from the snapshot.

const POS = {
  llm: [50, 11],
  vision: [79, 36],
  arm: [71, 86],
  emg: [29, 86],
  speech: [21, 36],
};
const HUB = [50, 53];

export function createDevices(pane, ctx) {
  const { esc, pct, loop, reduced } = ctx;

  pane.innerHTML = `
    <div class="dev">
      <div class="dev-map panel" data-ref="map">
        <svg class="links" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" data-ref="links"></svg>
        <div class="packets" data-ref="packets"></div>
        <div class="hub" data-ref="hub" style="left:${HUB[0]}%;top:${HUB[1]}%">
          <span class="hub-ring"></span><span class="hub-ring r2"></span>
          <div class="hub-core"><b>Armed</b><small data-ref="hubmeta">runtime</small></div>
        </div>
        <div data-ref="nodes"></div>
      </div>
      <div class="dev-side">
        <section class="panel poses">
          <div class="panel-head"><h3>Arm poses</h3><span class="chip" data-ref="posechip"><i></i>—</span></div>
          <div data-ref="poses"></div>
        </section>
        <section class="panel ready">
          <div class="panel-head"><h3>Bench readiness</h3><span class="kicker" data-ref="readyn"></span></div>
          <ul class="checks" data-ref="checks"></ul>
        </section>
        <section class="panel objs">
          <div class="panel-head"><h3>Objects · max grip</h3><span class="kicker" data-ref="objn"></span></div>
          <ul class="grips" data-ref="objs"></ul>
          <p class="muted fine">Bar = the highest gripper effort the safety gate will allow for that object (0–100%).</p>
        </section>
      </div>
    </div>`;
  const q = {};
  pane.querySelectorAll("[data-ref]").forEach((el) => (q[el.dataset.ref] = el));

  const ORDER = ["llm", "vision", "arm", "emg", "speech"];
  const nodeEls = {};
  q.links.innerHTML = ORDER.map((k) => `<line data-l="${k}" x1="${HUB[0]}" y1="${HUB[1]}" x2="${POS[k][0]}" y2="${POS[k][1]}"/>`).join("");
  q.nodes.innerHTML = ORDER.map(
    (k) => `<article class="node" data-k="${k}" style="left:${POS[k][0]}%;top:${POS[k][1]}%">
      <header><i class="dot"></i><h2 data-f="name"></h2><span class="tag" data-f="tag" hidden></span></header>
      <div class="head" data-f="head"></div>
      <dl class="kv" data-f="rows"></dl>
      <p class="err" data-f="err" hidden></p>
    </article>`,
  ).join("");
  for (const el of q.nodes.querySelectorAll(".node")) nodeEls[el.dataset.k] = el;

  // packets: two per link, positions driven by the shared loop
  const packets = {};
  for (const k of ORDER) {
    packets[k] = [0, 0.5].map((phase) => {
      const el = document.createElement("i");
      el.className = "pk";
      q.packets.append(el);
      return { el, phase };
    });
  }
  const live = {}; // k -> { on, tone, dir }

  function info(s) {
    const a = s.arm;
    const e = s.emg;
    const v = s.vision;
    const sp = s.speech;
    const l = s.llm;
    const armTone = { ready: "ok", moving: "ok", disarmed: "warn", stopped: "bad", fault: "bad" }[a.state] || "";
    const out = {};
    out.arm = {
      name: "SO-101 arm", tone: armTone, on: a.state === "ready" || a.state === "moving", dir: -1,
      tags: a.hardware ? [] : ["VIRTUAL ARM"],
      head: a.state,
      rows: [["backend", a.backend], ["hardware", a.hardware ? "yes" : "no"], ["task", a.skill ? `${a.skill} · ${a.stage}` : "idle"]],
      err: a.state === "fault" || a.state === "stopped" ? a.reason : "",
    };
    out.emg = e
      ? {
          name: "Ganglion EMG", tone: e.error ? "bad" : !e.live ? "warn" : e.hardware ? "ok" : "warn", on: !!e.live && !e.error, dir: 1,
          tags: e.source === "synthetic" ? ["SYNTHETIC EMG"] : [],
          head: e.live ? `live · ${e.age_ms} ms` : e.age_ms == null ? "no samples yet" : `stalled · ${e.age_ms} ms`,
          rows: [["source", e.source], ["profile", e.calibrated ? e.profile : "not calibrated"], ["brace scale", e.brace_calibrated ? "calibrated" : "none"]],
          err: e.error || "",
        }
      : { name: "Ganglion EMG", tone: "off", on: false, dir: 1, tags: [], head: "off in this build", rows: [["start with", "--emg ganglion"]], err: "" };
    out.vision = {
      name: "Overhead camera", tone: v.virtual ? "warn" : v.live ? "ok" : "bad", on: !!v.live && !v.virtual, dir: 1,
      tags: v.virtual ? ["VIRTUAL TABLE"] : [],
      head: v.virtual ? "no camera attached" : v.live ? `live · ${v.fps ?? "—"} fps` : "no frames",
      rows: [["detector", v.detector || "none"], ["hand tracking", v.hand_tracking ? "on" : "off"], ["objects seen", String((v.objects || []).length)]],
      err: (v.errors || []).join(" · "),
    };
    out.speech = sp
      ? {
          name: "Mic · ASR", tone: sp.error ? "bad" : sp.listening ? "ok" : "warn", on: !!sp.listening && !sp.error, dir: 1, tags: [],
          head: sp.error ? "mic error" : sp.listening ? "listening" : sp.asr ? "not listening" : "ASR loading",
          rows: [["asr", sp.asr || "loading"], ["listening", sp.listening ? "yes" : "no"], ["speaking", sp.speaking ? "yes" : "no"]],
          err: sp.error || "",
        }
      : { name: "Mic · ASR", tone: "off", on: false, dir: 1, tags: [], head: "off · typed input only", rows: [["start with", "speech on"]], err: "" };
    out.llm = {
      name: "Muse", tone: l.error ? "bad" : l.enabled ? "muse" : "off", on: l.enabled && !l.error, dir: 1, tags: [],
      head: l.enabled ? (l.last_ms ? `last call ${l.last_ms} ms` : "ready") : "grammar only",
      rows: l.enabled ? [["model", l.model || "—"], ["last call", l.last_ms ? `${l.last_ms} ms` : "none yet"]] : [["interpreter", "rule grammar"], ["to enable", "set MODEL_API_KEY"]],
      err: l.error || "",
    };
    return out;
  }

  function render(s) {
    const data = info(s);
    for (const k of ORDER) {
      const d = data[k];
      const el = nodeEls[k];
      el.dataset.tone = d.tone || "off";
      const set = (f, html) => {
        const t = el.querySelector(`[data-f="${f}"]`);
        if (t.dataset.h !== html) {
          t.innerHTML = html;
          t.dataset.h = html;
        }
      };
      set("name", esc(d.name));
      set("head", esc(d.head));
      set("rows", d.rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join(""));
      const tag = el.querySelector('[data-f="tag"]');
      tag.hidden = !d.tags.length;
      tag.textContent = d.tags.join(" ");
      const err = el.querySelector('[data-f="err"]');
      err.hidden = !d.err;
      err.textContent = d.err;
      live[k] = { on: d.on, tone: d.tone, dir: d.dir };
      q.links.querySelector(`[data-l="${k}"]`).dataset.state = d.on ? "on" : d.err ? "bad" : "off";
      q.links.querySelector(`[data-l="${k}"]`).dataset.tone = d.tone || "off";
    }
    const counts = ORDER.reduce((a, k) => ((a[data[k].tone === "bad" ? "bad" : data[k].on ? "on" : "idle"] += 1), a), { on: 0, bad: 0, idle: 0 });
    q.hubmeta.textContent = `${counts.on} live${counts.bad ? ` · ${counts.bad} error${counts.bad > 1 ? "s" : ""}` : ""}`;
    q.hub.dataset.tone = counts.bad ? "bad" : counts.on ? "ok" : "off";

    // poses
    const a = s.arm;
    const cmd = (p) => `armed arm teach ${p}`;
    if (a.hardware) {
      q.posechip.className = `chip ${a.untaught.length ? "warn" : "ok"}`;
      q.posechip.innerHTML = `<i></i>${a.untaught.length ? `${a.untaught.length} untaught` : "all taught"}`;
      q.poses.innerHTML = a.untaught.length
        ? `<p class="fine">The real arm refuses any plan that uses an untaught pose. Hand-guide the follower to the pose, then run:</p>
           <ul class="cmds">${a.untaught.map((p) => `<li><code>${esc(cmd(p))}</code><button class="btn copy" data-copy="${esc(cmd(p))}">Copy</button></li>`).join("")}</ul>`
        : `<p class="fine">Every pose the library lists is taught. Re-teach one with:</p><ul class="cmds"><li><code>armed arm teach &lt;pose&gt;</code><button class="btn copy" data-copy="armed arm teach ">Copy</button></li></ul>`;
    } else {
      q.posechip.className = "chip warn";
      q.posechip.innerHTML = "<i></i>virtual arm";
      q.poses.innerHTML = `<p class="fine">The virtual arm runs the placeholder poses as they are. Teaching applies to the SO-101 only:</p>
        <ul class="cmds"><li><code>armed arm teach &lt;pose&gt;</code><button class="btn copy" data-copy="armed arm teach ">Copy</button></li></ul>
        <p class="muted fine">The runtime reports untaught poses only when a real arm is attached.</p>`;
    }

    // readiness: each row is one snapshot field
    const row = (tone, label, value) => ({ tone, label, value });
    const checks = [
      row(a.hardware ? "ok" : "warn", "Arm hardware", a.hardware ? `SO-101 · ${a.backend}` : "virtual arm"),
      row(a.state === "ready" || a.state === "moving" ? "ok" : a.state === "disarmed" ? "warn" : "bad", "Arm armed", a.state),
      s.emg
        ? row(s.emg.error ? "bad" : s.emg.live && s.emg.hardware ? "ok" : "warn", "EMG signal", s.emg.error ? "error" : s.emg.live ? (s.emg.hardware ? "live" : "synthetic") : "no data")
        : row("off", "EMG signal", "off"),
      s.emg ? row(s.emg.calibrated ? "ok" : "warn", "EMG profile", s.emg.calibrated ? s.emg.profile : "not calibrated") : row("off", "EMG profile", "—"),
      s.emg ? row(s.emg.brace_calibrated ? "ok" : "warn", "Brace scale", s.emg.brace_calibrated ? "calibrated" : "not recorded") : row("off", "Brace scale", "—"),
      row(s.vision.virtual ? "warn" : s.vision.live ? "ok" : "bad", "Camera", s.vision.virtual ? "virtual table" : s.vision.live ? "live" : "no frames"),
      s.speech ? row(s.speech.error ? "bad" : s.speech.listening ? "ok" : "warn", "Microphone", s.speech.error ? "error" : s.speech.listening ? "listening" : "not listening") : row("off", "Microphone", "off"),
      row(s.llm.error ? "bad" : s.llm.enabled ? "ok" : "off", "Muse", s.llm.error ? "error" : s.llm.enabled ? "ready" : "grammar only"),
    ];
    q.checks.innerHTML = checks.map((c) => `<li data-tone="${c.tone}"><i></i><span>${esc(c.label)}</span><b>${esc(c.value)}</b></li>`).join("");
    const good = checks.filter((c) => c.tone === "ok").length;
    q.readyn.textContent = `${good} of ${checks.length} ready`;

    // objects
    q.objn.textContent = `${s.objects.length} known`;
    q.objs.innerHTML = s.objects.length
      ? s.objects.map((o) => `<li><span>${esc(o.name)}</span><div class="gbar"><i style="width:${Math.round(o.max_grip * 100)}%"></i></div><b class="num">${pct(o.max_grip)}</b></li>`).join("")
      : '<li class="muted">No objects known to the runtime.</li>';
  }

  pane.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      b.textContent = "Copied";
    } catch {
      b.textContent = "Select + copy";
    }
    setTimeout(() => (b.textContent = "Copy"), 1400);
  });

  let removeLoop = null;
  function onFrame(dt) {
    for (const k of ORDER) {
      const st = live[k];
      const line = POS[k];
      for (const p of packets[k]) {
        if (!st?.on || reduced) {
          p.el.style.opacity = 0;
          continue;
        }
        p.phase = (p.phase + dt * 0.32) % 1;
        const f = st.dir > 0 ? 1 - p.phase : p.phase; // sensors flow to the hub, the arm is fed by it
        const x = HUB[0] + (line[0] - HUB[0]) * f;
        const y = HUB[1] + (line[1] - HUB[1]) * f;
        p.el.style.left = `${x}%`;
        p.el.style.top = `${y}%`;
        p.el.style.opacity = String(Math.sin(p.phase * Math.PI));
        p.el.dataset.tone = st.tone;
      }
    }
  }

  return {
    activate() {
      removeLoop ||= loop.add(onFrame);
    },
    deactivate() {
      removeLoop?.();
      removeLoop = null;
    },
    update: render,
    dispose() {
      this.deactivate();
    },
  };
}
