// Muse Wonder's explanation (faithful to explanation.json) and the rule proposal card.
import { esc, words } from "/web/js/core.js";
import { respInfo, ruleSentence, shortId } from "./data.js";

const MARK = `<svg class="muse-mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="12.5" fill="none" stroke="currentColor" stroke-width="1.6" opacity=".45"/><path d="M7 22l4.5-12 4.5 8 4.5-8L25 22" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="16" cy="6.2" r="1.7" fill="currentColor"/></svg>`;
const chip = (k, v, tone = "") => `<span class="kvchip ${tone}"><b>${esc(k)}</b>${esc(v)}</span>`;

const ROUTE_CHANGE = {
  route_around: (r) => `avoids ${r.avoid}`,
  lift_higher: () => "high routes only",
  slow_down: () => "speed capped",
  grip_gentler: () => "grip capped",
};

export class MuseCard {
  constructor(host, ctx) {
    this.host = host;
    this.ctx = ctx;
    this.key = "";
    this.typing = 0;
    host.innerHTML = `<div class="panel muse">
      <div class="muse-head"><span class="muse-id">${MARK}<span class="muse-name">Muse Wonder</span></span><span class="muse-badge"></span></div>
      <div class="muse-body"></div></div>`;
    this.badge = host.querySelector(".muse-badge");
    this.nameEl = host.querySelector(".muse-name");
    this.replay = ctx.mode === "replay";
    this.label = null; // state.label: "SIMULATION" | "REPLAY"
    this.noSim = false;
    this.body = host.querySelector(".muse-body");
    this.panel = host.firstElementChild;
  }

