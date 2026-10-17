# Helm's look: Night Bridge (and Documents' Lumen)

_Written in the Helm visual session (2026-10-17). It replaced the flagship brief's "calm instrument
panel" (§10.2; NEEDS-OWNER N-111). Later sessions keep to it; change it here first._

Helm is the flagship and wears its own identity, **Night Bridge**: a ship's bridge at night. Documents
mode, where people work all day, wears **Lumen**: paper-clear and calm. One switch moves between them,
and the change is a cinematic transition.

## Principles

1. **Every number is real.** Nothing on screen is decorative data. A delta or a figure that is not in
   the data is left out, never invented (the "mandates holding" delta appears only when the trends
   hold a state for 7 days ago).
2. **Motion explains change**: entering, switching, updating. Never noise.
3. **Calm until touched, alive when touched.**
4. **Accessible and fast.** Colour is never the only signal (a word and an icon go with every state);
   every chart has a text or table alternative; reduced motion turns movement off.

## Where things live

| What | Where |
|---|---|
| Tokens (dark Night Bridge, light Day Bridge) | `frontend/src/styles/helm/tokens.css` |
| Atmosphere: aurora, dot grid, grain; the shell's glass rail and top bar | `styles/helm/atmosphere.css` |
| Glass panels and tiles, type, chips, buttons, inputs, toggles, nav, tables, meters, skeletons | `styles/helm/surfaces.css` |
| Fan chart, sparklines, pill bars, calendar heatmap | `styles/helm/charts.css` |
| Entrances, the live dot, the breached pulse, the radar, reduced motion | `styles/helm/motion.css` |
| The Documents \| Helm switch and the view transition (also outside `.helm`) | `styles/helm/switch.css` |
| Page pieces (plan timeline, composer, tour ring, drop zone, boot rail, preview veil) | `styles/helm.css` |
| Components | `frontend/src/components/helm/ui/` |
| Money format | `frontend/src/components/helm/money.ts` |
| Lumen (Documents) | `frontend/src/styles/index.css`, the block that starts "Lumen" |

