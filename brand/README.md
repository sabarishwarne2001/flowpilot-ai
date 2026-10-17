# Servabo brand

One place for what the app (`frontend/`) and the website (`website/`) share, so they look like one
company and cannot drift:

| File | What it is | Used by |
|---|---|---|
| `tokens.css` | The colour, surface and radius variables, light and dark (WCAG 2.1 AA) | `frontend/src/styles/index.css`, the website's global stylesheet |
| `tailwind-preset.js` | Tailwind's semantic colours, radii, type and elevation, reading `tokens.css` | `frontend/tailwind.config.js`, `website/tailwind.config.mjs` |
| `mark.ts` | The mark's and the wordmark's geometry (generated) | the app's `ServaboMark`, the website's logo |
| `tokens.json` | The name, its meaning, the positioning line and the brand colours as hex | the asset builder, the website |
| `assets/` | `servabo-mark.svg` (colour), `servabo-mark-mono.svg` (one colour, the S cut out), `servabo-logo-{light,dark,mono}.svg`, `servabo-wordmark.svg` | press, documents, anyone who needs the logo |

**The mark.** A document with its top-right corner folded, holding an "S" drawn as one stroke: a
document, kept. Servabo is Latin for "I will keep", from *pacta sunt servanda*, agreements must be
kept. Built on a 32-unit grid, it reads at 16 px, works in one colour, and the colour version's
blue tile and white S read on light and dark backgrounds alike. In emails it is drawn in HTML (a
blue tile with a white "S"), because mail clients hold remote images.

**The wordmark.** "Servabo" in Inter (SIL Open Font License, the app's own typeface) at weight
640, as outlines, so it looks the same everywhere without loading a font.

**Rebuilding.** The favicons, app icons and Open Graph cards in `frontend/public` and
`website/public` are generated, and committed so neither build needs these tools:

```bash
python -m venv /tmp/brand && /tmp/brand/bin/pip install fonttools brotli cairosvg pillow shapely svgpathtools
/tmp/brand/bin/python brand/scripts/build_brand_assets.py   # needs frontend/node_modules (Inter)
```