  /** status = state.learning.incident; details = GET /api/incident/<id>; learning = state.learning */
  render(status, details, learning) {
    const exp = details?.explanation || status?.explanation || null;
    const problems = details?.explanation?.problems || status?.problems || [];
    const canned = exp?.source === "canned"; // the simulation's scripted explanation: not a model
    const stateKey = (status ? `${status.id}|${status.state}|${exp?.say || ""}|${(problems || []).join()}|${learning.muse}` : "none") + `|${this.label}|${this.noSim}`;
    if (stateKey === this.key) return;
    this.key = stateKey;
    cancelAnimationFrame(this.typing);
    this.panel.classList.toggle("scan", !!status && status.state !== "explained");
    const sim = this.replay && this.label === "SIMULATION";
    const name = canned || (!status && sim) ? "Canned explanation" : "Muse Wonder";
    if (this.nameEl.textContent !== name) this.nameEl.textContent = name;

    if (!status && this.replay) {
      this.badge.className = "muse-badge";
      this.badge.textContent = this.noSim ? "no simulation" : sim ? "simulation" : this.label || "replay";
      this.body.innerHTML = this.noSim
        ? `<p class="muse-idle">No simulation running. Open Replay to read the explanation saved with each recorded incident.</p>`
        : `<p class="muse-idle">The scripted brace comes first; then the simulation shows its canned explanation here.</p>
          <p class="muse-note muted">No model and no network are used in the simulation.</p>`;
      return;
    }
    if (!status) {
      this.badge.className = "muse-badge";
      this.badge.textContent = learning.muse ? "ready" : "not configured";
      this.body.innerHTML = `<p class="muse-idle">When you brace, Muse Wonder reviews the seconds before it and says out loud what it thinks bothered you.</p>
        <p class="muse-note muted">${learning.muse ? "Muse Wonder is configured." : "Muse Wonder is not configured here (no MODEL_API_KEY). A brace will get a local fallback question instead, and no rule is proposed."}</p>`;
      return;
    }
    if (status.state === "capturing" || status.state === "explaining" || !exp) {
      this.badge.className = "muse-badge live";
      this.badge.textContent = status.state === "capturing" ? "recording the after-window" : learning.muse ? "reviewing the clip" : "no model: fallback";
      this.body.innerHTML = `<div class="muse-wait"><div class="scanbars">${Array.from({ length: 9 }, (_, i) => `<i style="--i:${i}"></i>`).join("")}</div>
        <p>${status.state === "capturing" ? "Frozen. Keeping one more second of the clip before it is saved." : "Muse is reading the seconds before the brace: the joints, the held object, what was nearby."}</p></div>`;
      return;
    }

    const fb = exp.source === "fallback";
    this.badge.className = `muse-badge ${fb || canned ? "fallback" : "ok"}`;
    this.badge.textContent = fb ? "local fallback, not Muse" : canned ? "canned, not a model" : "Muse Wonder";
    const r = exp.rule;
    const say = status.say || exp.say || "";
    let h = `<blockquote class="muse-say"><span class="say-full">${esc(say)}</span><span class="say-t" aria-hidden="true"></span><i class="say-bars" aria-hidden="true"><s></s><s></s><s></s><s></s></i></blockquote>`;
    if (fb) {
      h += `<p class="muse-note">${learning.muse ? "Muse did not return a usable explanation (error, malformed, or low confidence)." : "Muse is not configured here (no MODEL_API_KEY)."} The arm asks an open question instead and proposes no rule. It stays stopped either way.</p>`;
    } else {
      h += `<dl class="muse-facts">
        <div><dt>Blamed</dt><dd>${esc(exp.cause_object || "—")}</dd></div>
        <div><dt>Relation</dt><dd>${esc(words(exp.relation))}</dd></div>
        <div><dt>Held property</dt><dd>${esc(words(exp.held_property))}</dd></div>
        <div class="conf"><dt>Confidence</dt><dd><span class="num">${exp.confidence.toFixed(2)}</span><i class="confbar"><b style="width:${Math.round(exp.confidence * 100)}%"></b></i></dd></div>
      </dl>`;
      if (exp.mask_source) h += `<p class="muse-note mono">${exp.mask_source === "sam" ? "SAM 3.1 mask" : "detector box"} ${exp.mask_box ? exp.mask_box.map((v) => Math.round(v)).join(", ") : ""}</p>`;
      if (r) {
        const info = respInfo(r);
        h += `<div class="muse-rule"><span class="kicker">Candidate rule</span>
          <p class="rule-s">${esc(ruleSentence(r))}</p>
          <div class="chips">${chip("kind", r.response, "muse")}${r.when_holding.map((t) => chip("object tag", t)).join("")}${chip("zone", r.avoid, "brace")}${chip("route change", (ROUTE_CHANGE[r.response] || (() => info.kind))(r))}</div></div>`;
      }
    }
    if (problems?.length) h += `<ul class="muse-problems">${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
    this.body.innerHTML = h;
    const el = this.body.querySelector(".say-t");
    if (el) this.type(el, say);
  }

  type(el, text) {
    const bars = this.body.querySelector(".say-bars");
    if (this.ctx.reduced || this.seen === text) {
      el.textContent = text;
      bars?.classList.remove("on");
      return;
    }
    this.seen = text;
    const t0 = performance.now();
    bars?.classList.add("on");
    const step = (now) => {
      const n = Math.min(text.length, Math.floor((now - t0) / 22));
      el.textContent = text.slice(0, n);
      if (n < text.length) this.typing = requestAnimationFrame(step);
      else bars?.classList.remove("on");
    };
    this.typing = requestAnimationFrame(step);
  }
}

// ── proposal ────────────────────────────────────────────────────────────────
const RING_R = 30;
const RING_C = 2 * Math.PI * RING_R;

export class ProposalCard {
  constructor(host, { onApprove, onReject, onSeeRule, readOnly = false }) {
    this.host = host;
    this.ro = readOnly;
    this.cb = { onApprove, onReject, onSeeRule };
    host.innerHTML = `<div class="panel prop"></div>`;
    this.panel = host.firstElementChild;
    this.id = null;
    this.deadline = 0;
    this.total = 1;
    this.shownAt = 0;
    this.sig = "";
    this.panel.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b || b.disabled) return;
      const id = b.dataset.id;
      if (b.dataset.act === "approve") this.cb.onApprove(id, b);
      else if (b.dataset.act === "reject") this.cb.onReject(id, b);
      else if (b.dataset.act === "see") this.cb.onSeeRule();
    });
  }

  /**
   * proposal: state.learning.proposal | null
   * outcome: {kind: idle|waiting|saved|rejected|expired|none|lost, rule?, by?}
   */
  render(proposal, outcome) {
    const sig = (proposal ? `p|${proposal.rule.id}` : `o|${outcome.kind}|${outcome.rule?.id || ""}|${outcome.by || ""}`) + (this.noSim ? "|ns" : "");
    if (proposal) {
      this.deadline = performance.now() / 1000 + proposal.expires_in_s;
      if (this.id !== proposal.rule.id) {
        this.id = proposal.rule.id;
        this.total = Math.max(proposal.expires_in_s, 1);
        this.shownAt = performance.now();
      }
    } else this.id = null;
    if (sig === this.sig) return;
    this.sig = sig;
    this.panel.classList.toggle("hot", !!proposal);
    this.panel.classList.toggle("done", !proposal && outcome.kind === "saved");
    if (proposal) {
      const r = proposal.rule;
      this.panel.innerHTML = `<div class="prop-top">
          <svg class="ring" viewBox="0 0 72 72" aria-hidden="true"><circle cx="36" cy="36" r="${RING_R}" class="ring-bg"/><circle cx="36" cy="36" r="${RING_R}" class="ring-fg" stroke-dasharray="${RING_C}" stroke-dashoffset="0" transform="rotate(-90 36 36)"/></svg>
          <div class="ring-n"><span class="num">0</span><small>s left</small></div>
          <div class="prop-q"><span class="kicker">${this.ro ? "Proposed rule" : "Waiting for you"}</span><p>${esc(ruleSentence(r))}</p></div>
        </div>
        ${this.ro
          ? `<p class="prop-hint ro">Approval is disabled in replay; the scripted scenario approves it.</p>`
          : `<div class="prop-actions">
          <button class="btn ok big" data-act="approve" data-id="${esc(r.id)}">Approve rule</button>
          <button class="btn danger big" data-act="reject" data-id="${esc(r.id)}">Reject</button>
        </div>
        <p class="prop-hint">A squeeze or saying “yes” also approves. A brace never does.</p>`}`;
      this.fg = this.panel.querySelector(".ring-fg");
      this.n = this.panel.querySelector(".ring-n .num");
      this.btns = [...this.panel.querySelectorAll("button")];
      this.tick();
      return;
    }
    this.fg = this.n = null;
    const k = outcome.kind;
    const msg = {
      idle: this.noSim
        ? ["No simulation running", "Nothing is waiting for approval. Open Replay to read the recorded incidents."]
        : this.ro
        ? ["No rule waiting", "The candidate rule appears here after the scripted brace. Approval is disabled in replay; the scripted scenario approves it."]
        : ["No rule waiting", "After a brace, Muse’s candidate rule waits here for your answer."],
      waiting: ["A rule may follow", "Muse is still working on this incident."],
      saving: ["Rule approved", "Writing it to the rule store."],
      saved: ["Rule saved", outcome.rule ? ruleSentence(outcome.rule) : "Approved and saved."],
      rejected: ["Not saved", "The candidate rule was rejected. Nothing changed; the arm keeps its old routes."],
      expired: ["Not saved", "The proposal expired before anyone answered. Nothing changed."],
      none: ["No rule proposed", "Muse had no usable candidate, so there is nothing to approve. The arm stays stopped."],
      lost: ["No rule waiting", "The proposal is gone (answered or replaced) and no saved rule matches this incident."],
    }[k] || ["", ""];
    this.panel.innerHTML = `<div class="prop-done ${k}"><span class="kicker">${esc(msg[0])}</span><p>${esc(msg[1])}</p>${k === "saved" ? `<button class="btn" data-act="see">See it in Rules</button>${outcome.by ? `<small class="muted">approved by ${esc(outcome.by)}</small>` : ""}` : ""}</div>`;
  }

  /** called every frame while visible: countdown ring + reaction-time guard on the buttons */
  tick() {
    if (!this.fg) return;
    const left = Math.max(0, this.deadline - performance.now() / 1000);
    this.fg.setAttribute("stroke-dashoffset", String(RING_C * (1 - left / this.total)));
    this.n.textContent = String(Math.ceil(left));
    this.panel.classList.toggle("urgent", left < 8);
    const ready = performance.now() - this.shownAt > 450; // the runtime rejects answers under 0.3 s
    this.btns?.forEach((b) => (b.disabled = !ready || b.dataset.busy === "1"));
  }
}

export { shortId };
