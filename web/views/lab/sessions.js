// Stage 3: record sessions, label trials, tally outcomes. Counts come from state.session only.

const OUTCOMES = [
  ["success", "Success", "#5af0a4"],
  ["wrong_object", "Wrong object", "#ffc15a"],
  ["wrong_action", "Wrong action", "#ff9a5a"],
  ["no_action", "No action", "#7f8da1"],
  ["dropped", "Dropped", "#ff8fa0"],
  ["crushed", "Crushed", "#ff4d5e"],
];
const EXTRA = { abandoned: ["Abandoned", "#46526a"] };
const meta = (k) => {
  const f = OUTCOMES.find((o) => o[0] === k);
  return f ? { label: f[1], color: f[2] } : { label: EXTRA[k]?.[0] || k.replaceAll("_", " "), color: EXTRA[k]?.[1] || "#46526a" };
};
const GAP = 1.2; // percent of the ring left between segments
const clock = (s) => {
  if (s == null) return "00:00";
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};

export function createSessions(pane, ctx) {
  const { api, esc, words, countTo } = ctx;

  pane.innerHTML = `
    <div class="ses">
      <div class="ses-col">
        <section class="panel rec-card" data-ref="reccard">
          <div class="panel-head"><h3>Session</h3><span class="rec-ind" data-ref="ind"><i></i><b data-ref="indtext">not recording</b></span></div>
          <form class="rec-form" data-ref="sform" autocomplete="off">
            <div class="clockrow">
              <div class="clock num" data-ref="clock">00:00</div>
              <div class="sid mono" data-ref="sid">no session</div>
            </div>
            <div class="inrow">
              <input class="field" data-ref="part" placeholder="Participant id, e.g. p01" maxlength="40" aria-label="Participant id" />
              <input class="field" data-ref="notes" placeholder="Notes (optional)" maxlength="200" aria-label="Session notes" />
            </div>
            <button class="btn primary big" data-ref="sbtn" type="submit">Start session</button>
          </form>
        </section>

        <section class="panel trial-card">
          <div class="panel-head"><h3>Trial</h3><span class="kicker" data-ref="trialn">—</span></div>
          <form class="trial-form" data-ref="tform" autocomplete="off">
            <label>Expected action
              <select class="field" data-ref="tact">
                <option value="pick_place">pick &amp; place</option><option value="point">point</option><option value="home">home</option><option value="none">no action (distractor)</option>
              </select></label>
            <label>Expected object <select class="field" data-ref="tobj"></select></label>
            <label>Condition
              <select class="field" data-ref="tcond">
                <option>speech</option><option>speech+point</option><option>speech+point+emg</option><option>point+emg</option><option>noise</option>
              </select></label>
            <label>Instruction <input class="field" data-ref="tins" placeholder="What the participant was asked" maxlength="200" /></label>
            <button class="btn primary" data-ref="tbtn" type="submit">Start trial</button>
          </form>
          <p class="fine muted" data-ref="thint"></p>
        </section>

        <section class="panel outc">
          <div class="panel-head"><h3>What happened?</h3><span class="kicker" data-ref="ohint">no trial running</span></div>
          <div class="ogrid" data-ref="ogrid">${OUTCOMES.map(
            ([k, label, c]) => `<button class="obtn" data-o="${k}" style="--c:${c}"><i></i><b>${label}</b></button>`,
          ).join("")}</div>
        </section>
      </div>

      <div class="ses-col">
        <section class="panel tally">
          <div class="panel-head"><h3>This session</h3><span class="kicker" data-ref="tallyn">no trials yet</span></div>
          <div class="tally-body">
            <div class="donut" data-ref="donut">
              <svg viewBox="0 0 200 200" aria-hidden="true"><circle class="base" cx="100" cy="100" r="82" pathLength="100"/><g data-ref="segs"></g></svg>
              <div class="donut-mid"><b class="num" data-ref="rate">—</b><small data-ref="ratecap">success rate</small></div>
            </div>
            <ul class="legend" data-ref="legend"></ul>
          </div>
          <div class="seq" data-ref="seq" aria-label="Outcomes in order, oldest first"></div>
          <div class="cur" data-ref="cur"></div>
        </section>

        <section class="panel flinch">
          <div class="panel-head"><h3>Flinch test (gate G1)</h3><span class="chip" data-ref="fchip"><i></i>checking…</span></div>
          <div class="flinch-body" data-ref="fbody"></div>
        </section>

        <p class="bench-note">Full report (wrong-action rate, ask rate, latency, grip outcomes per condition): run <code>h2m bench</code>. Sessions are written to <code>data/sessions/&lt;session id&gt;/</code>.</p>
      </div>
    </div>`;
  const q = {};
  pane.querySelectorAll("[data-ref]").forEach((el) => (q[el.dataset.ref] = el));
  const obtns = [...pane.querySelectorAll(".obtn")];

  let state = null;
  let objKey = "";
  let segEls = new Map();
  let lastTally = "";
  let flinchAt = -Infinity;
  let pending = false;

  // ── actions ────────────────────────────────────────────────────────────────
  q.sform.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (pending) return;
    pending = true;
    q.sbtn.disabled = true;
    if (state?.session?.session_id) await api("/api/session/stop");
    else await api("/api/session/start", { participant: q.part.value.trim() || "anon", notes: q.notes.value.trim() });
    pending = false;
  });
  q.tform.addEventListener("submit", (e) => {
    e.preventDefault();
    api("/api/trial/start", {
      expected_action: q.tact.value,
      expected_object: q.tobj.value || null,
      condition: q.tcond.value,
      instruction: q.tins.value,
    });
  });
  q.ogrid.addEventListener("click", async (e) => {
    const b = e.target.closest(".obtn");
    if (!b || b.disabled) return;
    b.classList.remove("pop");
    void b.offsetWidth;
    b.classList.add("pop");
    await api("/api/trial/end", { outcome: b.dataset.o });
  });

  // ── tally ──────────────────────────────────────────────────────────────────
  function segFor(k) {
    if (!segEls.has(k)) {
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("cx", 100);
      c.setAttribute("cy", 100);
      c.setAttribute("r", 82);
      c.setAttribute("pathLength", 100);
      c.setAttribute("class", "seg");
      c.style.stroke = meta(k).color;
      c.style.strokeDasharray = "0 100";
      q.segs.append(c);
      segEls.set(k, c);
    }
    return segEls.get(k);
  }

  function renderTally(sess) {
    const outs = sess.outcomes || [];
    const counts = {};
    outs.forEach((o) => (counts[o] = (counts[o] || 0) + 1));
    const total = outs.length;
    const keys = [...OUTCOMES.map((o) => o[0]), ...Object.keys(counts).filter((k) => !OUTCOMES.some((o) => o[0] === k))];
    const sig = JSON.stringify([counts, sess.trials_done, total, !!sess.session_id]);
    if (sig === lastTally) return;
    lastTally = sig;
    let start = 0;
    const gap = Object.keys(counts).length > 1 ? GAP : 0;
    for (const k of keys) {
      const n = counts[k] || 0;
      const seg = segFor(k);
      const len = total ? (n / total) * 100 : 0;
      const draw = n ? Math.max(0.1, len - gap) : 0;
      seg.style.strokeDasharray = `${draw} ${100 - draw}`;
      seg.style.strokeDashoffset = String(-start);
      start += len;
    }
    q.donut.dataset.empty = total ? "0" : "1";
    if (total) {
      const rate = ((counts.success || 0) / total) * 100;
      countTo(q.rate, rate, { fmt: (v) => `${Math.round(v)}%`, ms: 700 });
      q.ratecap.textContent = `${counts.success || 0} of ${total} succeeded`;
    } else {
      q.rate.textContent = "—";
      q.rate.dataset.v = 0;
      q.ratecap.textContent = "no trials ended yet";
    }
    const ended = state && !state.session.session_id ? " · session ended" : "";
    q.tallyn.textContent = (!total ? "no trials yet" : sess.trials_done > total ? `last ${total} of ${sess.trials_done} trials` : `${total} trial${total > 1 ? "s" : ""}`) + (total ? ended : "");
    q.legend.innerHTML = keys
      .filter((k) => OUTCOMES.some((o) => o[0] === k) || counts[k])
      .map((k) => `<li data-zero="${counts[k] ? 0 : 1}" style="--c:${meta(k).color}"><i></i><span>${esc(meta(k).label)}</span><b class="num">${counts[k] || 0}</b><em class="num">${total && counts[k] ? Math.round((counts[k] / total) * 100) + "%" : ""}</em></li>`)
      .join("");
    q.seq.innerHTML = Array.from({ length: 20 }, (_, i) => {
      const o = outs[i];
      return o ? `<i style="background:${meta(o).color}" title="Trial ${sess.trials_done - total + i + 1}: ${esc(meta(o).label)}"></i>` : "<i class='empty'></i>";
    }).join("");
  }

  // ── flinch results (read-only; needs GET /api/flinch) ─────────────────────
  async function loadFlinch(force) {
    if (!force && performance.now() - flinchAt < 20000) return;
    flinchAt = performance.now();
    let data = null;
    try {
      const res = await fetch("/api/flinch", { headers: { Accept: "application/json" } });
      if (res.ok) data = await res.json();
    } catch {
      data = null;
    }
    const run = data?.runs?.[0] || (data?.run ? data : null);
    if (!run) {
      q.fchip.className = "chip";
      q.fchip.innerHTML = "<i></i>not readable";
      q.fbody.innerHTML = `<p class="empty-t">The runtime has no endpoint that serves flinch-test results yet, so none can be shown here.</p>
        <p class="fine muted">Record and score a run from the terminal. Results are written to <code>data/flinch/&lt;run&gt;/report.md</code>:</p>
        <ul class="cmds"><li><code>h2m flinch record</code></li><li><code>h2m flinch analyse</code></li></ul>`;
      return;
    }
    const verdict = String(run.verdict || "").toUpperCase();
    q.fchip.className = `chip ${verdict === "PASS" ? "ok" : verdict === "FAIL" ? "bad" : ""}`;
    q.fchip.innerHTML = `<i></i>${esc(verdict || "scored")}`;
    q.fbody.innerHTML = `<div class="frun"><b>${esc(run.run || run.name || "latest run")}</b>${run.simulated ? '<span class="tag">SIMULATED DATA</span>' : ""}</div>
      <dl class="kv">${run.participant ? `<dt>participant</dt><dd>${esc(run.participant)}</dd>` : ""}${run.stimuli != null ? `<dt>stimuli</dt><dd class="num">${esc(run.stimuli)}</dd>` : ""}${run.minutes != null ? `<dt>recording</dt><dd class="num">${esc(run.minutes)} min</dd>` : ""}</dl>
      ${run.report ? `<pre class="freport">${esc(run.report)}</pre>` : ""}`;
  }

  // ── render ─────────────────────────────────────────────────────────────────
  function render(s) {
    state = s;
    const sess = s.session;
    const rec = !!sess.session_id;
    q.reccard.dataset.rec = rec ? "1" : "0";
    q.indtext.textContent = rec ? "REC" : "not recording";
    q.clock.textContent = clock(sess.elapsed_s);
    q.sid.textContent = rec ? sess.session_id : "no session";
    q.sbtn.textContent = rec ? "Stop session" : "Start session";
    q.sbtn.classList.toggle("danger", rec);
    q.sbtn.classList.toggle("primary", !rec);
    if (!pending) q.sbtn.disabled = false;
    q.part.disabled = rec;
    q.notes.disabled = rec;

    const key = s.objects.map((o) => o.id).join("|");
    if (key !== objKey) {
      objKey = key;
      const keep = q.tobj.value;
      q.tobj.innerHTML = `<option value="">(none)</option>` + s.objects.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("");
      q.tobj.value = keep;
    }
    const t = sess.trial;
    q.tbtn.disabled = !rec;
    q.tbtn.textContent = t ? "Abandon and start new trial" : "Start trial";
    q.thint.textContent = !rec ? "Start a session first; trials are recorded into it." : t ? `Trial #${t.trial} is still open. Starting another marks it abandoned.` : "Starting a trial stamps the moment so the bench can score the run that follows.";
    q.trialn.textContent = t ? `#${t.trial} running` : rec ? `next #${sess.trials_done + 1}` : "—";
    obtns.forEach((b) => (b.disabled = !t));
    q.ohint.textContent = t ? `trial #${t.trial}` : "no trial running";
    q.ogrid.dataset.live = t ? "1" : "0";

    renderTally(sess);
    if (t) {
      q.cur.dataset.on = "1";
      q.cur.innerHTML = `<span class="pulse"></span><div><b>Trial #${esc(t.trial)} awaiting an outcome</b><small>expect ${esc(words(t.expected_action))}${t.expected_object ? " · " + esc(s.objects.find((o) => o.id === t.expected_object)?.name || t.expected_object) : ""} · ${esc(t.condition || "no condition")}${t.instruction ? ` · “${esc(t.instruction)}”` : ""}</small></div>`;
    } else {
      q.cur.dataset.on = "0";
      q.cur.innerHTML = `<div><b>No trial running</b><small>${rec ? "Start a trial, run the task, then press the outcome." : "Start a session to begin recording."}</small></div>`;
    }
  }

  return {
    activate() {
      loadFlinch(false);
    },
    deactivate() {},
    update: render,
    dispose() {},
  };
}
