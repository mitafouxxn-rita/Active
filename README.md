# Vector Field — studio site

A single-page site in the dark, real-time "creative technology studio" idiom:
WebGL background field, oversized grotesque type, procedurally-generated project
covers, custom cursor, scroll-reactive marquee.

Original design and content — inspired by the genre, not a copy of any
particular studio's site.

## Stack

No build step, no dependencies, no external assets. Open `index.html`.

- `index.html` — markup
- `assets/css/styles.css` — layout, type scale, motion
- `assets/js/main.js` — preloader, cursor, reveals, marquee, counters,
  canvas thumbnails, and the WebGL domain-warped noise background

## Notes

- Background shader falls back to a CSS gradient when WebGL is unavailable.
- Renders at 0.6× resolution and pauses when the tab is hidden.
- Honours `prefers-reduced-motion`; all animation is disabled and content
  is shown in its final state.
- Project thumbnails are rasterised from a seeded noise field at runtime, so
  the repo ships zero image bytes and each cover is stable across reloads.
