# H2M operator UI

Vanilla ES modules, no build step. Three.js is vendored at `web/vendor/three/` and reachable as `import * as THREE from "three"` (import map in `index.html`). Sections open one at a time; there is no long scrolling page.

```
web/
  index.html            shell markup (top bar, dock, #views)
  css/tokens.css        design tokens + shared components (.panel .btn .pill .chip .seg .field .kv .num .kicker)
  css/shell.css         shell layout, portal transition, global shock
  js/core.js            state store, bus, api, loop, helpers   (shell owns; views import from it)
  js/main.js            router, dock, status chips, stop, keyboard
  views/<id>/index.js   one section each: home stage flow signals learn lab
  views/<id>/style.css  that section's CSS (link it from mount() with ctx.css)
  views/<id>/*.js       any extra modules for that section
```

## View contract

`web/views/<id>/index.js` default-exports:

```js
export default {
  async mount(root, ctx) {},   // once, first visit. root = <section class="view"> you own (position:absolute; inset:0; overflow:hidden)
  enter(ctx) {},               // each time the section becomes visible: start animation/render loops
  leave(ctx) {},               // each time it is hidden: stop loops, pause WebGL (setAnimationLoop(null) / loop remover)
  update(state, ctx) {},       // ~10 Hz, ONLY while visible. state = /api/live snapshot
};
```

`ctx` = everything exported by `js/core.js` plus `ctx.router.go(id, [x,y])`:

| name | what |
|---|---|
| `store.state / store.history / store.objectNames` | latest snapshot; every runtime event since page load (oldest first, cap 400); id -> name |
| `bus.on("event", fn)` | one call per NEW runtime event, even while your section is hidden (subscribe once in `mount`). Use it to trigger animations. History from before page load is **not** emitted. |
| `bus.on("state", fn)` | every snapshot |
| `api(path, body)`, `api.get(path)`, `api.del(path)` | JSON helpers. Failure toasts automatically and still returns the body. |
| `loop.add((dt, t) => {}) -> remove` | the single shared rAF loop. Paused when the tab is hidden. Remove it in `leave`. |
| `loadThree()` | `Promise<THREE>`; rejects when WebGL is missing. Give a 2D/CSS fallback in that case. |
| `css(import.meta.url, "style.css")` | link your stylesheet once |
| `esc(text)` | escape anything from the runtime before `innerHTML` (object names, utterances, reasons) |
| `countTo(el, n, {fmt, ms})`, `damp(a,b,lambda,dt)`, `lerp`, `clamp`, `ease`, `pct`, `words`, `describeEvent(e)`, `EVENT_KIND`, `ago(state,e)` | helpers |
| `reduced`, `webgl` | booleans. Respect `reduced` (no camera shake, no looping decoration, short transitions). |

## Rules for every section

1. **Real data only.** Every number, label, trace and state comes from the snapshot or a runtime event. Never invent a value, a latency, a count or a "demo" series. When there is nothing yet, show the structure dim with an honest empty state ("No incident yet", "—").
2. **Say what is synthetic.** `state.emg.source === "synthetic"` and `state.arm.hardware === false` / `state.vision.virtual` must be visible wherever those signals drive a visual (a small `SYNTHETIC EMG` / `VIRTUAL ARM` tag).
3. **Stop is the shell's.** STOP and Space live in the top bar on every section. A section may show a prominent *Cancel* for a pending request, but must not hide or cover the top bar.
4. **Fit the viewport.** On ≥1024px wide a section fits its area with no page scroll; inner lists/panels may scroll. On phones (≤760px) the section may scroll vertically inside itself.
5. **Colour has one meaning per hue** (tokens): `--grip` flexor/squeeze, `--open` extensor/open hand, `--brace` brace/stop/danger, `--ok` approved/done, `--muse` model reasoning. Use them on data and state, not on chrome.
6. **Motion is information.** Pulses, packets, sweeps and 3D moves must be driven by state or events. Idle = calm, not busy. Entrance choreography (staggered `.rise`) on `enter` is welcome; endless decoration is not.
7. **Performance.** 60 fps target. One WebGL renderer per section, `setPixelRatio(min(devicePixelRatio, 2))`, resize via `ResizeObserver`, dispose geometries/materials/renderer on unmount, stop loops in `leave`.
8. **Escape runtime strings** with `esc()` before `innerHTML`.
9. Touch only `web/views/<your id>/`. If you need a shell/core change or a field the snapshot lacks, say so in your report instead of editing it.

## Design direction

Dark "instrument bench": ink-blue grounds, one signal hue per meaning, Bricolage Grotesque for words and JetBrains Mono for every number (tabular). 3D is used where space is the point (the arm and table, EMG as a surface, the hub), 2D SVG/canvas where precision is the point (graphs, flows). Avoid generic dashboard devices: icon-in-tinted-circle KPI tiles, rainbow charts, zebra tables, gradient text, emoji. Numbers are big type, not tiles. Charts have 3–5 faint gridlines, no legends for single series, real units.

## Run it

```bash
.venv/bin/h2m run --arm virtual --emg synthetic --vision off --speech off --llm off --port 8091
```

Open `http://localhost:8091/`. With the virtual arm, `POST /api/arm/arm` then `POST /api/say {"text":"hand me the cup"}` produces real events. Synthetic EMG can be driven with `POST /api/emg/synthetic {"flexor":0.8,"extensor":0}`. Old UI source (API usage reference): `git show HEAD:web/app.js`.
