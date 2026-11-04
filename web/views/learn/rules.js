// Rules view: gallery of saved rules (left) and the table map with their effects (right).
import { esc } from "/web/js/core.js";
import { ago, respInfo, ruleSentence, shortId } from "./data.js";

const SHIELD = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v6c0 4.6-3.2 7.9-8 9-4.8-1.1-8-4.4-8-9V6z"/><path d="M8.5 12.2h7"/></svg>`;

function thumb(model, rule) {
  const X = (x) => 4 + x * 80;
  const Y = (y) => 4 + (1 - y) * 80;
  let s = `<rect x="4" y="4" width="80" height="80" rx="6" class="th-t"/>`;
  for (let i = 1; i < 4; i++) s += `<path d="M${4 + i * 20} 4V84M4 ${4 + i * 20}H84" class="th-g"/>`;
  if (!model) return `<svg class="thumb" viewBox="0 0 88 88"><rect x="4" y="4" width="80" height="80" rx="6" class="th-t"/><text x="44" y="42" text-anchor="middle" class="th-n">${esc(rule.avoid)}</text><text x="44" y="56" text-anchor="middle" class="th-n dim">no geometry</text></svg>`;
  const z = model.zones.find((q) => q.name === rule.avoid) || model.zones[0];
  if (z) {
    const [x0, y0, x1, y1] = z.box;
    s += `<rect x="${X(x0)}" y="${Y(y1)}" width="${(x1 - x0) * 80}" height="${(y1 - y0) * 80}" class="th-z"/>`;
  }
  if (model.pts?.length > 1) {
    const d = model.pts.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)} ${Y(p.y).toFixed(1)}`).join("");
    s += `<path d="${d}" class="th-p"/><path d="${d}" class="th-ps"/>`;
    const f = model.freezePose;
    s += `<circle cx="${X(f.x)}" cy="${Y(f.y)}" r="3.4" class="th-f"/>`;
  }
  return `<svg class="thumb" viewBox="0 0 88 88" aria-hidden="true">${s}</svg>`;
}

const TICK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 8.4l3 3 6.6-6.8"/></svg>';
const CROSS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>';
const nice = (k) => String(k).replaceAll("_", " ");

/**
 * The verifier's result, only when the rule payload carries it (r.verifier as an object of checks, a boolean or a
 * string, or r.verified as a boolean). The runtime does not publish these yet: nothing is shown, never a guessed tick.
 */
function verifierHtml(r) {
  const items = [];
  const v = r.verifier;
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, x] of Object.entries(v)) {
      if (typeof x === "boolean") items.push([x, nice(k)]);
      else if (typeof x === "string" || typeof x === "number") items.push([null, `${nice(k)}: ${x}`]);
    }
  } else if (typeof v === "boolean") items.push([v, "verifier"]);
  else if (typeof v === "string" && v) items.push([null, `verifier: ${v}`]);
  if (typeof r.verified === "boolean") items.push([r.verified, "verified"]);
  if (!items.length) return "";
  return `<ul class="rc-ver" aria-label="Verifier result">${items.map(([ok, label]) => `<li class="${ok === true ? "ok" : ok === false ? "bad" : ""}">${ok === true ? TICK : ok === false ? CROSS : ""}<span>${esc(label)}</span></li>`).join("")}</ul>`;
}

export class RulesView {
  constructor(host, ctx, cb) {
    this.ctx = ctx;
    this.cb = cb;
    this.host = host;
    this.ro = !!cb.readOnly;
    host.innerHTML = `<div class="rules-main">
        <div class="rules-bar"><span class="shield">${SHIELD}<b>Rules can only restrict</b><em>They make the arm more cautious, never less. A rule never relaxes a safety check.${this.ro ? " Read-only here: rules can’t be approved or deleted in replay." : ""}</em></span><span class="rules-count num"></span></div>
        <div class="rules-grid" role="list"></div>
      </div>
      <aside class="rules-map panel">
        <div class="panel-head"><h2>Route map</h2><span class="map-leg"><i class="lg z"></i>avoid zone<i class="lg p"></i>blocked path</span></div>
        <div class="map-host"></div>
        <p class="map-note"></p>
        <div class="carries"><h3>Recent carries</h3><ul></ul></div>
      </aside>`;
    this.grid = host.querySelector(".rules-grid");
    this.count = host.querySelector(".rules-count");
    this.mapHost = host.querySelector(".map-host");
    this.note = host.querySelector(".map-note");
    this.carries = host.querySelector(".carries ul");
    this.seen = new Set();
    this.selected = null;
    this.sig = "";
    this.grid.addEventListener("click", (e) => this.onClick(e));
  }

  cardEl(id) {
    return this.grid.querySelector(`.rcard[data-id="${CSS.escape(id)}"]`);
  }

  onClick(e) {
    const card = e.target.closest(".rcard");
    if (!card || card.classList.contains("ghost")) return;
    const id = card.dataset.id;
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "del" && !this.ro) return this.del(card, e.target.closest("button"), id);
    if (act === "replay") return this.cb.onReplay(card.dataset.inc);
    this.cb.onSelect(id);
  }

  del(card, btn, id) {
    if (!btn.classList.contains("armed")) {
      btn.classList.add("armed");
      btn.textContent = "Confirm delete";
      clearTimeout(btn._t);
      btn._t = setTimeout(() => {
        btn.classList.remove("armed");
        btn.textContent = "Delete";
      }, 3200);
      return;
    }
    clearTimeout(btn._t);
    btn.disabled = true;
    card.classList.add("leaving");
    this.cb.onDelete(id).then((ok) => {
      if (!ok) {
        card.classList.remove("leaving");
        btn.disabled = false;
        btn.classList.remove("armed");
        btn.textContent = "Delete";
      }
    });
  }

  render({ data, geo, state, latestIncident, selected, routesError }) {
    this.selected = selected;
    const rules = data?.rules || [];
    const sig = JSON.stringify([data?.error, rules.map((r) => [r.id, r.verifier ?? null, r.verified ?? null]), [...geo.keys()].map((k) => !!geo.get(k)), selected, latestIncident]);
    this.count.textContent = data?.error ? "unreadable" : `${rules.length} saved`;
    if (sig !== this.sig) {
      this.sig = sig;
      if (data?.error) {
        this.grid.innerHTML = `<div class="rules-empty bad"><b>The rule store can’t be read</b><p>${esc(data.error)}</p><p class="muted">The arm treats this as “unknown” and holds tagged carries instead of assuming there are no rules.</p></div>`;
      } else if (!rules.length) {
        this.grid.innerHTML = `<div class="rules-empty"><svg viewBox="0 0 120 80" aria-hidden="true"><rect x="8" y="10" width="104" height="60" rx="8" class="e-t"/><path d="M8 40h104M60 10v60" class="e-g"/><rect x="66" y="20" width="26" height="16" class="e-z"/><path d="M20 58C40 58 50 28 100 28" class="e-p"/></svg><b>No rules yet</b><p>${this.ro ? "Rules from a simulation appear here once the script approves one." : "Approve a candidate rule after a brace and it is saved here."} ${data?.zones?.length ? `Known avoid zones: ${data.zones.map(esc).join(", ")}.` : ""}</p></div>`;
      } else {
        this.grid.innerHTML = [...rules].reverse().map((r, i) => this.card(r, geo.get(r.incident_id), state, latestIncident, i)).join("") + `<div class="rcard ghost" aria-hidden="true"><svg viewBox="0 0 88 88"><rect x="4" y="4" width="80" height="80" rx="6" class="th-t"/><path d="M4 44h80M44 4v80" class="th-g"/><path d="M18 70C34 70 40 30 70 24" class="e-p"/></svg><div class="rc-body"><p class="rc-s">The next rule appears here.</p><p class="rc-eff">${this.ro ? "Read-only. Saving and deleting rules is disabled in replay." : "Brace while the arm carries something, approve Muse’s candidate, and it is saved."}</p></div></div>`;
      }
      this.grid.querySelectorAll(".rcard:not(.ghost)").forEach((c) => {
        const id = c.dataset.id;
        c.classList.toggle("sel", id === selected && !c.classList.contains("ghost"));
        if (!this.seen.has(id) && this.seen.size) c.classList.add("fresh");
      });
      rules.forEach((r) => this.seen.add(r.id));
    } else this.grid.querySelectorAll(".rcard").forEach((c) => c.classList.toggle("sel", c.dataset.id === selected));

    this.grid.querySelectorAll(".rc-ago").forEach((el) => {
      const v = ago(state, Number(el.dataset.t)) || "—";
      if (el.textContent !== v) el.textContent = v;
    });
    // map notes
    const zones = data?.zones || [];
    this.note.innerHTML = `Taught-route geometry isn’t exposed by the runtime, so this map draws only what the incidents recorded: ${zones.length ? `the <b>${zones.map(esc).join(", ")}</b> avoid zone${zones.length > 1 ? "s" : ""}` : "no avoid zones"} and the path of the carry that caused each rule. ${routesError ? `<span class="bad">Route config unavailable: ${esc(routesError)}</span>` : ""}`;
  }

  card(r, model, state, latest, i) {
    const info = respInfo(r);
    const when = ago(state, r.created_at);
    const replayable = r.incident_id && r.incident_id === latest;
    return `<article class="rcard" role="listitem" data-id="${esc(r.id)}" data-inc="${esc(r.incident_id || "")}" style="--i:${Math.min(i, 8)}">
      ${thumb(model, r)}
      <div class="rc-body">
        <p class="rc-s">${esc(ruleSentence(r))}</p>
        <div class="chips">${r.when_holding.map((t) => `<span class="chip"><i></i>${esc(t)}</span>`).join("")}<span class="chip bad"><i></i>${esc(r.avoid)}</span><span class="chip muse"><i></i>${esc(info.kind)}</span></div>
        <p class="rc-eff">${esc(info.effect(r))}</p>
        ${verifierHtml(r)}
        <dl class="rc-meta"><dt>Learned</dt><dd class="num rc-ago" data-t="${r.created_at}">${esc(when || "—")}</dd><dt>Incident</dt><dd class="num">${r.incident_id ? `…${esc(shortId(r.incident_id))}` : "—"}${replayable ? ` <button class="lnk" data-act="replay">replay</button>` : ""}</dd><dt>Rule</dt><dd class="num">${esc(shortId(r.id))}</dd></dl>
      </div>
      ${this.ro ? "" : '<button class="btn danger sm del" data-act="del">Delete</button>'}
    </article>`;
  }

  renderCarries(history, rules) {
    const sig = history.length + "|" + (rules || []).map((r) => r.id).join();
    if (sig === this.carrySig) return;
    this.carrySig = sig;
    const ids = new Set((rules || []).map((r) => r.id));
    const arr = history.filter((e) => e.kind === "arm.started" && (e.route || e.applied_rules)).slice(-5).reverse();
    this.carries.innerHTML = arr.length
      ? arr
          .map((e) => {
            const ap = (e.applied_rules || []).filter(Boolean);
            return `<li><span class="chip"><i></i>${esc(e.route || "route —")}</span>${ap.length ? ap.map((id) => `<span class="chip ok"><i></i>rule …${esc(shortId(id))}${ids.has(id) ? "" : " (deleted)"}</span>`).join("") : `<span class="muted">no rule applied</span>`}</li>`;
          })
          .join("")
      : `<li class="muted">No carries since this page loaded.</li>`;
  }
}
