// Pictogram fallback by card `kind` (h2m.intent.KINDS), drawn here so no asset is needed.
// 24x24, stroke only, currentColor. A real `card.pictogram` image replaces it when the runtime sends one.
const S = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;

export const GLYPH = {
  pick_place: S('<rect x="3" y="11" width="8" height="8" rx="1.8"/><path d="M14 15h7M18 11.5l3.5 3.5-3.5 3.5M3 5h8"/>'),
  place_near_user: S('<path d="M12 3.5v11M7.5 10.5l4.5 4.5 4.5-4.5M4 20h16"/>'),
  look_at: S('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>'),
  say: S('<path d="M4 5.5h16V16h-8l-4.5 3.5V16H4z"/><path d="M8 9.5h8M8 12.5h5"/>'),
  send_message: S('<path d="M3.5 11 20.5 3.5 13 20.5l-2.2-7.3z"/><path d="M10.8 13.2 20.5 3.5"/>'),
  _: S('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/>'),
  stop: S('<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8z"/><path d="M8.5 12h7"/>'),
  rule: S('<path d="M12 3 5 6v5.5c0 4.2 2.8 7.4 7 9.5 4.2-2.1 7-5.3 7-9.5V6z"/><path d="m8.8 12 2.4 2.4 4-4.6"/>'),
  look: S('<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5.5 5.5"/>'),
  wait: S('<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.5l3.5 2"/>'),
  off: S('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5"/><circle cx="12" cy="16" r=".6" fill="currentColor"/>'),
  ask: S('<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.6c.3-1.5 1.4-2.3 2.6-2.3 1.5 0 2.6 1 2.6 2.4 0 1.9-2.6 2-2.6 4.2"/><circle cx="12.2" cy="17" r=".6" fill="currentColor"/>'),
};

export const ICON = {
  next: S('<path d="M5 12h13M13 6.5 18.5 12 13 17.5"/>'),
  back: S('<path d="M19 12H6M11 6.5 5.5 12 11 17.5"/>'),
  ok: S('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  no: S('<path d="M6 6l12 12M18 6 6 18"/>'),
  sound: S('<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>'),
  muted: S('<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>'),
  tick: S('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  cross: S('<path d="M6 6l12 12M18 6 6 18"/>'),
};
