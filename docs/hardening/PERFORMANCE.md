# Performance — measurements before launch (campaign session 4, §B)

Measured, not estimated. Each figure names the machine, the command and the date, so a later run
can be compared with it. The sandbox is a shared cloud container (4 vCPU class); a production VPS
of the size RUNBOOK §9.1 asks for is in the same range.

## Bulk delete of 500 documents (HANDOFF carry-over, CAMPAIGN-MAP §4.1)

- **Question:** does one `POST /workspaces/{id}/work-items/bulk` deleting 500 documents (the
  per-request maximum, `MAX_BULK_IDS`) finish well inside a proxy's timeout, or must it move onto
  the `work_items.bulk` job?
- **Run (2026-10-13):** the browser-test stack (uvicorn, the worker with every profile, Postgres 16,
  local storage), Enterprise tenant. 500 one-page PDFs uploaded through the real upload API (8 at a
  time), processing settled, then one bulk delete of all 500.
  Script: `backend/perf/bulk_delete_timing.py` (its docstring has the command).
- **Result:** HTTP 200 in **6.0 s**, 500 deleted, 0 refused (uploads took 67 s including processing).
- **Limits it must stay under:** production Caddy sets no response timeout on `/api/*`
  (`backend/deploy/Caddyfile`); uvicorn has none. A CDN in front would cut at 100 s (Cloudflare) or
  60 s (a typical load balancer). With object storage over the network (MinIO), each of the 500
  object deletions adds a few milliseconds: an estimated 7 to 9 s in production, still far inside.
- **Decision:** the bulk delete stays inline, with its per-document results in the response. The
  `work_items.bulk` job stays registered and unused (the ledger row is `blocked` with this reason);
  it is the path to take if `MAX_BULK_IDS` is ever raised far above 500.

## Frontend first load (session 4, §B.1)

