// Home: the hub. A living core that wears the body's signals, five portals to the other sections,
// the product's promise, and an honest health strip. Everything shown is read from the snapshot or an event.
// The portal set comes from the router: Lab live, Replay (read-only server) in its place.
import { portalFacts, healthItems } from "./facts.js";

const HEX = { grip: 0x4fd8ff, open: 0xffc15a, brace: 0xff4d5e, muse: 0xa58bff, ok: 0x5af0a4 };

export default {
  async mount(root, ctx) {
    ctx.css(import.meta.url, "style.css");
    const { esc, bus, api, store, loop, clamp } = ctx;
    const sections = ctx.router.sections;
    const keyOf = (id) => sections.find((s) => s.id === id)?.key || "";
    const replayMode = ctx.mode === "replay";
    // five slots in the hub; the router decides what they are
    const PORTALS = sections.filter((s) => s.id !== "home").slice(0, 5).map((s) => ({ id: s.id, name: s.label, n: sections.indexOf(s) + 1 }));
    const at = Object.fromEntries(PORTALS.map((p, i) => [p.id, i]));
    const keys = sections.map((s) => s.key);

    root.innerHTML = `
      <div class="home" data-mode="flow">
        <div class="home-scene" aria-hidden="true">
          <canvas></canvas>
          <div class="core-css"><i class="r r3"></i><i class="r r2"></i><i class="r r1"></i><i class="burst"></i><i class="orb"></i></div>
        </div>
        <div class="home-hud">
          <div class="home-health rise" style="--i:0" role="list" aria-label="System health"></div>
          <div class="home-keys rise" style="--i:1" aria-label="Keyboard shortcuts">
            <span><kbd>${esc(keys[0])}</kbd>–<kbd>${esc(keys[keys.length - 1])}</kbd> sections</span><span><kbd>Esc</kbd> home</span><span><kbd>Space</kbd> stop</span>
          </div>
          <nav class="home-portals" aria-label="Sections">
            ${PORTALS.map(
              (p, i) => `
              <a class="portal" href="#/${p.id}" data-id="${p.id}" data-tone="" style="--i:${i}">
                <span class="p-in rise" style="--i:${i}">
                  <span class="p-top"><span class="num">${String(p.n).padStart(2, "0")}</span><kbd>${esc(keyOf(p.id))}</kbd></span>
                  <span class="p-name">${p.name}</span>
                  <span class="p-fact"><i></i><span class="txt">—</span></span>
                </span>
              </a>`,
            ).join("")}
          </nav>
          <div class="home-hero">
            <h1 class="rise" style="--i:2"><span class="l1">Hands never moved.</span><span class="l2">One muscle, one twitch.</span></h1>
            <p class="rise" style="--i:3">A brace freezes the arm before you speak. Muse explains why, and you approve a rule that can only make it more cautious.</p>
            <div class="home-actions rise" style="--i:4">
              ${replayMode
                ? '<button class="btn primary" data-act="replay" type="button">Open the replay</button>'
                : '<button class="btn primary" data-act="arm" type="button">Arm the arm</button>'}
              <button class="btn ghost" data-act="stage" type="button">Open the stage</button>
              <span class="note${replayMode ? " wide" : ""}" data-note></span>
            </div>
          </div>
          <aside class="home-vitals rise" style="--i:3" aria-label="Live EMG">
            <div class="vit-row">
              <div class="vit grip"><span class="k">Grip</span><span class="n num"><b data-n="effort">0</b><u>%</u></span><span class="bar"><b data-b="effort"></b><s data-t="confirm"></s></span></div>
              <div class="vit open"><span class="k">Open</span><span class="n num"><b data-n="extensor">0</b><u>%</u></span><span class="bar"><b data-b="extensor"></b></span></div>
              <div class="vit brace"><span class="k">Brace</span><span class="n num"><b data-n="brace">0</b><u>%</u></span><span class="bar"><b data-b="brace"></b><s data-t="brace"></s></span></div>
            </div>
            <div class="home-tags" data-tags></div>
          </aside>
        </div>
      </div>`;

    const $ = (sel) => root.querySelector(sel);
    const home = $(".home");
    const sceneBox = $(".home-scene");
    const canvas = $("canvas");
    const cssCore = $(".core-css");
    const portalEls = [...root.querySelectorAll(".portal")];
    const factEls = portalEls.map((el) => ({ el, txt: el.querySelector(".txt"), last: "", tone: "" }));
    const healthEl = $(".home-health");
    const noteEl = $("[data-note]");
    const armBtn = $("[data-act=arm]"); // null in replay: that server has no /api/arm/*

    const tagsEl = $("[data-tags]");
    const nEls = Object.fromEntries([...root.querySelectorAll("[data-n]")].map((e) => [e.dataset.n, e]));
    const bEls = Object.fromEntries([...root.querySelectorAll("[data-b]")].map((e) => [e.dataset.b, e]));
    const tickConfirm = $("[data-t=confirm]");
    const tickBrace = $("[data-t=brace]");

    const S = { active: false, scene: null, useGL: ctx.webgl && !ctx.reduced, mode: "flow", rules: null, rulesErr: false, last: null, removeLoop: null, lastShock: 0 };

    // ── WebGL scene, if we can ────────────────────────────────────────────────
    if (S.useGL) {
      try {
        const THREE = await ctx.loadThree();
        const { createScene } = await import("./scene.js");
        S.scene = createScene(THREE, canvas, {
          onHover: (i) => portalEls.forEach((el, k) => el.classList.toggle("is-hover", k === i)),
          onPick: (i) => go(PORTALS[i].id, [lastPtr.x, lastPtr.y]),
          ids: PORTALS.map((p) => p.id),
        });
        S.scene.labels = placeLabel;
        canvas.addEventListener("webglcontextlost", (e) => {
          e.preventDefault();
          S.useGL = false;
          S.scene = null;
          applyMode();
        });
      } catch (err) {
        console.warn("home: WebGL scene unavailable, using CSS layout", err);
        S.useGL = false;
        S.scene = null;
      }
    }

    // ── navigation ────────────────────────────────────────────────────────────
    const lastPtr = { x: innerWidth / 2, y: innerHeight / 2 };
    const go = (id, origin) => ctx.router.go(id, origin);
    portalEls.forEach((el, i) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        go(el.dataset.id, [e.clientX || el.getBoundingClientRect().left + 40, e.clientY || el.getBoundingClientRect().top + 20]);
      });
      el.addEventListener("pointerenter", () => S.scene && (S.scene.labelHover = i));
      el.addEventListener("pointerleave", () => S.scene && (S.scene.labelHover = -1));
      el.addEventListener("focus", () => S.scene && (S.scene.labelHover = i));
      el.addEventListener("blur", () => S.scene && (S.scene.labelHover = -1));
    });
    home.addEventListener("pointermove", (e) => {
      lastPtr.x = e.clientX;
      lastPtr.y = e.clientY;
      S.scene?.setPointer(e.clientX, e.clientY);
    });
    home.addEventListener("pointerleave", () => S.scene?.pointerLeave());
    canvas.addEventListener("pointerdown", (e) => {
      lastPtr.x = e.clientX;
      lastPtr.y = e.clientY;
      S.scene?.setPointer(e.clientX, e.clientY);
    });
    canvas.addEventListener("click", (e) => {
      lastPtr.x = e.clientX;
      lastPtr.y = e.clientY;
      S.scene?.pick();
    });
    $("[data-act=stage]").addEventListener("click", (e) => go("stage", [e.clientX, e.clientY]));
    $("[data-act=replay]")?.addEventListener("click", (e) => go("replay", [e.clientX, e.clientY]));

    // The one primary action calls the real arm API. Nothing here fakes an event. (Not rendered in replay.)
    armBtn?.addEventListener("click", async () => {
      const st = S.last?.arm?.state;
      const act = st === "ready" || st === "moving" ? "disarm" : st === "stopped" || st === "fault" ? "reset" : "arm";
      armBtn.disabled = true;
      await api(`/api/arm/${act}`);
      setTimeout(() => (armBtn.disabled = false), 500);
    });

    // ── layout mode + sizing ──────────────────────────────────────────────────
    function applyMode() {
      const w = home.clientWidth;
      const mode = S.useGL && w > 760 ? "scene" : "flow";
      S.mode = mode;
      home.dataset.mode = mode;
      home.dataset.fallback = S.useGL ? "0" : "1";
      if (mode !== "scene") portalEls.forEach((el) => (el.style.transform = ""));
      const r = sceneBox.getBoundingClientRect();
      if (S.scene && r.width > 2 && r.height > 2) S.scene.resize(Math.round(r.width), Math.round(r.height), mode);
    }
    const ro = new ResizeObserver(applyMode);
    ro.observe(home);
    ro.observe(sceneBox);
    applyMode();

    function placeLabel(i, x, y, k, mode) {
      if (mode !== "scene") return;
      const el = portalEls[i];
      const w = sceneBox.clientWidth;
      const cx = clamp(x, 135, w - 135);
      el.style.transform = `translate3d(${cx.toFixed(1)}px, ${y.toFixed(1)}px, 0) translateX(-50%)`;
      el.style.opacity = String(clamp(k * 1.4));
    }

    // ── events -> animation (always subscribed; only animates while visible) ──
    async function loadRules() {
      const r = await api.get("/api/rules");
      if (Array.isArray(r.rules)) {
        S.rules = r.rules.length;
        S.rulesErr = false;
      } else S.rulesErr = true;
      if (S.last) renderFacts(S.last);
    }
    const fireSoon = (i, hex) => i != null && S.active && S.scene?.packet(i, hex);
    bus.on("event", (e) => {
      if (e.kind.startsWith("rule.") || e.kind === "incident.saved") loadRules();
      if (!S.active) return;
      const sc = S.scene;
      const now = performance.now();
      switch (e.kind) {
        case "emg.brace":
        case "arm.stopped":
          if (now - S.lastShock > 350) {
            S.lastShock = now;
            sc?.shock();
            cssShock();
          }
          fireSoon(e.kind === "emg.brace" ? at.signals : at.stage, HEX.brace);
          break;
        case "emg.squeeze":
          sc?.pulse(HEX.grip);
          fireSoon(at.signals, HEX.grip);
          break;
        case "emg.open_hand":
          sc?.pulse(HEX.open);
          fireSoon(at.signals, HEX.open);
          break;
        case "compiler.llm":
          if (e.ok) sc?.museSweep();
          fireSoon(at.flow, HEX.muse);
          break;
        case "incident.explained":
          sc?.museSweep();
          fireSoon(at.learn, HEX.muse);
          break;
        case "intent":
          fireSoon(at.flow, e.decision?.verdict === "block" ? HEX.brace : HEX.ok);
          break;
        case "arm.started":
        case "arm.completed":
        case "arm.fault":
          fireSoon(at.stage, e.kind === "arm.fault" ? HEX.brace : HEX.ok);
          break;
        default:
          if (e.kind.startsWith("incident.") || e.kind.startsWith("rule.")) fireSoon(at.learn, HEX.muse);
          else if (e.kind.startsWith("trial.") || e.kind.startsWith("session.")) fireSoon(at.lab, HEX.ok);
      }
    });

    // CSS core (fallback): colour + squeeze/open scale from the same signals
    const cssColor = (s) => {
      const base = [111, 147, 196], grip = [79, 216, 255], open = [255, 193, 90], brace = [255, 77, 94];
      const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
      const e = s.emg || {};
      let c = mix(base, grip, clamp((e.effort || 0) * 1.5));
      c = mix(c, open, clamp((e.extensor || 0) * 1.5));
      c = mix(c, brace, clamp((e.brace || 0) * 1.8));
      return c.map(Math.round).join(", ");
    };
    function cssShock() {
      cssCore.classList.remove("shock");
      void cssCore.offsetWidth;
      cssCore.classList.add("shock");
    }

    // ── render pieces from state ──────────────────────────────────────────────
    function renderFacts(s) {
      const f = portalFacts(s, store.history, S.rules, S.rulesErr);
      factEls.forEach((x, i) => {
        const v = f[PORTALS[i].id];
        if (v.text !== x.last) {
          x.last = v.text;
          x.txt.textContent = v.text;
        }
        if (v.tone !== x.tone) {
          x.tone = v.tone;
          x.el.dataset.tone = v.tone;
        }
      });
    }
    let healthHtml = "";
    function renderHealth(s) {
      const html = healthItems(s)
        .map((h) => `<div class="hh" role="listitem" data-tone="${h.tone}"><span class="k">${esc(h.key)}</span><span class="v"><i></i>${esc(h.value)}${h.tag ? `<span class="tag">${esc(h.tag)}</span>` : ""}</span></div>`)
        .join("");
      if (html !== healthHtml) healthEl.innerHTML = healthHtml = html;
    }
    let tagsHtml = "";
    function renderTags(s) {
      const t = [];
      if (s.emg && !s.emg.hardware) t.push("SYNTHETIC EMG");
      if (s.arm && !s.arm.hardware) t.push("VIRTUAL ARM");
      const html = t.map((x) => `<span class="tag">${x}</span>`).join("");
      if (html !== tagsHtml) tagsEl.innerHTML = tagsHtml = html;
    }
    function renderVitals(s) {
      const e = s.emg;
      const row = (k, v) => {
        nEls[k].textContent = e ? Math.round(v * 100) : "—";
        bEls[k].style.transform = `scaleX(${e ? clamp(v) : 0})`;
      };
      row("effort", e?.effort || 0);
      row("extensor", e?.extensor || 0);
      row("brace", e?.brace || 0);
      root.querySelector(".vit-row").classList.toggle("dim", !e || !e.live);
      if (e) {
        tickConfirm.style.left = `${clamp(e.confirm_effort ?? 0.3) * 100}%`;
        tickBrace.style.left = `${clamp(e.brace_effort ?? 0.35) * 100}%`;
      }
    }
    function renderAction(s) {
      if (!armBtn) {
        // replay: nothing to arm. Say what this is, from the server's own note.
        const html = `<span class="ro">READ-ONLY · ${esc(s.label || "REPLAY")}</span>${esc(s.note || "Replay of saved recordings. Nothing here moves an arm.")}`;
        if (noteEl.dataset.h !== html) noteEl.dataset.h = noteEl.innerHTML = html;
        return;
      }
      const st = s.arm.state;
      const hw = s.arm.hardware;
      let label, path, cls;
      if (st === "ready" || st === "moving") [label, path, cls] = ["Disarm", "disarm", "btn"];
      else if (st === "stopped" || st === "fault") [label, path, cls] = ["Reset arm", "reset", "btn danger"];
      else [label, path, cls] = [hw ? "Arm SO-101" : "Arm the arm", "arm", "btn primary"];
      if (armBtn.textContent !== label) armBtn.textContent = label;
      if (armBtn.className !== cls) armBtn.className = cls;
      noteEl.innerHTML = `sends <b>POST /api/arm/${path}</b><br>real command, no staged events`;
    }

    this._ = { S, renderFacts, renderHealth, renderTags, renderVitals, renderAction, cssColor, loadRules, applyMode, sceneBox, home, cssCore, root };
    this._.update = (s) => {
      S.last = s;
      renderFacts(s);
      renderHealth(s);
      renderTags(s);
      renderVitals(s);
      renderAction(s);
      S.scene?.setSignal(s);
      if (!S.useGL) {
        const e = s.emg || {};
        cssCore.style.setProperty("--hue", cssColor(s));
        cssCore.style.setProperty("--amp", String(clamp(Math.max(e.effort || 0, e.extensor || 0, e.brace || 0) + (s.arm?.state === "disarmed" ? 0.1 : 0.35))));
        cssCore.style.setProperty("--sq", String(e.effort || 0));
        cssCore.style.setProperty("--ex", String(e.extensor || 0));
      }
    };
    this._.destroy = () => {
      S.removeLoop?.();
      ro.disconnect();
      S.scene?.dispose();
      S.scene = null;
    };
    this._.loop = loop;
    loadRules();
  },

  enter(ctx) {
    const { S, applyMode, loadRules, home, root, loop } = this._;
    S.active = true;
    applyMode();
    // replay the staggered entrance
    root.querySelectorAll(".rise").forEach((el) => {
      el.classList.remove("rise");
      void el.offsetWidth;
      el.classList.add("rise");
    });
    S.scene?.enter();
    S.removeLoop?.();
    if (S.scene) S.removeLoop = loop.add((dt, t) => S.scene?.render(dt, t));
    loadRules();
  },

  leave() {
    const { S } = this._;
    S.active = false;
    S.removeLoop?.();
    S.removeLoop = null;
  },

  update(state) {
    this._?.update(state);
  },
};
