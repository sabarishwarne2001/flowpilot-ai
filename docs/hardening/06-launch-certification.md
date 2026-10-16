# Launch certification — campaign session 4 (2026-10-14)

_FlowPilot AI was renamed Servabo on 2026-10-16. This file is history and keeps the old name._

**Verdict: GO for a first paying customer, once the owner has done the launch checklist**
(HANDOFF.md, "Owner launch checklist"). Nothing in the engineering list below blocks a launch; the
items carried into `NEXT-SESSION-PROMPT.md` are improvements and edge cases that were found,
written down and, where they could cost money or data, decided.

This page is for the owner: what was checked, how, and what it means.

## What "certified" means here

| Check | Result | How it was proven |
|---|---|---|
| Every engineering plan box (CAMPAIGN-MAP.md, sessions 1–4) | **All ticked** | `CAMPAIGN-MAP.md` has no open box |
| Every endpoint, page, job, migration and integration has a proving test | **Yes** | `docs/hardening/tools/campaign_slices.py --check all` exits 0 (`COVERAGE.csv`) |
| Backend test suite | **Passes: 4,492 passed, 0 failed** (9 skipped) | full local run on the final code (1 h 05 min); CI runs it in four shards on every push |
| Browser test suite | **Passes** (811 tests) | 58 Playwright files against the production bundle with the worker running and the production Content-Security-Policy enforced; a required CI check. Its first run with the policy on found two issues, both fixed (F-410, and a journey that opened a panel before the webhook existed) |
| Types, lint, the self-checks and the build | **Pass** | the CI "Frontend" job (`tsc -b`, the browser tests' types, `npm run lint`, `npm run check:self`, `npm run build`) |
| Database migrations | **One head; up, down to the start and up again** | the CI "Migration head & drift" job |
| Known-vulnerable dependencies | **None** | `pip-audit` clean; `npm audit --omit=dev` 0 (7 development-only advisories accepted, N-074) |
| Under load | **50 people for 15 minutes: p95 670 ms, 0 server errors** | PERFORMANCE.md "Load test" |
| Logs during the final runs | **0 tracebacks, 0 unexpected server errors** | the API and worker logs of the final browser and load runs |
| Accessibility | **WCAG 2.1 AA, light and dark, every page** | `72-accessibility.spec.ts` (axe-core) |
| Phones and tablets | **No page scrolls sideways at 375, 768, 1024 or 1440 px** | `74-layout.spec.ts` |
| Tenant isolation | **Another organization gets "not found"** for every id tried, including TruthMesh, batches, packages and document packs | the isolation sweeps (sessions 1–3) and `75`–`77` |

## What this session added (in plain words)

- **The first five minutes:** a new customer sees "Get started with FlowPilot" and can try a
  built-in sample invoice.
- **It holds up under load:** the first load test found that one assistant answer could freeze the
  whole API for seconds (F-363); fixed and proven.
- **Every page** meets the accessibility standard in light and dark mode and fits a phone.
- **Emails** look right in Gmail, Outlook and Apple Mail.
- **Operations:** the configuration is checked at start-up, every scheduled job was run by hand,
  backups restore in a timed drill, the owner is emailed about failures, and costs are written down.
- **TruthMesh, Batch Operations and Document Packs** were audited and tested end to end at the
  owner's request. Fixed: two simultaneous rebuilds crashing (F-382), an empty workspace rebuilding
  without end (F-409), a "zip bomb" and damaged packages crashing verification (F-390, F-407), pack
  races and wrong errors (F-398, F-399), and the pack card promising more than it did (F-406, F-408).
- **Secrets:** rotating the encryption key now re-encrypts all 16 kinds of stored secret; before,
  it would have broken two-factor sign-in, SSO, webhooks and customers' own AI keys (F-370).
- **Billing:** a changed legal name now reaches Stripe's invoices (F-365).

## What is not certified (and why that is acceptable)

- **Things only the owner can do** — real Stripe and Postmark accounts, the server, DNS, legal
  text, and spot checks with the real AI model and real OCR on scans — are in the owner launch
  checklist. The test sandbox cannot reach those services.
- **The audit's unproven items** (FINDINGS.md, "Session 4 (continued) — … NOT proven"): rare
  races (two dispatches at the same instant), edge cases (a deleted document's TruthMesh conflicts
  wait for the next rebuild) and screen limits (the conflict list shows the first 100). None loses
  money or another tenant's data; each is first in `NEXT-SESSION-PROMPT.md`.
- **32 features whose browser tests do not yet click every action** (their server side is tested):
  listed, highest risk first, in `NEXT-SESSION-PROMPT.md`.

## Sign-off

Engineering sign-off for campaign session 4: the verdict above, with the evidence in this
repository. The owner's sign-off is the launch checklist, done in order.