**What "first load" means.** Before the first screen appears, the browser has to download the
JavaScript and CSS that `dist/index.html` points at: the entry script, every file it lists as
`modulepreload` or `stylesheet`, and anything those files import directly. Everything else (each
page's own code, loaded with `import()`) comes later, only when someone opens that page. The
sizes below are gzipped (level 9), which is close to what Caddy actually sends over the network.
Goal: under about 200 KB.

**Result: 634.2 KB to 191.9 KB gzipped (-70%).**

| Step (one commit each) | First load, KB gzipped |
| --- | ---: |
| Before (main at `6802a2b`) | 634.2 |
| 20 pages that `App.tsx` still imported directly are now lazy, like the other 54 | 331.8 |
| The `markdown` manual chunk removed from `vite.config.ts` | 255.3 |
| The two-factor panel (and its QR code library) in the policy notice is lazy | 244.1 |
| The dashboard and organization layouts are lazy, like the platform layout already was | **191.9** |

| First-load file | Before, KB gz | After, KB gz |
| --- | ---: | ---: |
| Entry script (`index-*.js`) | 409.6 | 169.2 |
| `pdf-*.js` (pdf.js, preloaded) | 125.5 | not in first load |
| `markdown-*.js` (marked, DOMPurify, highlight.js, preloaded) | 76.4 | not in first load |
| Stylesheet (`index-*.css`, Tailwind and font faces) | 22.3 | 22.3 |
| `theme-boot.js` | 0.4 | 0.4 |
| **Total** | **634.2** | **191.9** |

What a whole first screen costs now (first load plus that page's lazy chunks):

| Screen | KB gzipped |
| --- | ---: |
| `/login` | 196.7 |
| `/register` (adds the form and validation libraries) | 225.7 |
| Workspace dashboard (adds the dashboard layout and the dashboard page) | 269.7 |

**What was wrong, in plain terms.**

1. *Pages loaded for everyone.* 54 routes were already lazy, but 20 pages were still imported at
   the top of `App.tsx` (sign-in, registration, dashboard, work items, assistant, automation,
   settings, workspace pickers). An import at the top of a file is downloaded before anything
   renders, so a visitor opening the login page also downloaded the assistant canvas, which
   brought pdf.js (125 KB) with it, and the settings pages with their QR code, form and schema
   libraries. They now load with their route.
2. *A shared helper dragged the markdown libraries in.* `vite.config.ts` grouped marked,
   DOMPurify and highlight.js into one "markdown" chunk. Rollup (the tool Vite uses to bundle)
   also puts a group's dependencies into the group. highlight.js is an older (CommonJS) package,
   so the group received Rollup's small shared CommonJS helper. React needs the same helper, so
   the entry script imported the whole markdown chunk to get it, and `index.html` preloaded it
   for every visitor (76 KB). The group is gone; the three libraries now load with the assistant
   canvas, the one page that renders markdown. The `pdf` group has no such dependency and stays.
3. *A dialog mounted everywhere carried a settings panel.* The "your organization requires
   two-factor sign-in" notice is mounted on every screen and imported the two-factor settings
   panel directly. The panel now loads only when that notice actually shows it.
4. *The signed-in chrome loaded on the login page.* The workspace and organization layouts
   (sidebars, menus, command palette, notification tray, billing banners, and the floating-ui
   library that positions menus and tooltips) were imported directly, about 52 KB a signed-out
   visitor never sees. They are lazy now, as the platform layout already was. So a workspace page
   does not wait first for the layout and then for its own chunk, the tenant and organization
   guards start downloading the layout while the tenant is being looked up.

**What is still in the first load, and why.** React and react-dom, React Router, TanStack Query,
axios and the API client (the session is restored before anything renders), sonner (the toast
area is mounted app-wide), the route table, the guards, the sign-in shell, the step-up and policy
dialogs, the loading screen, and the one stylesheet. React and react-dom alone are roughly a third
of the entry. Going much lower would mean removing or replacing one of these, which changes
behaviour, so it was not done.

**Behaviour.** Nothing visible changed except that the pages and layouts listed above now show the
same "Loading…" screen the 54 lazy pages already showed while their code downloads (only on the
first visit after a release: Caddy serves `/assets/*` as immutable for a year, so the files are
cached afterwards), and the two-factor notice shows a small spinner for a moment before its panel.
The Content-Security-Policy is unchanged: `index.html` still has no inline script, and every chunk
is served from the same origin.

**Largest chunks after the change** (only the entry script and the stylesheet are in the first load):

| Chunk | KB gzipped | Loaded when |
| --- | ---: | --- |
| zxcvbn English dictionary (`index-*.js`) | 591.1 | the first keystroke in a "choose a password" field |
| zxcvbn common dictionary (`index-*.js`) | 216.5 | same |
| Entry script (`index-*.js`) | 169.2 | first load |
| `pdf-*.js` | 125.5 | pages that show a PDF |
| `AssistantCanvas-*.js` (with the markdown libraries) | 87.1 | the assistant canvas |
| `Automation-*.js` | 44.0 | the automation page |
| `Settings-*.js` | 30.7 | personal settings |
| Stylesheet | 22.3 | first load |
| `ReviewHub-*.js` | 21.0 | the review hub |
| `TruthMesh-*.js` | 18.5 | TruthMesh |

`npm run build` still prints "Some chunks are larger than 1000 kB": that is the zxcvbn English
dictionary (1,172 KB before gzip). It is the password-strength word list, loaded only while
someone types a new password; splitting it would not make any screen faster, so it was left.

**Lighthouse 12.2.1** (mobile profile, simulated slow 4G; Chromium 141 headless; `vite preview`
serving `dist/` with no backend; one run each, so expect a few points of noise):

| Page | Performance before | Performance after | Accessibility | Best practices |
| --- | ---: | ---: | ---: | ---: |
| `/login` | 68 | 91 | 100 (before and after) | 96 (before and after) |
| `/register` | 60 | 86 | 100 (before and after) | 96 (before and after) |

First contentful paint went from 4.6 s to 2.6 s on `/login` and from 4.7 s to 2.8 s on `/register`;
largest contentful paint from 4.7 s to 2.8 s and from 5.0 s to 3.3 s. The 4 points missing in
best practices are one console error that exists only in this setup: with no backend behind
`vite preview`, `GET /api/v1/branding/manifest` answers 404.

**Re-measure:**

```bash
cd frontend
npm run build
node scripts/measure-first-load.mjs                    # first load + the 10 largest chunks
node scripts/measure-first-load.mjs --plus Login       # add a lazy page (chunk name before the hash)
node scripts/measure-first-load.mjs --max-kb 200       # exits 1 above a budget, usable in CI
```

Lighthouse, as run here (any free port; Chromium from the Playwright browser folder; this sandbox
also needed `--no-proxy-server` in the Chrome flags because it routes traffic through a proxy;
stop the preview server afterwards):

```bash
cd frontend && npx vite preview --port 4317 --strictPort --host 127.0.0.1 &
CHROME_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome npx --yes lighthouse@12.2.1 \
  http://127.0.0.1:4317/login --only-categories=performance,accessibility,best-practices \
  --chrome-flags="--headless=new --no-sandbox" --output=json --output-path=./lh-login.json
```