Everything Helm is scoped to `.helm` (HelmLayout's root), so Documents is untouched by it. The website
imports `helm.css` for its Helm page and wraps it in `.helm.helm-shell.helm-page` (atmosphere absolute,
not fixed; no grain, because the site's CSP refuses `data:` images).

## Tokens

Colour tokens are HSL channels (`--helm-accent: 152 72% 56%`), used as `hsl(var(--helm-accent) / .3)`.

| Token | Night Bridge (dark, the reference) | Day Bridge (light) |
|---|---|---|
| Canvas `--helm-bg` | `160 24% 3.5%` near-black, green undertone | `150 22% 95%` pale mint-grey |
| Deck `--helm-deck` | `160 18% 6%` | `150 18% 92.5%` |
| Glass fill `--helm-glass-top/bottom` | `150 30% 92% / .07 → .035` | white `/ .86 → .68` |
| Rim `--helm-rim` / strong | `150 40% 90% / .09` / `.17` | `160 20% 40% / .14` / `.26` |
| Ink / muted / faint | `150 20% 94%` / `150 8% 68%` / `150 6% 50%` (faint: large text and non-text only) | `165 32% 9%` / `162 9% 31%` / `160 6% 46%` |
| Signal `--helm-accent` (the one hero accent) | `152 72% 56%` | `160 84% 28%` (AA as text) |
| Aqua (second series) | `172 70% 50%` | `182 78% 29%` |
| Amber (drifting) / rose (breached) / sky (likely, information) | `38 94% 62%` / `352 88% 71%` / `204 92% 70%` | `30 94% 32%` / `350 74% 42%` / `206 84% 36%` |

Radii: 20px panels (`--helm-r-panel`), 14px inner tiles (`--helm-r-tile`), pills fully round.
Glow (`--helm-glow`): a 1px signal ring and a soft signal bloom, for the primary and selected things.

## Atmosphere

Four layers behind every Helm page, on the shell (`.helm-shell`), all `pointer-events: none`:
the canvas colour; a slow aurora (signal top-left, aqua bottom-right, 48 s, moving in tenth-of-a-second
steps so the glass above re-blurs ten times a second, not sixty); a fine dot grid masked to the edges;
a 2–3% grain from an inline SVG `feTurbulence` (its own element, `.helm-grain`). Under reduced motion
the aurora stands still.

## Glass panel (`.helm-panel`, `GlassPanel`)

A gradient fill lighter at the top, `backdrop-filter: blur(18px) saturate(140%)` (panels only, never
rows), a 1px rim, a 1px inner top highlight and a deep ambient shadow. On hover the rim brightens, a
**spotlight** follows the pointer (`--mx`/`--my`, set by one delegated rAF-throttled listener,
`useSpotlight`, on the shell) and a sheen runs along the border (a mask-composite ring). Variants:
`helm-panel-strong`, `helm-panel-primary` (signal rim and glow; also `aria-current`/`aria-selected`),
`helm-panel-lift` (2px lift for a panel that is one thing to click). `helm-panel-raised` is an inner
tile with no blur. Position and radius are set with zero specificity (`:where`), so a page's own
`fixed` or `rounded-*` still wins.

## Typography

Inter Variable everywhere. **Money and figures are Inter with tabular, slashed-zero digits**
(`.helm-figure`, `.helm-numeral` for KPI numerals, -0.02 to -0.035em tracking). JetBrains Mono is for
identifiers only (`.helm-id`: fingerprints, invoice numbers, digests, masked accounts). Scale: KPI
numerals 30–36px semibold; page titles `.helm-title` 28–32px; panel titles 15px semibold; labels 11px
uppercase, 0.08em (`.helm-label`).

## Money (decided, N-112)

INR in Helm uses **Indian grouping (en-IN) in every language**: ₹6,59,440.64, ₹3,24,45,975.20, because
Helm's sentences say lakh and crore. Other currencies follow the person's language (N-064). Short
figures (`formatHelmMoneyCompact`): ₹6.6L, ₹1.2Cr; **rupees under a lakh are written out** (₹1,453),
because Chromium's Indian English writes thousands as "T". Everything goes through `formatHelmMoney`
(`HelmBits.Money` uses it); the one fixed-locale `Intl` call is `formatMoneyInLocale` in
`utils/displayTime.ts`. `npm run check:self` ("helm money") guards it.

## Icons and the Helm mark

Lucide, as in the rest of the app. `HelmMark` is the one custom mark: a ship's wheel with eight spokes,
in `currentColor`. It is in the sidebar header, inside the switch (it turns 180° as Helm arrives), on
Start, and, turning slowly, the loading indicator. Loading states are glass skeletons with a soft
shimmer (`HelmSkeleton`), never a bare spinner.

## Motion

Durations 120 / 220 / 420 / 650 ms (`--helm-t-1…4`); easings expo-out `cubic-bezier(.16,1,.3,1)` and a
gentle overshoot `cubic-bezier(.34,1.56,.64,1)`.

- **Entrance** (first view of a page, or a new plan; not on refetch): `.helm-enter` rises 12px and fades
  in, 40 ms apart (`--i`, at most ten staggered). KPI numbers count up for ~900 ms to the exact API value
  (`useCountUp`: the last frame is the value itself). Rings sweep from zero, the fan chart draws in
  (clip-path), heatmap cells fade in along the diagonal, pill bars grow, read-back chips light up one
  after another, plan rows a new plan adds rise in.
- **Live**: the status pill's dot breathes (2.4 s); a breached instrument's ring pulses softly every
  4.8 s, never flashing; "Re-plan now" shows a radar sweep while the planner runs; the bank drop zone's
  border scans while a file is read or imported.
- **Hover**: a 2px lift, the spotlight, button glow; a light sweep crosses a hero button once.
- **Rules**: animate only transform, opacity, filter and clip-path, with one stated exception (a ring
  gauge's sweep is an SVG `stroke-dashoffset` on a 40–120px circle). Nothing over 1 s except the aurora,
  the breath and the pulse. `prefers-reduced-motion: reduce` turns off entrance, count-up, drift, pulse,
  the thumb's glide and the view transition; state stays readable from colour, icon and text.

## Components (`components/helm/ui/`)

`GlassPanel`, `HelmPageHeader` (eyebrow, the page's one h1, description, actions), `KpiTile` (label,
count-up figure with suffix, detail, `DeltaBadge`, a ring or `Sparkline`; screen readers get the exact
figure at once), `RingGauge` (the ring **encodes a value**, 0–1; with no share it draws a full ring in
the tone; a twelve-tick bezel; `pulse` for breached), `StatusPill`, `DeltaBadge` (only for a delta in
the data), `Sparkline`, `FanChart` (the flight path v2: gradient P10–P90 band, glowing P50, dashed floor
with its label, today, run-day ticks, planned payments, expected receipts, a crosshair with a tooltip,
Left/Right/Home/End/PageUp/PageDown read it day by day and announce it, the forecast as a table; it
redraws at its real width so a phone keeps legible text), `PillBars` (with a screen-reader table),
`CalendarHeatmap` (a grid: one tab stop, arrows move by day and week, Enter opens the day; each cell
named with its date and amount; a summary in words), `VerbChip` (Pay, Hold, Chase, Request, Reconcile,
Decide…), `CertificateSeal` (a rosette engraved with the first 8 characters of the fingerprint),
`HelmMark`, `HelmSkeleton`, and the hooks `useSpotlight`, `useCountUp`, `useReducedMotion`.
`GuardBadge`, `StateBadge`, `InstrumentRing` and `VisibilityMeter` (three rings) stay in `HelmBits.tsx`.

Charts are hand-written SVG. **No chart library**; any new runtime dependency must be under 15 kB
gzipped and load only in Helm's chunks (this session added none).

## The switch (`ModeSwitch.tsx`)

A glass track with a thumb that glides between the options (measured with a ResizeObserver; transform
and width over 420 ms with the overshoot). In Helm the thumb is the signal gradient with a glow, the Helm
mark turns as it arrives and one light sweep crosses it; in Documents it is neutral ink. Until a person
has opened Helm once (`helm.discovered` in this browser), the Helm label shimmers. Kept: the radiogroup,
arrow/Home/End keys, one tab stop, the lock and "preview on your plan", mode and last-page memory, every
`data-testid`. Focus follows to the other header's switch.

**The transition.** The pointer entering or focusing the switch prefetches Helm's layout and page code
and the overview (`helmPrefetch.ts`); switching waits up to 600 ms for them while the thumb glides
(240 ms). Then `--helm-vt-x/--helm-vt-y` (the switch's centre) and `data-helm-transition="enter"|"leave"`
go on `<html>` and the switch calls `document.startViewTransition` itself, navigating inside it and
waiting (at most 1 s) until the other mode's switch has rendered, so the new picture is the real page.
Entering: a circle grows from the switch (650 ms, expo-out) while Documents recedes (scale .96, dimmed,
a little blur). Leaving: Helm collapses into the switch while Documents rises. Both switches share
`view-transition-name: helm-mode-switch`, so the switch morphs between headers. The attributes are
removed when the transition finishes or is skipped. No View Transitions support: plain navigation.
Reduced motion: never a transition.

_Why not React Router's `viewTransition` option_: the app's pages are descendant `<Routes>` under one
`createBrowserRouter` route, and there `navigate()` drops the option (found in this session: the flag
reached the router, `startViewTransition` was never called).

## Pages

Cockpit: header with the autonomy status pill and a glowing Re-plan; six KPI tiles (mandates holding,
cash floor confidence, money at risk, waiting for approval, payments planned, value held confirmed); the
fan chart (8 of 12 columns) beside the Promise calendar and payments by week; instruments whose titles
wrap to two lines (full title in the tooltip and name); the plan as a timeline with Apply plan as the
hero; What if, value held and visibility; done recently. Every other page opens with `HelmPageHeader` and
has its own feature (see the commit messages and HANDOFF "Helm visual session").

## Documents: Lumen

Documents' identity, distinct from Night Bridge: a soft light on the canvas from the top (brand blue on
the left, violet on the right) over a fine grid that fades down the page; it never moves. The header is
frosted glass; the sidebar has a faint brand wash and its active page a lit pill with a glowing marker;
cards carry a hairline of light on their top edge and lift when they are links; icon tiles are brand
wells (`.fp-well`); bars fill blue into violet; page titles are larger (`.fp-title`); the primary button
has a violet edge and a sweep of light. Helm's card on the Overview is a window onto the flagship in
Night Bridge's colours. The brand tokens the website shares (`brand/tokens.css`) are unchanged.

**The Overview** (`pages/Dashboard/Dashboard.tsx`, `OverviewParts.tsx`; N-117): a greeting and a live
pill; a hero card (`.fp-hero`) with the total as a count-up numeral (`.fp-hero-numeral`), the last 14
days as rounded columns (`.fp-columns`, today lit) and the success rate as a blue-to-violet ring; the
pipeline (`.fp-pipeline`: uploaded, waiting, being read, read, failed; a stage with work breathes); the
KPI cards (`.fp-kpi-figure`); Helm's card; Recent Activity; the document types as a donut with bars in
the same eight hues (`.fp-kind-1…8`). The 14 days and the "read" count come from the overview API
(`daily_completed`, `completed_count`). Each chart has a list or words for screen readers.

