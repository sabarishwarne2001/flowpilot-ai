# Findings

How to read this file:

- **Severity.** P0 = stop-ship (data leak, money loss, cannot run). P1 = must fix
  before a paid launch. P2 = must fix before enterprise sales or a real security
  review. P3 = quality or hygiene.
- **Status.** `unverified` = seen by reading code only; no test has proved it yet.
  `confirmed` = a failing test or a live run proved it. `fixed` = the Evidence
  Rule in CLAUDE.md is met (failing test, fix, that test passes, suite passes).
- Every Phase 0 finding is `unverified`: Phase 0 does not run code. Severities
  are provisional until a test proves the behaviour.

| ID | Sev | Status | Area | Title |
|----|-----|--------|------|-------|
| F-001 | P1 | **fixed** (Phase 1, PR #2) | CI | CI on `main` fails instantly with zero jobs; the encoding gate would also fail (44 files) |
| F-002 | P1 | **resolved** (final release: 291-test browser suite (290 passed, 1 skipped by design) incl. model-dependent flows via the local model stand-in, backend suite, live engine harness; ledger in COVERAGE.csv) | Tests | ARCH-31..50 features, billing webhooks and the frontend have no automated tests |
| F-003 | P2 | **fixed** (Phase 2) | Config/secrets | Hard-coded default secrets are used if the env var is missing |
| F-004 | P2 | **fixed** (Phase 2; reads/deletes await N-003) | Plan gating | Some endpoints behind a locked nav item have no server-side plan check |
| F-005 | P3 | **fixed** (Phase 5, `7adf7b3`; the three consoles in the final release, `a796946`, `f1a256b`, N-002) | Plan gating / UX | Sidebar lock state does not match what the server enforces |
| F-006 | P2 | **fixed for Compose** (Phase 2; a real `docker compose` run on the VPS is unverified) | Jobs / ops | Retention, backup and 13 sweepers run only from host cron, which the prod compose file does not start |
| F-007 | P3 | **fixed** (logo/favicon `1a98cdb`; README/LICENSE Phase 5; historical scripts archived `ec912e0`, N-016; history rewrite declined, N-005) | Repo hygiene | Binary in history, empty README/LICENSE, UTF-16 requirements, ~250 historical scripts |
| F-008 | P3 | **fixed** (Phase 5, `cc48275`) | Frontend guards | Route-level role guard exists but is unused; org pages have no route-level role check |
| F-009 | P3 | **fixed** (Phase 5, `0074f74`; no automated test) | Frontend | `/admin` has no index route (likely an empty screen) |
| F-010 | P3 | **fixed** (Phase 5, `7c7933f`; checked by grep, no automated test) | Config | `frontend/.env.example` omits `VITE_API_URL`, the only variable the code reads |
| F-011 | P3 | **partly fixed** (Phase 2; the long tail of harmless defaults is Phase 4) | Config | 246 of 302 backend settings are missing from `.env.production.template` |
| F-012 | P3 | **not a defect** (Phase 2; guard tests) | Auth | Live-review WebSocket accepts the access token in the URL query string |
| F-013 | P3 | **verified** (Phase 2; F-034 fixed) | Billing | Three webhook routes can receive Stripe events; each must be proven to verify signatures |
| F-014 | P3 | **fixed** (Phase 5, `39fb0d5`) | Tenancy URLs | Organization slug `request` is not reserved and collides with the public `/request/:token` page |
| F-015 | P3 | **fixed** (Phase 5, `782d0e3`, N-006) | UI/API roles | Sidebar hides some pages from ADMIN that the API allows ADMIN to use |
| F-016 | P1 | **fixed** (Phase 5: full suite 3,223 passed, 0 failed, 9 skipped; `main` 3,134 passed, 22 failed) | Tests | The backend test suite is red: 2,285 passed, 219 failed, 33 errors, 9 skipped of 2,546 |
| F-017 | P2 | **fixed for the 22 real differences** (Phase 5, `b2accf1`, `ad411ec`; 283 undeclared-index/constraint lines remain, harmless and ratcheted) | Schema | 314 model/migration drift operations; the CI drift gate could never fail |
| F-018 | P1 | **fixed** (Phase 1, PR #2) | Startup / CI | A fresh clone could not install, migrate, test or start (13 blockers) |
| F-019 | P1 | **fixed** (Phase 2) | OCR | On one engine error class, OCR silently returns invented text |
| F-020 | P1 | **fixed** (Phase 2) | Ingestion | Any chunk without a bounding box makes the whole document fail |
| F-021 | P2 | **fixed** (Phase 2) | Secrets | A rejected BYOK API key is echoed back in the 422 response |
| F-022 | P2 | **not a defect** (Phase 2; guard tests) | Tenancy | An AI agent tool selector takes no tenant scope |
| F-023 | P2 | **not a defect** (Phase 2; guard tests) | Security | Five modules read `X-Forwarded-For` themselves (spoofable client IP) |
| F-024 | P2 | **resolved by N-009** (pre-launch; RUNBOOK 9.2 step 6) | Deployment | Production `migrate` fails on a fresh database (ARCH-40 contract flag) |
| F-025 | P3 | **fixed** (Phase 5, `b2accf1`) | Models | `organization_addon` model is not registered in `app/models/__init__` |
| F-026 | P3 | **fixed** (Phase 5, `02a67de`) | Uploads | Storage errors surface as a raw 500 on upload |
| F-027 | P3 | **fixed** (Phase 5, `f6e4b36`) | Dev env | Dev compose makes the shell's `AWS_ACCESS_KEY_ID` the MinIO root user |
| F-028 | P3 | **fixed** (Phase 5, `a8a05ec`) | Frontend | Dashboard fires 4 failing avatar requests for users without an avatar |
| F-029 | P3 | **closed** (final release: gates archived with their scripts and the CI gates job retired, N-016; the two gate modules active tests import still pass) | Gates | 33 of 78 verification gates fail; several are stale |
| F-030 | P3 | **partly fixed** (Phase 5: the order dependence was F-100, fixed; the suite still takes ~25 min on a RAM disk) | Tests | The test harness is slow and order-dependent |
| F-031 | P3 | **fixed** (Phase 2) | Tenancy (IDOR) | Two document sub-routes answer 200 for another tenant's or an unknown document |
| F-032 | P2 | **fixed** (Phase 2) | Auth | An unverified account for someone else's address can list that address's pending invitations |
| F-033 | P3 | **closed** (Phase 5, `e80c900`: Dodo's envelope has no mode flag; the per-mode secret separates them) | Billing | Dodo's "test event reached a live deployment" guard compares the deployment's config with itself and cannot fire |
| F-034 | P3 | **fixed** (Phase 2) | Billing | `/billing/webhooks/STRIPE` (any capitalisation but lowercase) crashed with an unhandled AttributeError |
| F-035 | P2 | **fixed** (Phase 2) | Uploads | Page-level PDF actions (JavaScript, Launch, SubmitForm) survive the upload scrub into the stored, downloadable file |
| F-036 | P2 | **fixed** (Phase 2) | Uploads | An upload over the size limit crashed the upload endpoint with a 500 instead of answering 413 |
| F-037 | P3 | **fixed** (Phase 2) | Input handling | A NUL character (`%00`) in a search box crashed 19 routes with a 500 |
| F-038 | P2 | **fixed** (Phase 2) | Frontend | Production builds shipped source maps (`index-*.js.map`), publishing the original TypeScript |
| F-039 | P2 | **fixed for 12 of 13 packages** (Phase 5, `936ff0e`, `f80b517`, `8bb9c9b`; torch/setuptools/paramiko left with reasons) | Dependencies | 7 npm advisories (fixed) and 13 Python packages with known advisories (4 bumped, rest tracked) |
| F-040 | P1 | **fixed** (Phase 2) | Deployment | The production API container cannot start: `gunicorn` is in no requirements file |
| F-041 | P2 | **fixed** (Phase 2) | Logs | gunicorn's access log wrote capability tokens (public upload and calendar-feed paths), query strings and the Referer to stdout |
| F-042 | P3 | **fixed** (Phase 2) | Ops | No readiness probe: `/health` reported "healthy" with Postgres down |
| F-043 | P3 | **fixed** (Phase 2) | Containers | Floating image tags (`pg16`, `7-alpine`, `2-alpine`, `python:3.12-slim`) |
| F-044 | P3 | **fixed** (final release, `c47ae3f`: CSP enforced on the platform host and custom domains; whole browser suite passes under it, `fc8c643`) | Frontend/ingress | No Content-Security-Policy header |
| F-045 | P3 | **partly fixed** (Phase 5, `3d8cff5`: OCR engine only in the `ocr` image, 1.2 GB less per other image; the CUDA build of torch remains, see the entry) | Containers | Every image, including `web`, installs torch, paddle and sentence-transformers (about 8 GB) |
| F-046 | P3 | **fixed** (Phase 2) | Information exposure | Swagger UI and the full OpenAPI schema (every route and request shape) were public in production |
| F-047 | P1 | **fixed** (Phase 2) | Deployment / config | 23 settings the production template tells you to fill in never reached the containers (LLM keys, Dodo, billing gateway, price ids, token lifetimes, upload limit) |
| F-048 | P3 | **fixed** (Phase 5, `53d9267`; the allowance itself is N-018) | Auth / availability | Sign-in is limited to 10 attempts per 5 minutes per IP, not the intended 20, because the limiter runs twice; one shared office network can lock everyone out |
| F-049 | P3 | **fixed** (Phase 5, `a8a05ec`) | Frontend / noise | Every page requests the signed-in user's avatar and logs a 404 when they have none (most users) |
| F-050 | P0 | **fixed** (Phase 3; full backend suite re-run pending) | API stability | Opening a scanned packet's review screen aborted the whole API process (PDFium used from several threads) |
| F-051 | P1 | **fixed** (Phase 4) | Team / billing | Accepting a team invitation fails with HTTP 500 on any organization with a live subscription |
| F-052 | P2 | **fixed** (Phase 4) | Audit log | Audit log export (CSV and NDJSON) always fails: the page sends `format=CSV`, the API only accepts `csv`/`jsonl` |
| F-053 | P2 | **fixed** (Phase 5 error pages `cc48275`; final release hides the three pages from viewers, `a796946`, N-019) | Roles / UX | A workspace VIEWER sees Workflows, Run history and Review queue in the sidebar, but each page fails with 403 errors |
| F-054 | P3 | **fixed** (Phase 5, `cc48275`) | Roles / UX | Admin-only organization pages opened by a MEMBER say "couldn't be loaded. Try again" instead of "you don't have permission"; Branding shows a full edit form |
| F-055 | P3 | **fixed** (Phase 5, `53f383a`) | Roles / UX | The BILLING role gets a 403 on every organization page (the header bell asks for organization notifications it may not read) |
| F-056 | P2 | **fixed** (Phase 5, `1e52864`) | AI assistant | When the AI provider is unavailable the user's question disappears; only a 4-second toast says why |
| F-057 | P3 | **fixed** (Phase 5, `84f3074`) | Error messages | Forms show "Request failed with status code 422" instead of the server's explanation (ERP target, corroborator rules) |
| F-058 | P3 | **fixed** (Phase 5, `07e768b`) | Frontend / noise | Normal pages use 404 as "nothing configured" (workspace email override, branding logo), so the console shows errors on healthy pages |
| F-059 | P3 | **fixed** (Phase 5, `4192ad8`) | Plan gating / UX | A locked feature opened by URL shows a lock card with no upgrade button; the upgrade path exists only in the sidebar |
| F-060 | P3 | **fixed** (Phase 5, `903ea97`) | Custom domains / UX | "Claim domain" is offered on a deployment where custom domains are switched off; the click returns 501 |
| F-061 | P3 | **fixed** (all seven built: items 1, 4, 6 in Phase 4; 2 `e3dc770`, 3 `d77e59e`, 5 `8ef1157`, 6-7 `f53ccc8`/`c9c01cc` in the final release, N-020) | Product gaps | Capabilities in the Phase 3 brief that do not exist: global search by invoice number, notification filters and mark-unread, promo code, webhook test ping, page image and field correction in the document viewer |
| F-062 | P3 | **fixed** (Phase 5, `32b3721`) | Settings | The unsaved-changes guard does not cover the workspace General form: a rename is lost silently on sidebar navigation |
| F-063 | P2 | **fixed** (final release, `49182fd`: deterministic OpenAI-compatible model stand-in; model-dependent flows run in every browser run, `E2E_LLM=1` in CI) | Test coverage | With `ML_STUBS=true` and no LLM key, several features cannot be exercised end to end here (entities, tables, three-way match cases, radar flags, extraction review items, assistant answers) |
| F-064 | P3 | **fixed** (Phase 5, `53d9267`) | Config | `RATE_LIMIT_LOGIN_IP_PER_5MIN` and the other `RATE_LIMIT_*_PER_*` settings are never read; the limits are hard-coded in `policy.py` |
| F-065 | P2 | **fixed** (Phase 4, owner decision N-019 ERP part) | Roles / ERP | A MEMBER (workspace CONTRIBUTOR) may create ERP postings; the Phase 3 brief expected only admins/owners to |
| F-066 | P2 | **fixed** (Phase 4) | API stability | Seven other pypdfium2 call sites (redaction, tables, corroboration, OCR) have no PDFium lock; same class as F-050 |
| F-067 | P1 | **fixed** (Phase 4) | Document settings | Opening Document settings switched extraction off; verification could not be turned on |
| F-068 | P2 | **fixed** (Phase 4) | Verification | Verification agents were told every document was "Other" |
| F-069 | P1 | **fixed** (Phase 4) | Verification | A field the agents split on evenly was approved automatically |
| F-070 | P2 | **fixed** (Phase 4) | Extraction memory | The nightly sweep crashed on values of different lengths |
| F-071 | P2 | **fixed** (Phase 4) | Extraction memory | The rule applied was not the one measured |
| F-072 | P2 | **built** (Phase 4) | Extraction memory | Learned layouts never filled an empty field |
| F-073 | P2 | **fixed** (Phase 4) | Cases | Cases ignored the model's classification |
| F-074 | P3 | **fixed** (Phase 4) | Tables | Table exports dropped printed decimals |
| F-075 | P2 | **fixed** (Phase 4) | Radar | A supplier's next monthly invoice was flagged as a duplicate |
| F-076 | P2 | **fixed** (Phase 4) | Workflows | A new workspace could not build data conditions |
| F-077 | P2 | **fixed** (Phase 4) | Clause checks | A saved clause check stayed switched off |
| F-078 | P1 | **fixed** (Phase 4) | Team | A refused role change hung the request and locked the organization |
| F-079 | P1 | **fixed** (Phase 4) | SLO | No SLO measurement could be recorded |
| F-080 | P3 | **built** (Phase 4; e2e drives it since Phase 5, `5a21282`) | Webhooks | "Send test event" |
| F-081 | P1 | **fixed** (Phase 4) | Billing | Voided invoice could not be corrected; retried invoice job crashed |
| F-082 | P2 | **fixed** (Phase 4) | Billing | Creating a billing account froze the organization while Stripe answered |
| F-083 | P2 | **fixed** (Phase 4) | Public API | No rate-limit headers; body said "FREE, 0 left" |
| F-084 | P1 | **fixed** (Phase 4) | Workflows | A security violation inside an action did not stop the rule |
| F-085 | P3 | **fixed** (Phase 4) | Verification | Resolving a settled verification answered "not found" |
| F-086 | P2 | **fixed** (Phase 4) | Tenancy | Radar and erasure read chunks without the workspace filter |
| F-087 | P2 | **fixed** (Phase 4) | Metering | A retried document could be refused at the ceiling for tokens already paid |
| F-088 | P1 | **fixed** (Phase 4) | Uploads | A tiny image-bomb avatar could exhaust memory |
| F-089 | P2 | **fixed** (Phase 4) | SCIM | SCIM failed with 500 when the caller's address was not an IP |
| F-090 | P2 | **fixed** (Phase 4) | BYOK | Saving a route before its key answered 500 |
| F-091 | P3 | **built** (Phase 4) | Search | Global search finds documents, entities and cases |
| F-092 | P3 | **built** (Phase 4) | Review | Page evidence with each agent's reading highlighted |
| F-093 | P2 | **built** (Phase 4) | Radar | Bank-account-changed and round-total flags |
| F-094 | P2 | **fixed** (Phase 4; the rest in F-099) | Tests | ~230 tests red on main for stale reasons |
| F-095 | P2 | **fixed** (Phase 5, `24a0fb2`, `ea13d14`, N-021) | Plan gating | BYOK was writable on every plan |
| F-096 | P3 | **closed by N-022** (Phase 5, `3864de5`) | Auth | Locked-out sign-in answers like a wrong password |
| F-097 | P3 | **closed by N-023** (Phase 5, `41e8f99`) | Uploads | Oversized avatars are shrunk, image bombs refused |
| F-098 | P3 | **closed by N-024** (Phase 5, `23e1c69`) | Public API | Token links under `/api/v1/public` stay |
| F-099 | P3 | **fixed** (Phase 5, 7 commits) | Tests | Remaining stale engineering invariants |
| F-100 | P2 | **fixed** (Phase 5, `af6d9c8`) | Logging | Running migrations in-process silenced every app logger |
| F-101 | P3 | **fixed** (Phase 5, `306fc51`) | Auth | A session-less token minted in the revocation second outlived it |
| F-102 | P2 | **fixed** (Phase 5, `deb494c`) | SLO | One unknown organization discarded every SLO measurement in a flush |
| F-103 | P2 | **fixed** (Phase 5, `ad411ec`) | Schema | Deleting a user row cascaded to the file records of their uploads |
| F-104 | P2 | **fixed** (Phase 5, `f80b517`) | Warehouse sync | Snowflake could not sign with a passphrase-protected key |
| F-105 | P2 | **fixed** (Phase 5, `3b83fc8`) | Documents | PDF pages were freed by the garbage collector outside the PDFium lock |
| F-106 | P1 | **fixed** (Phase 5, `8a4253a`) | Email | A client that hung up before the reply was written silently cancelled the request's verification, reset or invitation email |
| F-107 | P2 | **fixed** (Phase 5, `8541f0a`) | Invitations | Every invitee landed on "That workspace is no longer available to you" after accepting |
| F-108 | P1 | **fixed** (final release, `2d60e40`) | Compliance | A legal hold did not stop single delete, the purge sweep or GDPR erasure; bulk delete let a member delete other people's documents |
| F-109 | P2 | **fixed** (final release, `6ce327e`) | Backups | The nightly backup verify could fail on a closed pipe ("bad decrypt") although the backup was good |
| F-110 | P3 | **fixed** (final release, `e3dc770`) | Notifications | Organization notifications: "Next page" showed the same page (offset not in the cache key) |
| F-111 | P2 | **fixed** (final release, `b44fddd`) | Review hub | Every extraction review item said "Agents disagreed", including calibration holds, audit samples and memory trials |
| F-112 | P2 | **fixed** (final release, `975d919`) | Assistant | A question typed while "New" was creating a conversation went to the previous conversation |
| F-113 | P1 | **fixed** (final release, `6465b6d`) | Enrichment | The first document of a new workspace skipped its AI steps (no provider default yet) |
| F-114 | P3 | **fixed** (final release, `c12c9f1`) | Logging | Log lines dropped their `extra=` context; no structured (JSON) option |
| F-115 | P2 | **fixed** (final release, `cec4b18`) | Radar | A goods receipt was flagged as a "duplicate" of the purchase order it quotes |
| F-116 | P3 | **fixed** (final release, `66130a3`) | Review hub | A tab clicked while the hub was loading was lost |
| F-117 | P2 | **fixed** (final release, `19aac28`; sign-in page proven by a browser test, the step-up dialog path unverified: no test can open it) | Auth / UX | A wrong password in the "Confirm it's you" dialog signed the user out |
| F-118 | P2 | **fixed** (final release, `34def1f`) | Sign-up | A refused sign-up still switched to "Check your email" |
| F-119 | P2 | **fixed** (final release, `f48235b`, `7242b3d`) | Auth (ASVS V2.1) | Any 8-character password was accepted, "Password123!" included; no strength meter |
| F-120 | P2 | **fixed** (final release, `01a4bfa`) | Sessions (ASVS V3.3.2) | A session refreshed daily never required signing in again |
| F-121 | P2 | **fixed** (final release, `2a8bb7c`) | Frontend | Every page was thrown away and rebuilt when the user's profile arrived, losing what had just been typed or selected |
| F-122 | P3 | **fixed** (final release, `c634ee1`) | API (ASVS V8.2.1) | API responses carried no `Cache-Control`, leaving tenant data to browser caching heuristics |
| F-123 | P2 | **fixed** (production config & UI, `c1068be`) | SSO / config | SAML and OIDC advertised `http://localhost:8000` and an empty redirect URI on every deployment; the settings meant to fix it were ignored |
| F-124 | P3 | **unverified** (code reading) | SCIM | The SCIM token HMAC falls back to `str(JWT_SECRET_KEY)`, which for a `SecretStr` is the constant `**********`, not the secret |
| F-125 | P2 | **open, owner action** (production config & UI) | Billing config | The production webhook secret was the `stripe listen` CLI secret, so every Stripe event to the server would be refused |
| F-126 | P3 | **fixed** (final systemic polish, `cbcf88e`; proven live) | Sessions | Two refreshes of one token within the reuse grace revoke the session the first one just handed out, so that page's next request is refused once; every refresh in one tab cost the other tab a 401 |
| F-127 | P3 | **fixed** (final systemic polish, `dcc6f57`, `d68d215`) | Review queue / API | Lists paged by offset sorted on non-unique keys (18 of them, the extraction queue first): rows sharing a timestamp moved between pages; the hub stayed on an emptied last page; the browser test depended on how many runs the database had seen |
| F-128 | P2 | **fixed** (final systemic polish, `5e6f997`; proven live) | Knowledge base | "Reindex knowledge base": every job crashed with `SpendLimitMisconfiguredError: 'embedding.backfill_token' is neither the wildcard '*' nor a billable usage event type` |
| F-129 | P3 | **fixed** (final systemic polish, `695e40a`) | Knowledge base | After the first reindex (or the crash) every later click answered "Queued N" and queued nothing |
| F-130 | P2 | **fixed** (final systemic polish, `dfbd797`) | Auth (ASVS V3.3.1) | "Sign out" never ended the session on the server: the refresh cookie's path kept it from reaching `/auth/logout` |
| F-131 | P2 | **fixed** (final systemic polish, `e7fc1ec`, `fae4a50`) | Tenancy | An archived workspace could never be restored (the restore route refused archived workspaces) and stayed in members' switchers |
| F-132 | P3 | **fixed** (final systemic polish, `e7fc1ec`, `fae4a50`) | Tenancy | An archived organization could not be restored at all, though archive is documented as reversible |
| F-133 | P3 | **fixed** (final systemic polish, `d3bd89a`, `e7fc1ec`) | Tenancy | Sign-in could land in an archived organization ("no access") while the person had a working one |
| F-134 | P2 | **fixed** (final systemic polish, `493efb7`) | Workers | Archived tenants kept their background work: billable nightly sweeps, warehouse exports of their data, queued automations and ERP deliveries |
| F-135 | P3 | **fixed** (final systemic polish, `22528cd`) | Theme | The toggle did nothing on its first click from "system" on a light computer; dark-mode pages flashed white on every load |
| F-136 | P3 | **fixed** (final systemic polish, `9e25b07`) | Profile | A new profile picture (or logo) was replaced by the old one after a reload or on another device |
| F-137 | P3 | **fixed** (final systemic polish, `ca3b053`) | Accessibility | Eleven dialogs ignored Escape and let Tab leave them; two inline panels were marked modal |
| F-138 | P3 | **fixed** (final systemic polish, `e72a65a`) | Compliance | A DPA/GDPR export bundle could not be downloaded where storage cannot presign URLs (local disk) |
| F-139 | P3 | **fixed** (final systemic polish, `9567cc3`) | Roles / UX | The invite form offered "Admin" to organization admins, who may not grant it; they were refused only on submit |
| F-140 | P3 | **fixed** (final systemic polish) | Run history | Every execution came back with `rule_name` null, so Run history could only say "Rule 1f3a9c0e" |
| F-141 | P2 | **fixed** (live feedback, `ac12570`) | Knowledge base / UX | "Reindex knowledge base" gave no feedback after the 202: button always enabled, nothing said whether re-embedding ran, finished or failed |
| F-142 | P3 | **fixed** (live feedback, `42d3f5b`) | Tenancy / UX | "Your workspace access" listed a workspace of an archived organization like an active one and dropped grants on archived workspaces |
| F-143 | P3 | **fixed** (live feedback, `a0ee2e7`, page images `15c971e`) | Profile / branding | An undecodable profile picture or logo showed a broken-image icon; the profile page refetched it in a loop (`?v=1155` within seconds) |
| F-144 | P2 | **fixed** (live feedback, `5b91104`) | Frontend resilience | One page's render error (or a deploy while a tab was open) replaced the whole app; "Retry" re-rendered the same crash |
| F-145 | P3 | **fixed** (live feedback, `19b012d`; browser test for the bulk path in `6346f92`) | UX | 20 mutations (bulk actions, domains, IdP, presets, SLO reset, analytics schedules, calendars, …) gave no feedback on failure, some none on success |
| F-146 | P3 | **fixed** (live feedback, `a9f1e39`) | Automation / workers | `reap_stranded` (times out runs stranded in RUNNING) existed and was tested but never scheduled |
| F-147 | P3 | **fixed** (live feedback, `57ad3c4`) | Notifications / workers | `sweep_due_deliveries` (re-enqueues an email whose job died) was documented as scheduled and never called |
| F-148 | P3 | **fixed** (live feedback, `9ac4681`) | Members / UX | An expired organization invitation was listed as "Pending … expires <a past date>" |
| F-149 | P2 | **fixed** (live feedback, `6346f92`) | Documents | The bulk API (delete, reprocess, export, tag) and its action bar existed but nothing mounted the bar: bulk actions were unreachable; viewers were offered Delete/Retry the server refuses |
| F-150 | P2 | **fixed** (live feedback, `078b21f`) | Automation / database | Deleting an automation rule that had ever run failed with a foreign-key violation (HTTP 500) |
| F-151 | P2 | **fixed** (live feedback, `65e773f`) | Documents / privacy | Deleting a document left its original file in storage with a live `uploaded_files` record, though the dialog promised permanent deletion |
| F-152 | P2 | **fixed** (live feedback, `d628560`) | Compliance (GDPR Art. 17) | A subject erasure listed the subject's stored files as "orphaned" and never deleted them |
| F-153 | P3 | **fixed** (live feedback, `2a6a899`) | Members / UX | Organization → Members showed bare email addresses: the display name the API sent was unused and there was no way to show a picture without a 404 per member |
| F-154 | P3 | **fixed** (live feedback, `c857fa0`) | UX | Every browser tab and history entry was titled "FlowPilot AI": open tabs could not be told apart |

---

## F-001 — CI on `main` fails instantly with zero jobs (P1, fixed in Phase 1)

**Fixed.** Root cause: `ci.yml:297` used a double-quoted string inside a
`${{ }}` expression (`join(needs.*.result, ",")`). GitHub accepts only
single-quoted strings there and rejected the whole file. actionlint reported
the lex error before the fix and nothing after it, and run 98 on PR #2 was
the first run in the repository's history to create jobs. The 44 encodings
were normalised in their own commit. Encoding and Frontend then passed on
GitHub.

**Plain language.** The automatic checks that should run on every push to `main`
are not running at all. Every one of the last 5 runs ended as "failure" in the
same second it started, and GitHub created **no jobs** for any of them. So for
now nothing automatically stops a broken change from landing on `main`.

**Evidence.** GitHub Actions runs 36674718556, 36673514318, 36671244692,
36586037060, 36582994316 (all `conclusion: failure`, `jobs: []`). The run
title shows the file path `.github/workflows/ci.yml` instead of the workflow's
name `CI`, which is what GitHub does when it rejects the workflow file before
starting it. The YAML itself parses locally. Likely causes (unverified):
a workflow feature GitHub rejects, or Actions being disabled or out of minutes
for the account.

**Second layer (confirmed locally).** Even once GitHub starts the jobs, the
first job, `encoding`, which every other job waits on, would fail.
`python3 backend/scripts/normalize_encodings.py --check` exits 1 on this
commit: `backend/requirements.txt` is UTF-16, and 43 files start with a UTF-8
BOM (e.g. `frontend/src/services/api/client.ts`, `frontend/src/types/tenancy.ts`).
The script offers `--apply`. That is a mechanical fix for Phase 1, one commit.

**Next step.** Phase 1: open the run page on GitHub for the error text, fix
the workflow, fix the encodings, and prove a green run on the baseline branch.

## F-002 — Large parts of the product have no automated tests (P1, confirmed by static count)

**Plain language.** There are 131 backend test files, but none of them calls
the APIs of the newest ~20 features. Those include every locked
"premium" feature, the billing webhooks and the Dodo gateway. The frontend has
no test files at all. The `verify_arch*.py` scripts that "certified" these
features read the source code. They do not run the product, so they cannot
show that a feature works.

**Evidence.** `grep -rl` over `backend/tests` for each API path returns 0
files for: `/procurement`, `/redactions`, `/assertions`, `/anomalies`,
`/autonomy`, `/extraction-memory`, `/entities`, `/packet-splits`, `/cases`,
`/tables`, `/corroboration`, `/obligations`, `/erp/`, `/review`, `/process`,
`/egress`, `/admin/sovereign`, `/admin/revops`, `/billing/webhooks`, and
`dodo_gateway`. `find frontend/src -name "*.test.ts*"` returns 0 files.
The ledger tags the affected endpoint rows with `F-002`.

**Next step.** Phase 3 builds the harness. Until then these rows stay `untested`.

## F-003 — Default secrets are hard-coded in the app config (P2, fixed in Phase 2)

**Plain language.** Three secrets have a default value written into the code:
`API_KEY_PEPPER`, `REDIS_IDENTITY_PEPPER` and `EMAIL_ENCRYPTION_KEYS`. The
repository is public, so anyone can read these values. If the server starts
without these variables set, it quietly uses the public values. Nothing refuses
to start. Any secret the server encrypts at rest would then be encrypted with a
key everyone knows: tenant SMTP passwords, webhook signing secrets, BYOK
provider keys. API-key hashes would be peppered with a known value.

**Evidence.** `backend/app/core/config.py:91-92` (peppers) and `:148`
(a valid-looking Fernet key). `_resolve_encryption_keys` (`:883`) only raises
when the value is empty, and the default is never empty. No validator checks
the peppers. Mitigation today: `docker-compose.prod.yml:55-58` uses `${VAR:?}`
and refuses to start without them, but only when you deploy with that exact
compose file. There is one more gap: `.env.production.template` ships
`API_KEY_PEPPER=` blank. A blank value overrides the default, which would give
an empty pepper with no error.

**Fixed (Phase 2).** Proof of the bug and the fix on the same input
(`ENVIRONMENT=production`, `CORS_ORIGINS=*`, `LOG_LEVEL=DEBUG`,
`POSTGRES_PASSWORD=postgres`, `STORAGE_BACKEND=local`, rate limit off): the code
on `main` **booted**, using the public pepper and the public email key; the new
code refuses with 12 named problems. What changed:

- `app/core/production_guard.py` (new) and one validator in `Settings`. In
  `production` **and `staging`** the app refuses to start and lists every
  problem at once, by variable name, never by value: missing, blank, short,
  repetitive, placeholder or repository-published secrets (`API_KEY_PEPPER`,
  `REDIS_IDENTITY_PEPPER`, `EMAIL_ENCRYPTION_KEYS`, `JWT_SECRET_KEY`); the same
  value reused for two secrets; a default or trivial `POSTGRES_PASSWORD`;
  wildcard, non-https or localhost CORS; a non-https `FRONTEND_URL`; debug
  logging; rate limiting, login back-off, SAML wrapping defence or LLM
  metering switched off; an in-memory rate limiter; `STORAGE_BACKEND=local` in
  production; the public MinIO credentials; no `REDIS_URL`; no SMTP host
  (invitations and password resets would silently never arrive); a reranker
  without its token; Stripe or Dodo keys without their webhook secret, a
  wrong-mode publishable key, and live payment mode in staging.
- The three public defaults are **gone from `config.py`**. Development and
  test derive private values from the checkout's own `JWT_SECRET_KEY`, so
  nothing public can be the key and API keys survive a restart.
- `ENVIRONMENT` is normalised (`Production`, `prod`) and a misspelling is
  refused. Before, `ENVIRONMENT=Production` silently switched off every check
  that compares against the literal `"production"` (API-key prefix, SSRF
  production rules, internal-TLS rule, SFTP host checks).
- The refusal is its own exception, because a pydantic `ValidationError`
  prints the whole input dictionary, which here holds every secret.
- `.env.production.template` now names every variable the compose file and the
  guard require (it lacked `REDIS_URL`, the AI provider keys, session and
  upload limits and the migration flag) and a test proves it: copied as-is it
  is refused, with secrets filled in it boots, and it ships no usable secret.

Tests: `tests/core/test_production_config_guard.py` (72) and
`tests/infra/test_production_env_template.py` (24) pass. The
existing ML-stub, encryption-boundary and config tests pass; the only failures
in the neighbouring test files are ones already in the Phase 1 baseline.

## F-004 — Server-side plan gating gaps to prove (P2, fixed in Phase 2; see the resolution below)

**Plain language.** A locked item in the sidebar only hides a feature. The
server has to refuse the request too, or someone on a lower plan can call the
API directly and get a paid feature free. Most premium endpoints do call the
plan check (194 of them). The ones below do not, even though their page is
locked in the sidebar:

- `developer.py` (Developer platform, sidebar-locked by `developer_api`): 0 of
  6 handlers call a capability check. A code comment says the plan ceiling is
  enforced inside the service. Needs a test.
- `identity_admin.py` (Enterprise identity, Enterprise only):
  `PUT /identity/security-policy` and `POST /identity/domains/{id}/verify`
  have no check. The other 7 ungated handlers there are reads or deletes.
- `review_collab.py` (collaborative review, Enterprise only):
  `GET /review/collab/state` and the live WebSocket have no check.
- Reads and deletes in several gated modules have no check (API keys,
  webhooks, analytics, custom domains, branding, email settings). This is
  probably deliberate, so that a customer who downgrades can still see and
  delete what they built. **Product decision → NEEDS-OWNER N-003.**

**Evidence.** The static extraction of all 555 handlers (the `plan_required`
column in COVERAGE.csv). Rows marked `UNGATED in a <plan>-plan module`.

**Next step.** Phase 2: call each of these with a Free-plan token and expect 402/403.

## F-005 — Sidebar lock state does not match server enforcement (P3, fixed in Phase 5)
- "Analytics & BI egress" shows **no** lock, but every write behind it needs the
  `addon.warehouse_sync` grant (Business and above). A Free or Developer tenant
  sees an open page whose buttons fail.
- "Enterprise BYOK & models", "Partner marketplace", "Service levels" and
  "Data governance" have no plan gating anywhere (neither UI nor API). That may
  be intended. **Owner decision → N-002.**

## F-006 — Critical sweeps and backups depend on host cron (P2, fixed for Docker Compose)

**Plain language.** Some important jobs are not started by the app or its
containers. They are started by a Linux "cron" file that must be installed on
the server by hand (`backend/deploy/cron.d/`). These are the data-retention and
erasure sweep (`compliance`), obligation reminders, ERP posting retries,
invitation and identity sweeps, and **the database backup and the restore
drill**. `docker-compose.prod.yml` does not run cron. If you deploy only with
containers, these jobs never run, and nothing tells you. Your retention
promises break silently and you have no backups.

**Why the existing files could not work on your VPS.** Both scripts assumed a
Python virtual environment and Postgres client tools installed *on the server*.
A Docker Compose VPS has neither: Python and Postgres live in containers.

**What changed (Phase 2).**

- `deploy/bin/flowpilot-sweep` accepts `FLOWPILOT_RUNNER`. Set it (in
  `/etc/flowpilot/sweepers.env`) to a `docker compose ... run --rm ... python`
  command and every sweeper runs inside the stack's own image. Unset, the wrapper
  behaves exactly as before.
- New `deploy/bin/flowpilot-compose-backup`: `pg_dump` inside the `db`
  container (so the client always matches the server), AES-256 encryption, a
  read-back **verification** (decrypt, then `pg_restore --list`; a backup that
  cannot be read back is deleted, not kept), a checksum, 7 daily + 4 weekly
  retention, and an optional off-host mirror command.
- New `deploy/bin/flowpilot-compose-restore-drill`: restores the newest backup
  into a scratch database, compares table row counts and the Alembic head with
  the live one, times it, and drops the scratch database.
- New `deploy/cron.d/flowpilot-compose-backups`: the schedule (nightly backup,
  weekly drill). Install it *instead of* `flowpilot-backups`.
- Both scripts can report to a dead-man's-switch monitor (a free Healthchecks.io check
  works; `HEARTBEAT_BASE` plus one id per job): success pings `<base>/<id>`, failure
  pings `<base>/<id>/fail`, and a run that never happens is the monitor's alarm. Cron
  output goes to a log file, not nowhere (`MAILTO` is empty in these files, so anything
  printed and not logged was lost). A monitor that cannot be reached never fails a backup.
- `docs/RUNBOOK.md` gained "First deploy", "Backups" and "Scheduled jobs".

**Evidence.** `tests/infra/test_compose_backup_scripts.py` (20 tests) runs the
scripts against a real PostgreSQL with real `pg_dump`, `pg_restore` and
`openssl`: a backup is encrypted, checksummed and readable only with the key; a
missing or empty key stops the run and writes nothing; a failing dump keeps
nothing; the restore drill restores into a scratch database and drops it; the
drill fails on a corrupted backup and when a core table comes back empty;
retention keeps 7 daily + 4 weekly; a failed mirror is reported while the local
backup stays; the mirror command receives the backup path; a sweep runs through
the configured container runner and a failing sweep is recorded as a failure;
every cron entry names a sweeper the wrapper knows; the compose cron file
schedules a nightly backup and a weekly drill; success, a failed dump, a missing
key, a failed mirror and a failed drill each ping the right monitor address (5 of the
7 monitor tests failed before the change; the other 2 are no-monitor and
unreachable-monitor controls). A mutation check (encryption removed from the
script) makes the encryption test fail.

**Not verified.** A real `docker compose exec` / `run` against the production
stack: this session has no Docker daemon that can pull images (Docker Hub rate
limit), so the container-side commands were exercised through the
`FLOWPILOT_PG_EXEC` and `FLOWPILOT_RUNNER` prefixes with a native Postgres, not
through Docker itself. The first run on the VPS must be watched (RUNBOOK,
"First deploy", step 6).

**Still open.** The three point-in-time-recovery entries in
`flowpilot-sweepers` (`dr-heartbeat`, `base-backup`, `pitr-drill`) need
PostgreSQL WAL archiving configured; a Compose deployment does not have it. The
RUNBOOK tells you to leave those three lines commented out. Point-in-time
recovery is an owner decision (RPO wanted: today the newest nightly backup, up
to 24 hours of data) → N-012. Also unverified: whether anything enqueues the job
types `billing.reconcile`, `billing.assemble_invoice`, `usage.reconcile` and
`billing.seat_sync` on a schedule (apart from scripts and gateway code paths).

## F-007 — Repository hygiene (P3, partly fixed in Phase 2)

- `backend/stripe.exe` (38 MB Stripe CLI binary) was committed in `d790047`
  and deleted in `1884891`. It is gone from the current tree but **still in git
  history**, so every clone downloads it. Removing it needs a history rewrite
  (force-push), which is the owner's call → N-005.
- `README.md` and `LICENSE` are empty (0 bytes). A public repo with no license
  is legally "all rights reserved". Choosing a license is the owner's call → N-004.
- `backend/requirements.txt` is UTF-16LE with CRLF line endings. It is a full
  Windows `pip freeze` (includes `git-filter-repo`, `colorama`, both
  `google-generativeai` and `google-genai`, and both torch and paddle stacks).
  pip reads UTF-16 with a BOM, but other tools (pip-audit, Dependabot, grep) may
  not. `requirements-dev.txt` contains only `pytest-env`.
- Historical one-off scripts: 5 `apply_hardening_*.py` + 20 `run_arch*.ps1` /
  `run_hardening*.ps1` at the root; 27 `apply_arch*.py` (up to 4 MB each) and
  30 `verify_*.py` in `backend/`; about 150 `verify_*`, `patch_*`, `fix_*`,
  `mutate_*` and `restore_*` scripts in `backend/scripts/`. Per CLAUDE.md these
  are not read or edited. Phase 2 moves them to `archive/` only if nothing
  imports or runs them. The CI job `backend-gates` runs
  `scripts/run_all_gates.py`, which probably runs many `verify_*` scripts, so
  most cannot be moved blindly.
- Large tracked files: `frontend/public/flowpilot-logo.png` and `favicon.png`
  are 1.37 MB each. That is heavy for a favicon.

**Phase 2 status.**

- *`stripe.exe`*: still in history (N-005: no force-push without your say-so). The root
  `.gitignore` now blocks `*.exe`, `*.msi`, `*.dll`, `*.bin`, `*.dmp` and key material
  (`*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`). `git ls-files` shows no
  tracked binary or key file today. **Unfixed**: the clone still downloads 36 MB.
- *README and LICENSE*: the README was written in Phase 1. `LICENSE` is now the
  proprietary, all-rights-reserved notice you chose (N-004), with "FlowPilot AI" as the
  holder until you give the legal name. Have a lawyer read it before you rely on it.
- *`requirements.txt`*: already plain ASCII since Phase 1. Its contents are unchanged
  (F-045 covers the size problem).
- *Historical scripts*: **not archived, because they are not unused.** 37 tracked
  `apply_*.py` are named by 14 of your PowerShell runners (`run_arch*.ps1`,
  `run_hardening*.ps1`), and the 107 tracked `verify_*.py` are executed by CI's
  `backend-gates` job through `scripts/run_all_gates.py`. Moving either would break your
  Windows workflow or the gate job. I did not read or edit them (CLAUDE.md, N-010).
  Retiring them is your decision → N-016.
- *Logo images*: unchanged (Phase 4).

## F-008 — Frontend route guards (P3, fixed in Phase 5)
`frontend/src/routes/RequireWorkspaceRole.tsx` is defined but never used. Under
`/organizations/:orgSlug/*`, `OrganizationGuard` checks membership and archived
status but not role. Pages such as Audit log, Service levels, Marketplace and
Autonomy rely on in-page checks or on the API returning 403. A MEMBER who
deep-links to `/organizations/x/audit` may see an error or an empty state
instead of a clear permission screen. The server still refuses the data, so
this is not a leak. Phase 3 renders every page as a forbidden role.

## F-009 — `/admin` has no index route (P3, fixed in Phase 5)
The platform shell `/admin` has only `margins`, `sovereign` and `revops`
children and no index route. A super admin who opens `/admin` probably sees
the platform layout with an empty body.

## F-010 — Frontend env template is wrong (P3, fixed in Phase 5)
`frontend/.env.example` lists `VITE_APP_NAME`, `VITE_APP_VERSION` and
`VITE_ENVIRONMENT`, and nothing in `src/` reads them. The only variable the code
reads, `VITE_API_URL` (`services/api/client.ts`), is missing from the template.

## F-011 — Most settings are undocumented for production (P3, partly fixed in Phase 2)

`backend/app/core/config.py` defines 302 settings. 246 of them are not in
`.env.production.template`, so their code defaults apply silently in
production. Defaults to review in Phase 2: `POSTGRES_PASSWORD='postgres'`,
`S3_DEV_FALLBACK_CREDENTIALS=True`, `SMTP_ALLOW_PRIVATE_IN_DEVELOPMENT=True`,
`ENVIRONMENT='development'` (the app starts in development mode if the variable
is forgotten). Each has a ledger row (`config_var`).

**Phase 2 status.**

- `POSTGRES_PASSWORD='postgres'`: production refuses a weak or default database password
  (F-003, tests in `tests/core/test_production_config_guard.py`).
- `ENVIRONMENT='development'` when forgotten: the production compose file sets
  `ENVIRONMENT: production`, the spelling is normalised (`Production`, `prod`), and the
  guard treats `production` and `staging` alike.
- `S3_DEV_FALLBACK_CREDENTIALS=True` and `SMTP_ALLOW_PRIVATE_IN_DEVELOPMENT=True`: read
  from the code, both apply only when the environment is development
  (`config.py:1132`, `email_service.py:65`), so they are inert in production. No test pins
  this yet.
- The template now names every variable the compose file requires and the start-up guard
  checks, and F-047 made sure everything in it reaches the containers.
- **Still open:** about 230 other settings keep their code defaults and are not in the
  template. I did not review each one for an unsafe default; the ones that matter for
  security are covered by the guard (secrets, CORS, debug logging, protections that must
  stay on, storage, Redis, mail, billing consistency).

## F-012 — WebSocket token in URL (P3, not a defect; Phase 2)

`app/services/collab/gate.py:extract_token` reads the bearer token from
headers **or** from the query parameters `token`, `access_token`, `bearer` or
`auth`. Tokens in URLs end up in proxy and access logs. Check whether the query
form is actually used, and that logs redact it.

**Phase 2 result: not a defect.** The code only names those four parameters in order to
*refuse* them: a token in the query string raises `LiveRefused` and the handshake is closed
(1008) without being accepted. The token travels in the WebSocket subprotocol list (what a
browser can set) or an `Authorization` header, and API keys are refused outright.
`tests/security/test_live_review_token_not_in_url.py` (13 tests) pins this; a mutation that
accepts a query-string token makes 8 of them fail. The Caddy access log also drops the
`Sec-Websocket-Protocol` header where the token rides.

## F-013 — Three billing webhook entry points (P3, verified in Phase 2)

Stripe events can arrive at `POST /api/v1/billing/webhooks/stripe` and
`POST /api/v1/billing/stripe/webhook` (both are the same handler in
`billing_webhook.py`), and at the generic `POST /api/v1/billing/webhooks/{gateway}`
(`billing_webhook_multi.py`). Stripe-specific wins for the literal path because
it is registered first. Phase 2 must prove, for every path, that there are no
unsigned or replayed events, that events are idempotent, and that out-of-order
events are handled.

## F-014 — Organization slug `request` collides with a public route (P3, fixed in Phase 5)
`/request/:token` is the public document-request upload page. Workspace URLs
are `/:orgSlug/:workspaceSlug`. The backend reserved-slug list
(`app/core/slugs.py`) does not include `request`. An organization named
`request` would have every workspace URL shadowed by the public upload page.

## F-015 — Sidebar and API disagree on ADMIN access (P3, fixed in Phase 5)
The sidebar shows Webhooks, Audit log, API keys and Enterprise identity only to
OWNER, while several of those endpoints accept `OWNER,ADMIN` (see
`roles_allowed` in the ledger). The most important case: **an ADMIN can create,
rotate and delete organization API keys through the API**
(`api_keys.py:53-174`, all `Depends(deps.RequireOrgAdmin)`). The comment in
`frontend/src/components/layout/navigation.ts` describes API keys as the
OWNER-only surface. Webhook endpoint management and audit-log read/export also
accept ADMIN. This is not a cross-tenant leak, but the UI and API disagree about
who may mint credentials. The owner should decide which one is right → N-006.
If OWNER-only is intended, this becomes a P2 authorization bug.

**Owner decision (2026-09-30):** OWNER and ADMIN may both create API keys. The
API is therefore right for API keys, and the sidebar is wrong to hide API keys
from ADMIN. Phase 4 makes the sidebar show API keys to ADMIN, with a test.
Webhooks, audit log and enterprise identity were not part of the decision and
stay as they are.

---

# Phase 1 findings (2026-09-30)

## F-016 — The backend test suite is red (P1, fixed in Phase 5)
**Plain language.** There are 2,546 backend tests (2,538 before Phase 1 plus 8
new stub tests). Run as CI runs them, but without stopping early,
2,285 passed, 219 failed, 33 errors, 9 skipped of 2,546. CI stops at the first 5 failures (`--maxfail=5`), so the
`pytest` job will stay red until these are worked through. Nothing in Phase 1
changed a test's expectations; the only test edit fixed an import path
(F-018 item 9).

**What the failures are (grouped by first error line; details in
`01-baseline.md`):**
- **Plan gating newer than the tests (≈40).** The API now answers
  `ADDON_REQUIRED` or `CAPABILITY_REQUIRED` (custom domains, warehouse sync,
  custom email, branding), but the tests' tenant is on a plan without those
  add-ons. Each needs a decision: fix the test fixture's plan, or treat it as
  a real regression.
- **API drift between code and tests (≈20).** Constructors changed signature
  (`AISettings(input_cost_per_1k_tokens=…)`, `LLMReservation(input_cost_per_1k=…)`,
  `FactSet(_facts=…)`).
- **Missing seed data in the test DB (≈50).** No published quota tier or price
  book is "in force" when a test needs one. The per-test TRUNCATE wipes them.
- **Order and harness dependence (≈25).** `relation "users" does not exist` in
  tests that use a separately named database, depending on what ran before.
- **Real behaviour gaps.** F-021 (secret echo) and SLO measurements written
  with a NULL `observed_value`. Automation timeouts report a different reason
  than the tests expect. Two IntegrityErrors that should be raised are not.
- **Sandbox only (4).** Model downloads blocked by the network (these pass
  where Hugging Face is reachable, as on GitHub runners).

**Next step.** Phase 3/4: triage each group. Fix the harness (seed data,
database naming) first, because it hides the real failures.

## F-017 — Model/migration drift; the drift gate could never fail (P2, real differences fixed in Phase 5)
**Plain language.** The database built by the migrations and the database the
Python models describe are not the same. `alembic revision --autogenerate`
proposes **314 operations**: 292 are indexes and constraints that migrations
created but the models do not declare (harmless at runtime, but autogenerate
would drop them). The other 22 matter more:
- table `organization_addons` exists but has no registered model (F-025);
- columns in the DB but not the model: `invoices.gateway`,
  `invoices.gateway_invoice_id`, `dunning_actions.gateway_event_id`,
  `invoice_line_items.created_at`;
- type mismatches: `automation_rules.conditions/actions` JSON vs JSONB; role
  columns that are Postgres enums in the DB but `Text` in the model
  (`enterprise_idp_configs.jit_default_org_role`,
  `idp_role_mappings.organization_role`, `scim_groups.workspace_role`);
  `redaction_*` CHAR(64) vs String(64); `sessions.idp_session_index` TEXT vs
  String(512); nullability of `document_chunks.content_tsv` and
  `document_settings.intent_config`;
- unique constraints the model declares but the DB lacks:
  `uq_ad_node_version`, `uq_scim_api_keys_key_prefix`,
  `uq_sso_auth_requests_request_id`.

**Why nobody saw it.** CI grepped `alembic/versions/ci_drift_probe.py`, but
alembic names the probe `ci_drift_probe_ci_drift_probe.py`. The grep never
found the file and the step always printed "No drift."

**Phase 1 action (CI only).** `backend/scripts/check_migration_drift.py`
replaces the inline step, and the 314 operations are listed in
`backend/alembic/drift_baseline.txt`. CI now fails on any new drift and on
any listed line that no longer drifts, so the list can only shrink. The drift
itself is **not fixed**. Zero drift means an empty baseline file.

## F-018 — A fresh clone could not install, migrate, test or start (P1, fixed)

Each item was proven failing, fixed in its own commit, and re-run.

1. **CI workflow rejected by GitHub:** see F-001.
2. **44 files with BOM/UTF-16:** see F-001.
3. **CI on Python 3.11, but `scipy==1.18.0` needs ≥ 3.12.** `pip install`
   failed ("No matching distribution"). CI now uses 3.12, matching
   `backend/Dockerfile`.
4. **CI set only `DATABASE_URL`, which the app ignores.** Settings builds the
   URI from `POSTGRES_*`, defaulting to `postgres:postgres`, while the CI
   database is `flowpilot:flowpilot`. CI now sets `POSTGRES_*`.
5. **`alembic upgrade head` refuses on every fresh database.** The last
   revision, `arch40_step3_contract_ai_settings`, raises unless
   `ARCH40_CONTRACT=1`. That blocked CI's migration job, the test suite's own
   setup, `start_dev.sh` and dev `docker compose up migrate`. Fixed for
   throwaway and dev databases only; production is F-024 / N-009.
6. **`start_dev.ps1` stopped 12 migrations short.** It upgraded to the pinned
   `arch40_step2a_review_view_paths`, so a fresh Windows setup had no tables
   for hm1 or ARCH-41..50. It now upgrades to head.
7. **`minio/minio` and `minio/mc` are gone from Docker Hub** ("object not
   found"), so `docker compose up` failed. Replaced by pinned `pgsty/minio` and
   `pgsty/mc` (a from-source rebuild of MinIO). → N-008.
8. **`run_all_gates.py` exited 2 before running any gate:** two gate names
   were neither `verify_arch<NN>` nor in `SPECIAL_ORDER`.
9. **A test imported a moved function** (`decrypt_password` from
   `app.core.smtp`). The collection error stopped the whole pytest run.
10. **The dev launchers never installed Python packages, never set
    `RERANKER_INTERNAL_TOKEN`** (compose refuses to start without it), **and
    never seeded plan tiers**: `seed_quota_tiers.py` refuses without gateway
    price ids, and both launchers reported success anyway.
11. **`./start_dev.sh` gave "Permission denied"** from a fresh clone: git
    stored it without the executable bit (mode 100644). Now 100755.
12. **`seed_price_book.py` crashed on the `.env` that `.env.example`
    produces.** Its own `.env` reader kept inline comments
    (`PLATFORM_SMTP_ENCRYPTION=TLS   # NONE | TLS | SSL`) and pushed them into
    the environment, so Settings validation failed. `reset_dev_environment.py`
    had the same reader. Both now use python-dotenv.
13. **`start_dev.sh` printed "(already seeded)" for any seed failure**, and
    `start_dev.ps1` printed `[ok]` regardless, so items 10 and 12 were
    invisible. Both launchers now show the failure.

**Fresh-clone proof (Linux).** `git clone` → `./start_dev.sh`, with no
`backend/.env`, `.venv` or Docker volumes present: all 8 steps `[ok]`, 1 price
book and 4 plan tiers seeded, `/api/v1/health` 200, and headless Chromium logs
in to the dashboard. A second run skips the package install, and both seed
scripts exit 0 when rerun. `start_dev.ps1` got the same changes but is
**unverified** (no Windows here).

## F-019 — OCR silently returns invented text on one error class (P1, fixed in Phase 2)

**Plain language.** If the PaddleOCR engine raises an error that mentions
`ConvertPirAttribute2RuntimeAttribute` or `onednn_instruction` (a known
Paddle/CPU problem), `PaddleOCRProvider._run_engine`
(`backend/app/services/ocr/paddle.py`) does not fail. It returns a made-up
result with the text **"FLOWPILOT GATE INVOICE 12345"**, and that text is then
extracted, embedded and shown as the customer's document content. It looks
like a shim added so a verification gate could pass on a machine where Paddle
crashed.

**Fixed (Phase 2).** Tests in `tests/services/test_paddle_ocr_honest_failure.py`
drive the real provider with an engine that raises the two real Paddle error
messages: 5 of 6 failed before (the provider returned the invented line), all
pass after. The shim is removed; `_run_engine` now raises `OCRError` that says
the page was **not** read and what to do, and logs `ocr.onednn_pir_crash` at
error level. A tripwire test fails if the invented string ever reappears in the
provider.

**Why it existed (read under owner decision N-010, gate is already red).**
`verify_arch10_step9.py` check G6.2 draws exactly that phrase on a PNG and asks
the pipeline to OCR it. The shim answered with the same phrase for *any* image,
so the gate could pass on a machine where Paddle crashes. That gate therefore
proved nothing on such machines. It is not edited (N-010). On a machine whose
Paddle works, or with `ML_STUBS=true`, G6.2 exercises the real path; on a
crashing machine it now fails, which is the honest result.

**Parallel session.** A different Claude session pushed its own fix for this to the same branch
(`04fa763`, with `tests/services/test_paddle_no_fabricated_text.py`, 7 tests). Both fixes make the
engine crash raise `OCRError`; the merge (`3b0aad1`) kept this branch's wording and both test files,
which pass together.

## F-020 — A chunk without a bounding box fails the whole document (P1, fixed in Phase 2)

**Plain language.** `DocumentChunk.bbox` is `mapped_column(JSONB,
nullable=True)` without `none_as_null=True`, so SQLAlchemy stores Python `None`
as the JSON value `null`, not SQL NULL (checked: `none_as_null = False`). The
table's check constraint `ck_document_chunks_bbox_is_object` allows only SQL
NULL or a JSON object, so the insert fails and the entire enrichment job
fails. A missing box is a normal outcome in the real pipeline: the PDF
text-layer fallback emits pages with `blocks=[]`, OCR blocks without a polygon
get `box=None`, and a chunk that overlaps no block gets no box.

**Evidence.** With the first OCR stub, whose blocks had no boxes, every upload
ended `FAILED` with `CheckViolation … ck_document_chunks_bbox_is_object`
(worker log, 10 occurrences). With boxes present, the same uploads reach
`COMPLETED`.

**Fixed (Phase 2), part 1: the chunk box.** The failing test
(`tests/services/test_chunk_writer_bbox.py`) calls the real writer
`replace_document_chunks` with a chunk that has no box and reproduced the exact
production error (`CheckViolation … ck_document_chunks_bbox_is_object`). The
column is now `JSONB(none_as_null=True)`, so a missing box is SQL NULL. Both
tests pass, and the 19 vector tenancy tests still pass. Part 2 (the same
mistake on 35 other nullable JSON columns) is the next commit below.

**Fixed (Phase 2), part 2: the sweep.** Introspecting the model metadata found
**35 more nullable JSON/JSONB columns** with the same default (for example
`audit_logs.details`, `usage_events.details`, `automation_rules.flow_spec`,
`jobs.result`, `work_items.extracted_entities`, `document_verification_fields.resolved_value`).
Three of them (`usage_events.details`, `automation_rules.flow_spec` and
`extracted_table_cells.bbox`) carry the same `IS NULL OR jsonb_typeof(...)` style of
constraint, so they would have failed the same way on the first `None`. All 35 now
use `none_as_null=True`. NOT NULL columns are left alone on purpose: there a JSON
null is a legal value. The ORM already reads SQL NULL and JSON null both as `None`
and no code queries for JSON null, so no behaviour is lost. The DDL is
identical, and the drift ratchet still reports the same 314 known operations.
A structural test (`tests/models/test_nullable_json_columns.py`) fails on any
future nullable JSON column that forgets the flag; it failed listing all 35
before the change.

**Parallel session.** The other session's fix (`e298702`, with `tests/services/test_chunk_writer_bbox_null.py`)
covered three of the columns (`document_chunks.bbox`, `usage_events.details`, `automation_rules.flow_spec`);
this branch covers all 35 nullable JSON columns. The merge (`3b0aad1`) kept this branch's comments and both
test files, which pass together.

## F-021 — A rejected BYOK API key is echoed in the error response (P2, fixed in Phase 2)

`PUT /organizations/{id}/byok/credentials` with an over-long key returns 422,
and the response body contains the full key under `"input"`. The existing
test `tests/api/test_byok_endpoints.py::TestCredentialConfidentiality::test_a_rejected_key_is_not_echoed`
fails for exactly this reason. FastAPI's default validation error handler
echoes input, and responses like this tend to be copied into logs, browser
devtools and error trackers.

**Fixed (Phase 2).** Root cause: FastAPI's default 422 handler returns pydantic's
error list, and every entry carries `input`, the value the caller sent.
`SecretStr` cannot prevent that, because the error is built from the raw input.
So the same leak applied to **every** field on every endpoint: passwords on
`/auth/register`, `/auth/reset-password` and `/auth/change-password`, reset
tokens, SMTP passwords, webhook secrets. A global `RequestValidationError`
handler (`app/core/exception_handlers.py`) now removes `input` from every 422
and keeps `type`, `loc` and `msg`, so no field needs to opt in. Proof: the
existing BYOK test plus 5 new tests in
`tests/security/test_validation_errors_do_not_echo_input.py`; 5 of them failed
before the change and all pass after. Nothing in the frontend or the tests read
`input`.

## F-022 — AI agent tool selector without tenant scope (P2, not a defect; Phase 2)

Gate `verify_arch0v.py` 0V-G13: `agent_selectors.py:resolve_review_item` is a
registered tool selector with no `tenant` parameter. ARCH-13 required every
selector to carry a `TenantScope`. Phase 2 must prove whether an agent in
tenant A can resolve a review item from tenant B.

## F-023 — Client IP read from `X-Forwarded-For` in five places (P2, not a defect; Phase 2)

Gate `verify_arch08_step6.py`: `core/client_ip.py`,
`services/identity/session_policy_service.py`, `api/v1/saml.py`,
`api/v1/byok.py` and `api/v1/scim.py` each read the header. Only the first
applies the `TRUSTED_PROXY_HOPS` rule. A client can forge the header to evade
IP-based rate limits, IP allow-lists or audit records. Phase 2: prove with a
forged header.

## F-024 — Production `migrate` fails on a fresh database (P2, mitigated in Phase 2)

`docker-compose.prod.yml` runs `alembic upgrade head` without
`ARCH40_CONTRACT`, so on a new production database the `migrate` container
exits with an error (F-018 item 5). Phase 1 left production unchanged on
purpose: when to run a lossy step on a real database is an owner decision →
N-009.

**Phase 2 status: mitigated.** The `migrate` service now reads `ARCH40_CONTRACT` from
the environment (default `0`), so production never runs the lossy step by accident and
staging can keep `1` (your N-009 answer for dev, test and staging). RUNBOOK section 9.2
step 6 is the one-time procedure: back up if there are customers, run
`ARCH40_CONTRACT=1 $COMPOSE run --rm migrate` once, leave the file at `0`. The step archives
the three dropped columns' values first, and its `downgrade` restores them. **Still open:**
you have not said whether a production database with real data exists (N-009), and the
procedure has not been run against real containers.

## F-025 — `organization_addon` model not registered (P3, fixed in Phase 5)
Gate `verify_arch20.py` G10: the model module with a table is not imported by
`app/models/__init__.py`. That is why autogenerate wants to drop the
`organization_addons` table (F-017), and why relationships or metadata that
rely on the registry can miss it.

## F-026 — Storage errors surface as a raw 500 on upload (P3, fixed in Phase 5)
When MinIO rejected the credentials, `POST /workspaces/{id}/work-items`
returned a bare 500 with an unhandled `StorageError` traceback in the log. A
storage outage should give the user a clear 503-style error and not leave a
half-created work item. (Seen live; the credential cause itself was a sandbox
artefact, F-027.)

## F-027 — Dev compose uses the shell's AWS key as the MinIO root user (P3, fixed in Phase 5)
`backend/docker-compose.yml` sets `MINIO_ROOT_USER: ${AWS_ACCESS_KEY_ID:-minioadmin}`.
Docker Compose prefers the shell's environment over `backend/.env`. A
developer with real AWS credentials exported gets a local MinIO whose root
user is their AWS key id, while the app uses `minioadmin` from `.env`:
uploads fail with `InvalidAccessKeyId`, and the real key id is copied into a
container. The RUNBOOK documents the workaround. Phase 2: use MinIO-specific
variable names.

## F-028 — Four failing avatar requests on every dashboard load (P3, fixed in Phase 5)
Headless Chromium after login: `GET /api/v1/users/{id}/avatar` returns 404
four times for a user without an avatar, and each logs a console error. Phase
3's fixture fails any test on console errors, so this must be fixed (don't
request an avatar the profile says does not exist, or return 204) before the
browser suite can be green.

## F-029 — 33 of 78 verification gates fail (P3, confirmed)

`run_all_gates.py --static-only`: 39 pass, 33 fail, 6 skip. Several failures
are stale gates that expect a pre-migration schema (`company_logo_url`),
require an argument (`verify_step4.py --phase`), or need rows in the database,
even though `--static-only` is supposed to avoid the database. Others are
real and are listed above (F-022, F-023, F-025) or in `01-baseline.md`. CI's
`backend-gates` job stays red until the gates are triaged. Gate scripts are
not read or edited under CLAUDE.md, so the triage needs an owner decision on
which gates are still authoritative.

**Phase 2 check (no gate regressed).** The static gate runner was run against `main` (a worktree
of the Phase 1 head) and against this branch, with the same development database. Per-gate
results are identical for 77 of 78 gates. The 78th, `verify_arch05_step0.py`, passed on `main`
and failed on the branch only because the `main` run had just left data behind: the gate
`verify_arch09_step9.py` creates organizations named `gate9-*` with no owner and never removes
them, and `verify_arch05_step0.py` fails when any ownerless organization exists. Run against the
same database, `main`'s own code fails identically (16 ownerless organizations), so it is state,
not code. **Finding:** the gates are not idempotent: running the suite twice on one database turns
a passing gate red (audit logs are append-only, so the leftovers cannot even be deleted). Fixing
that means editing gate scripts, which the rules forbid (N-010). The `main` run here measured 38 pass / 34 fail / 6 skip against Phase 1's recorded
39 / 33 / 6: one gate differs from that record, and because it is in the `main` run too it is
not from Phase 2. I did not chase which one.

**Phase 5 check (no gate regressed).** `run_all_gates.py --static-only` on two fresh databases on
the same server: `main` (with `main`'s own package versions) **37 pass / 35 fail / 6 skip**, this
branch **38 / 34 / 6**. 77 of 78 gates give the same result; `verify_arch20.py` went from FAIL to
PASS. The remaining failures are the stale gates described above (they expect older schema heads,
a component that was redesigned, or arguments), which only the owner can retire (N-010, N-016).

## F-030 — The test harness is slow and order-dependent (P3, confirmed)

Every DB test runs `TRUNCATE … CASCADE` over about 200 tables twice (about
0.9 s each on a normal disk), so the suite takes about 2 hours serially on a
laptop-class disk and about 25 minutes on a RAM-disk Postgres. Some service
tests use a fixed database name (`flowpilot_svc_test`), so two runs at once
collide, and results change with test order (for example, no published tier
is "in force"). Phase 3: transaction-rollback isolation or a template
database, plus a unique database name per run.

---

# Phase 2 findings (2026-09-30)

## F-031 — A foreign or unknown document id answers 200 on two sub-routes (P3, fixed)

**Plain language.** Every route that addresses a document by id should answer
"not found" when the document is not in your workspace, whether it belongs to
another customer or does not exist at all. Two did not: `GET
/workspaces/{ws}/work-items/{id}/entities` and `.../extraction-memory` answered
`200` with an empty result. No data crossed the boundary (both queries filter by
your workspace), so this is not a leak, but it broke the rule every other route
follows and would have hidden a future leak behind a friendly empty page.

**How it was found.** A new sweep (`tests/security/test_idor_child_entities.py`)
creates real objects in tenant B (16 collections: 12 through the API, plus work
items, API keys, automation rules and memberships through the ORM), then calls
every route that addresses a child of those collections from tenant A's own
organization, with B's ids. 47 child routes were probed (16 GET, 14 POST, 10
DELETE, 6 PATCH, 1 PUT) and these two were the only ones that answered.

**Fixed.** Both handlers now return `404 Document not found` unless the document
is in the URL's workspace. Six new tests (three per route: foreign, unknown, and
the control that a workspace's own document still answers 200): 4 failed before,
all pass after. The sweep and its control that B's rows are untouched afterwards
also pass.

## Multi-tenancy proof (Phase 2) — no cross-tenant leak found

- **Path-level (all 423 org- and workspace-scoped operations, both
  directions):** `tests/security/test_cross_tenant_sweep.py`. Tenant A's owner
  called tenant B's real organization and workspace ids with schema-valid bodies
  on every route and method; every answer was 401/403/404, none 2xx, none 5xx. A
  user in no tenant is refused everywhere; anonymous callers get 401/403
  everywhere. A **mutation control** turns the membership check off and the
  sweep must then find crossings (it does), so a passing sweep is not vacuous.
- **Object-level (IDOR):** F-031 above; 16 object collections, 47 child routes.
- **Platform routes:** 39+ super-admin operations, discovered from the
  dependency tree, refuse all tenant roles (`test_superadmin_only.py`).
- **Partner programme:** 25 routes with route-level "any signed-in user" refuse
  non-members and lower partner roles (`test_partner_isolation.py`).
- **Not covered here (honest limits):** object-level IDOR is proven for the 16
  object collections above (12 the API can create from a generated body, 4
  created through the ORM); the remaining collections need hand-built fixtures (Phase 3/4). SCIM,
  the public API-key gateway and the WebSocket are checked separately below.

## F-004 — resolved (Phase 2): five routes gated, the rest documented

Method: a real organization on the seeded FREE tier (and one on ENTERPRISE as the
control) calls **every route in every locked feature area** with a schema-valid
request (`tests/security/test_plan_gating_server_side.py`, 200+ operations across
26 areas). Every answer must be a plan refusal (HTTP 402 `CAPABILITY_REQUIRED` or
`ADDON_REQUIRED`) or be listed with a reason. The server answers **402, not 403**,
by design: "your plan does not include this" is the true statement.

**Two real bypasses (a FREE tenant succeeded):**
- `POST /organizations/{id}/developer/keys` → **201**: minted a public-API
  gateway key, while `POST /api-keys` (same key type) was refused. The module's
  comment claimed the plan ceiling was "enforced inside the service"; the
  capability itself was checked nowhere. Fixed (commit `4d8f24e`).
- `PUT /organizations/{id}/identity/security-policy` → **200**: the Enterprise
  session and IP policy was writable from any plan. Fixed (`b7b5f34`).

**Three routes with a missing gate that were not exploitable on FREE** (no object
can exist to act on) but belong to the same feature: `PATCH
.../developer/keys/{id}/tier`, `POST .../identity/domains/{id}/verify`, `POST
.../branding/sender-domain/verify`. Gated (`4d8f24e`, `b7b5f34`, `d9fc369`).

**Deliberately still open, pending owner decision N-003** (listed by name with
that reason in `ALLOWED_OPEN`, so a change is a one-line test edit): reads and
deletes of a paid feature's own data (API keys, custom domains, developer
console, identity config, branding, analytics destinations, webhooks, email
settings). Two more are open by design: the `/potential` upsell counts and the
email "test" routes, which use the tenant's own SMTP settings and send nothing
without them. A NEW ungated route in a locked area now fails the test.

**Not yet gated by anyone's decision (N-002):** BYOK, marketplace, service
levels, data governance and analytics reads have no plan gating in the API or the
UI; the sweep does not treat them as locked.

**Controls.** The ENTERPRISE tenant is refused on plan grounds on none of these
routes, so the gate is not a blanket refusal.

**Existing tests touched.** Three tests in `test_public_api_endpoints.py` issued
developer keys on an organization with no plan. Their arrangement now uses a
fixture that puts it on the seeded DEVELOPER plan; no assertion changed.

## F-022 — not a defect; guarded (Phase 2)

Gate 0V-G13 flagged `agent_selectors.resolve_review_item` because it has no
parameter named `tenant`. Reading the code: every selector takes `scope:
AgentScope` (organization, workspace and proposal ids) and only *builds* a typed
action. Tenancy is enforced where an action is *applied*:
`actions._execute` loads the review item with `workspace_id=proposal.workspace_id`
and cases with `Case.workspace_id == proposal.workspace_id`, and
`lock_proposal` filters by workspace, so a proposal in workspace A that names an
item id from workspace B finds nothing and is SUPERSEDED. Guard tests
(`tests/security/test_agent_selectors_carry_a_tenant_scope.py`) pin those facts.
**Unverified dynamically:** a full cross-workspace apply needs review fixtures
(Phase 3). The gate that flagged it is not edited (N-010).

## F-023 — not a defect; guarded (Phase 2)

The five "reads" are one parser and its callers. Only `app/core/client_ip.py`
splits the header, applies `TRUSTED_PROXY_HOPS` and takes the entry the trusted
proxy appended (the last), never the client-controlled first. The SAML route
hands the raw header to the same resolver; BYOK, SCIM, warehouse sync and custom
domains call `client_ip(request)`; three of the five hits in the gate are comments
or docstrings. No code reads `request.client.host` directly. With forged headers:
the real address always wins, 39 rotating forged values resolve to one client (a
per-IP rate limit cannot be evaded), a chain shorter than the trusted hops or a
garbage entry never becomes the client IP (rate limiting falls back to the proxy,
IP pinning refuses). `tests/security/test_forged_forwarded_for.py`, including an
AST check that no other module splits the header or reads the socket peer.
**Deployment note:** with `TRUSTED_PROXY_HOPS=0` behind a reverse proxy every user
shares the proxy's IP and the per-IP limits (600 requests a minute globally) would
throttle everyone together. `docker-compose.prod.yml` defaults it to 1 for Caddy.

## F-032 — Pending invitations were shown to an unverified account (P2, fixed)

**Plain language.** Registration deliberately never says whether an email address
is already taken (good: it stops people probing who has an account). The side
effect is that *anyone* can create an account for `victim@company.com` before the
real person does, and that account starts out "unverified". The list "my pending
invitations" looked up invitations by the address alone. So the squatter could
open it and read every invitation sent to the victim: the organization's name, the
role offered, the inviter's email address and the workspace names. No token and no
access to the victim's mailbox was needed. This is the "pre-hijacking" pattern.

**Proof.** `tests/security/test_invitations_and_unverified_accounts.py`: an
organization invites an address; an unverified account holds that address; before
the fix the response listed the organization, its role and the inviter's email.

**Fixed.** `list_invitations_for_user` returns nothing until the account's email is
verified. A genuine invitee loses nothing: accepting an invitation needs the
emailed token and itself verifies the address. Control test: a verified owner of
the address still sees the invitation. The 34 existing invitation tests pass.

## Authentication audit (Phase 2)

**Already covered by existing tests (2,000+ pass; nothing weakened):** refresh
rotation and reuse detection (replaying a rotated cookie kills the family), grace
window for racing tabs, logout, logout-all (in-flight access tokens die),
per-device revocation, another user's session is a 404, tokens are stored only as
hashes, verification/reset/email-change tokens are single-use and purpose-scoped,
a completed reset kills a link sent to a stolen mailbox, login back-off. Refresh
cookie: HttpOnly, SameSite=Lax, path-scoped to `/auth/refresh`, `Secure` in every
environment except development and test (`ENVIRONMENT` is now normalised, so a
different spelling cannot switch it off).

**Added (`tests/security/test_auth_token_attacks.py`, 28 tests, all pass):**
unsigned `alg: none` tokens, wrong-secret and tampered-payload tokens, HS384/HS512
signed with the right secret (only the configured algorithm is accepted), expired
tokens, wrong or missing `type`, missing expiry, tokens for a user that does not
exist, malformed subjects (SQL text, empty, nil UUID) and malformed
`Authorization` headers all answer 401 and never 500, all refusals return the
same body (the reason is not revealed), an unverified member reaches no tenant
data, a deactivated user is refused, a token issued before the user's revocation
cutoff is refused, an access token is not accepted as a refresh cookie.

**Registration and reset do not enumerate accounts:** registration answers 202
with the same message whether or not the address exists (and emails the existing
owner instead); F-032 was the one place that undid that.

**Rate limits, proven live against Redis** (`tests/security/test_rate_limits.py`, 4 tests; the
test harness normally bypasses the limiter, so nothing else exercised it): password guessing is
cut off with a 429 and a `Retry-After`; the refusal comes before the password is checked (a
correct password after the cut-off does not sign in); a forged `X-Forwarded-For` does not buy a
fresh allowance; the sign-in limiter fails closed when its store is broken while an ordinary
route fails open. Finding F-048 came out of it. Registration, forgot-password and reset share
the generic 300-per-minute-per-IP limit; each account also has its own reset-token issue limit
in the token service (unit-tested).
**Not covered here (residual, Phase 3/4):** MFA (the product has none), SSO/SAML
end-to-end (an XML-signature-wrapping rig exists from ARCH-28), OIDC state and
nonce handling, and invitation role-escalation edge cases beyond the existing
suite (OWNER is not invitable by design).

## F-034 — Stripe posted to the generic webhook route crashed (P3, fixed)

`POST /billing/webhooks/{gateway}` is the gateway-neutral receiver added for Dodo.
Stripe has its own literal route, registered first, so `/billing/webhooks/stripe`
never reaches it. But `normalize_gateway` is case-insensitive, so
`/billing/webhooks/STRIPE` or `/Stripe` did: the Stripe adapter has no Standard
Webhooks verifier, and every delivery raised an unhandled `AttributeError` (a 500).
It failed closed (nothing was trusted or stored), so it is not a hole, but a
Stripe dashboard endpoint typed with a capital would have looked like a permanent
Stripe outage. The generic route now answers 404 for Stripe (its endpoint is its
own path). Three tests (three spellings) failed before, pass after.

## F-033 — Dodo's mode guard cannot fire (P3, open, needs a real payload)

The generic receiver refuses "an event from the other environment" by comparing
`event.livemode` with `DODO_LIVEMODE`. For Dodo, `event.livemode` is set from
`settings.DODO_LIVEMODE` (`dodo_gateway.verify_webhook_signature`), so the two
values are the same variable and the check can never differ. A test-mode Dodo event
posted to a deployment configured live is stored. What actually separates the two
environments today is the signing secret, which differs per Dodo mode; a live
deployment carrying the test-mode secret (or the reverse) would accept the wrong
environment's events. **Not fixed here:** it needs a sample of a real Dodo event to
see whether the envelope carries a mode flag (Dodo is blocked in this sandbox).
**Mitigation now:** the production template says Dodo test and live keys and
secrets are separate; keep exactly one set per environment. Decision for Phase 4
once a payload is available.

## Billing webhook audit (Phase 2)

Tests: `tests/security/test_billing_webhooks.py`, 21 tests with real Stripe and
Standard-Webhooks (Dodo) signatures. **Stripe** (both literal paths): valid event
recorded once and a replay acknowledged as `duplicate: true` with no new row; no
header, garbage header, empty scheme, wrong secret, tampered body, stale timestamp
and a signature over a different body all answer 400 with the same generic message
and write no row; secret rotation (either of two secrets); oversized body refused
before any signature work (413); a test-mode event is refused by a live deployment
and the reverse; **no secret configured means nothing is trusted (500, so Stripe
retries)**; a verified body that is not an event is refused. **Dodo:** same set
with 401 and an empty body (no information), replay adds no row, id swapped after
signing refused, oversized body refused, no secret means 500.
**Observation, not a defect:** a Stripe signature with a timestamp in the future
verifies; that is the vendor SDK's own behaviour (it only rejects old ones), and a
forged future timestamp needs the secret.
**Not proven (residual):** out-of-order delivery, failed-payment dunning,
cancellation, downgrade and quota enforcement are decided by the reconciler jobs,
which fetch current state from the gateway; the gateways are blocked here. They
are on the Phase 3 list and in `02-security-deploy.md`.

## F-036 — An over-size upload was a 500, not a 413 (P2, fixed)

**Plain language.** The main upload endpoint (`POST /workspaces/{id}/work-items`)
is supposed to answer "413: file too large" when someone uploads more than the
workspace allows. The line that builds that answer named a constant that does not
exist in the installed web framework (`HTTP_413_PAYLOAD_TOO_LARGE`; the real name is
`HTTP_413_REQUEST_ENTITY_TOO_LARGE`), so the size check itself worked and then the
error handler crashed. Every oversize upload was an unhandled 500 with a traceback
in the log, and the console could not tell the user "too large". Nothing oversize
was stored, so this is a correctness and noise problem, not a data leak. Found by
the upload attack tests; nothing else in the code used the wrong name.
**Proof:** `test_an_upload_over_the_limit_is_413_and_stores_nothing` fails with
`AttributeError` before the change and passes after.

## F-035 — Page-level PDF actions survived the scrub (P2, fixed)

**Plain language.** Uploaded PDFs are rebuilt from their pages, which removes the
document-level scripts (`/OpenAction`, `/Names /JavaScript`); that part worked and
is tested. But a page keeps its own "additional actions" and its links, and a link
can carry a JavaScript, Launch (run a program), SubmitForm or ImportData (send data
to a URL) action, or a whole attachment. Those were copied into the stored file,
which colleagues then download and open in Acrobat or a browser. A low-privilege
member could plant a hostile PDF for an administrator to open. Defence in depth, not
a server compromise, hence P2.
**Fixed.** `_strip_page_active_content` removes `/AA`, dangerous annotation actions
(JavaScript, Launch, SubmitForm, ImportData, GoToR/GoToE, media, following any
`/Next` chain) and attachment and media annotations from each page before it is
copied, so the removed objects are unreachable and are not written at all. Ordinary
links and destinations are kept. **Proof:** four tests (one per action type) that
inspect the stored bytes failed before and pass after; a control test proves an
ordinary 3-page PDF still round-trips.

## File handling and injection audit (Phase 2, uploads)

`tests/security/test_upload_attacks.py` (49 tests) and
`test_upload_endpoint_filenames.py` (14). Result: the pipeline is sound. **Refused
(and pinned):** HTML, a Windows executable, a shell script, SVG, empty, null-filled
and truncated bodies named or declared as PDF; a ZIP called a PDF; a PDF declared as
PNG (mismatch, quarantined); an octet-stream declaration hiding an executable; a
script in a GIF comment (removed by the re-encode); encrypted PDFs; corrupt PDFs; a
60-page PDF over a 50-page limit; an image that declares 1.6 billion pixels in a few
hundred bytes. **Archives** (the bulk-import path, `ingestion/archive.py`): a
300 MB-inflating bomb refused from its directory alone; thirty entries at moderate
ratio refused in aggregate; 2,005 tiny entries refused; six traversal and absolute
names never become a member; a nested archive, executables, HTML, SVG, macro
documents inside an archive are not members; a fake ZIP is refused. **XML** (the ERP
formats): external entities, entity-expansion bombs, parameter entities, case and
spacing variants, a UTF-16-encoded DOCTYPE, oversized input are refused; XInclude is
not processed. **Filenames through the real endpoint:** eleven hostile names (path
traversal in both slash styles, absolute paths, a 400-character name, NUL, an HTML
tag, CR/LF, a right-to-left override) never change where the file is stored (key is
`<organization>/<random>`), nothing is written outside the storage root, and the
download response carries no injected header. A non-PDF body leaves no work item and
no stored file; a viewer cannot upload. **Presigned links:** exports and redaction
bundles use a fixed 15-minute lifetime the client cannot change; the generated URL
carries `X-Amz-Expires <= 900` and is scoped to one key.
**Not covered here (residual):** the multipart upload-session and batch/bulk paths
(they share `validate_spooled` and `archive.inspect`, exercised above, but their own
endpoints are Phase 3), virus scanning (none exists; an owner decision for
enterprise sales), and the Caddy request-size ceiling (checked with the container
audit).

## F-037 — `%00` in a search box was an unhandled 500 on 19 routes (P3, fixed)

**Plain language.** PostgreSQL cannot store the "NUL" character. If someone types it
(or sends `%00` in the address), the database driver raises a Python error while it
builds the query, no handler expects it, and the user gets an internal-server-error.
Any signed-in user could do this on 19 routes (work-item search, obligations,
entities, cases, assistant sessions, the review queue, ERP postings and more), which
fills the error log and would trip alerting. It is **not** SQL injection: every value
is passed as a bound parameter, which is exactly why it fails safely.
**Fixed.** A small ASGI middleware (`app/middleware/nul_guard.py`) answers 400 "NUL
characters are not allowed" for NUL in the path, the query string, and JSON or
URL-encoded bodies (small ones; it recognises the JSON `\u0000` escape but not an
escaped backslash followed by the letters `u0000`). Uploads (binary) are never
inspected. **Proof:** the sweep found the 19 routes; 8 of 11 tests in
`test_nul_byte_guard.py` fail with the middleware unregistered and all pass with it,
and the sweep now records zero crashes.

## Injection audit (Phase 2)

**SQL injection: none found.** `tests/security/test_injection_sweep.py` calls every
tenant-scoped GET route that takes a string query parameter (search boxes, filters,
sort keys, cursors; 20+ parameters) with 16 payloads each (quote-or-true, stacked
`DROP TABLE`, `pg_sleep`, LIKE wildcards, backslash, template markers, a log4j-style
string, script tags, NUL, 5,000 characters, path traversal, `ORDER BY` injection):
300+ probes, no 5xx after F-037, the users table intact afterwards, and a boolean
probe (`' OR '1'='1`, `quarterly' OR '1'='1`) does not widen a search.
**SSRF: the client is sound.** `tests/security/test_ssrf_hostile_destinations.py`
(63 tests): loopback, the cloud metadata address, private, link-local, carrier-grade
NAT, multicast and reserved ranges are refused in every notation an attacker uses
(dotted, decimal `2130706433`, hex `0x7f000001`, octal, short `127.1`, bracketed
IPv6, IPv4-mapped IPv6, NAT64, 6to4); a hostname that resolves to both a public and a
private address is refused; only validated addresses are returned for connecting;
non-https schemes, `file:`, `gopher:` and `javascript:` are refused before any
connection; and ten hostile webhook URLs are refused at creation through the real
API. Existing tests cover no-redirect-following, size and time caps.
**The two other places a tenant types a destination are sound too**
(`test_tenant_supplied_endpoints.py`, 23 tests): the BYOK Azure OpenAI
`resource_endpoint` (which the server connects to with the tenant's key) accepts only a genuine
Azure OpenAI hostname, and a tenant SMTP host and port cannot point at loopback, the cloud
metadata address or a private address (that would have turned "send test email" into a probe of
the platform's network).
**Injection into automations: sound** (`test_automation_template_injection.py`, 22 tests). A
workflow reacts to a document whose text an outsider wrote, and prompt injection can put any
text into an extracted field. What matters is what the automation layer lets that text do: a
template is a regex substitution over an allow-list of variables, not an engine, so nothing is
executed; values are inserted in one pass, scalar-only, truncated and HTML-escaped, never
re-expanded; the recipient of `email.send` and the endpoint of `webhook.send` are chosen by the
rule's author, never taken from a document field (and `webhook.send` has no URL at all); an
unknown variable is refused when the rule is saved.
**Not covered here (residual):** template injection in tenant-editable email and branding
templates that are rendered to the tenant's own users, and prompt injection driven end to end
through the AI assistant. The AI tool selectors take ids and closed vocabularies and never
retrieved text (an import-time check refuses otherwise), which is the design answer to prompt
injection, but no test here drives a hostile document through the assistant with a real or
recorded model (Phase 3/4).

## F-038 — Production builds shipped source maps (P2, fixed)

**Plain language.** The frontend was built with `sourcemap: true`, so `npm run build`
wrote a `.js.map` next to every bundle. A source map is the application's original
TypeScript source, comments included. Caddy serves the whole `dist` folder, so
`https://app.example.com/assets/index-<hash>.js.map` would have handed anyone the
readable source of the product, which makes finding bugs and business logic trivial.
**Fixed.** `sourcemap: false`. `frontend/scripts/check-no-sourcemaps.mjs` checks the
Vite config and (with `--dist`) the built folder for any `.map` file or
`sourceMappingURL`; it exited 1 before the change ("build.sourcemap is true"), exits 0
after, and runs in CI after the build. A real production build was made and `dist`
holds zero map files. **If you want maps for an error tracker:** build them in a
separate step and upload them, never into the served folder.

## F-039 — Dependency audit (P2, fixed in Phase 5 except torch/setuptools/paramiko)
**npm (frontend): 7 advisories (6 high), all fixed.** `npm audit fix` (no
major-version change, 14 packages) took `npm audit` from 7 to 0. The two on
production code were `react-router` and `react-router-dom` (an RSC-mode CSRF bypass;
this app uses client-side routing only, so it was not reachable, but it is fixed
anyway). tsc, lint and the production build pass after the update. CI now runs
`npm audit --audit-level=high` and fails on any new one.

**Python: 54 advisory entries in 13 of 194 pinned packages (pip-audit).** Bumped
(security fix, no conflict, affected tests pass with no new failure against the
Phase 1 baseline): `pypdf` 6.14.2 → 6.16.1 (five advisories; it parses **untrusted
uploaded PDFs**), `pyasn1` 0.6.3 → 0.6.4, `anyio` 4.14.1 → 4.14.2, `aiohttp` 3.14.1 →
3.14.3. **Not bumped, with the reason (these are the Phase 4 dependency items):**
- `starlette` 0.41.3 (13 advisories; fixed in 0.47.2 to 1.3.1) is pinned by `fastapi`
  0.115.6. Moving needs a FastAPI line upgrade and the full suite. It carries the
  multipart parser used by uploads, so this is the most important remaining one.
- `python-jose` 3.3.0 (algorithm-confusion and JWE-bomb advisories): the fix (3.4.0)
  pins `pyasn1<0.5`, which conflicts with the patched pyasn1. The app uses it for
  HS256 only with a fixed algorithm list and no JWE, so neither advisory is
  reachable; the clean fix is to migrate to PyJWT.
- `cryptography` 49 → 50 (major), `oauthlib` → 4 (major), `pyarrow` → 23, `torch`
  2.12.1 → 2.13.0, `setuptools` → 83: upgrade with the suite; none is on a request
  path that takes untrusted input except through the libraries above.
- `paramiko` 3.5.1 and `ecdsa` 0.19.2 have no fixed version published. `paramiko` is
  the ERP SFTP client (egress-checked, host key pinned); `ecdsa` comes with
  python-jose and is not used for HS256.
A `pip-audit` job now runs on every pull request as **advisory** (not required),
so the list stays visible.

## F-040 — The production API container could not start (P1, fixed)

**Plain language.** The production compose file starts the API with
`gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker ...`. Gunicorn is
not listed in `requirements.txt` (or anywhere else), and the Docker image installs
only what that file lists, so the `web` container would exit at once with
"gunicorn: executable file not found". Every other production service waits for
`web`, and Caddy waits for `web` to be healthy, so the whole stack would come up
with no API. It survived because nothing in CI ever runs the production command
(the dev compose uses `uvicorn --reload`).
**Proof.** `tests/infra/test_production_compose_commands.py` reads the compose file
and checks that every Python program it starts (`gunicorn`, `uvicorn`, `alembic`) is a
pinned requirement and importable, that the worker class and the `app.main:app`
target exist: the gunicorn check failed before, all 9 pass after.
**Fixed.** `gunicorn==26.2.0` is pinned (installed and imported here; the pinned
resolution has no conflict). **Unverified:** a real `docker compose up` of the
production stack (images cannot be pulled or built in this sandbox: Docker Hub
rate-limits and the image needs ~8 GB), so the first production deploy should run
`docker compose -f docker-compose.prod.yml --env-file .env.production config` and
then `up -d web` and watch `docker compose logs -f web` (RUNBOOK, "First deploy").

## Containers and infrastructure audit (Phase 2)

**Verified by reading and by rendering `docker compose config` with a filled-in env
file** (the daemon cannot pull images here, so nothing below was *run* in containers):

- **Non-root: yes.** Every Dockerfile target ends with `USER flowpilot`; the two that
  switch to root to create a cache directory switch back.
- **Ports:** only Caddy publishes ports (80, 443); Postgres, Redis, MinIO, the API and
  the reranker are on the internal network. The dev compose publishes database and
  MinIO ports, which is dev only.
- **Health:** the API had a liveness check only; F-042 adds readiness and the
  production `web` healthcheck uses it. Workers, the scheduler and the reranker have no
  health check (a worker has no port; a heartbeat file is a Phase 4 item).
- **Memory limits:** set on every service.
- **`/api/v1/internal/*` is not published** (Caddy answers 404); the TLS "ask" endpoint
  is only reachable from inside the network.
- **F-043 (pinned versions).** `pgvector/pgvector:0.8.1-pg16`, `redis:7.4.6-alpine`,
  `caddy:2.10.2-alpine` and `python:3.12.13-slim-bookworm` (each confirmed to exist on
  Docker Hub); a test fails on any floating third-party tag. Pinning by digest
  (`@sha256:...`) is the next step and is an operator action at release time.
- **F-044 (CSP).** Caddy sends HSTS (1 year, preload), `nosniff`, `X-Frame-Options:
  DENY` and a strict Referrer-Policy, but no CSP. A
  `Content-Security-Policy-Report-Only` header is added so violations are visible in
  the browser without breaking anything; rename it to `Content-Security-Policy` after
  one real session shows none. **Unverified in a browser** (Caddy could not be run here).
- **F-045 (image size, open).** `requirements.txt` is a full `pip freeze` and every
  target installs it, so the "zero ML dependencies" `web` image carries torch (with
  CUDA libraries), paddle and sentence-transformers. It slows every deploy, enlarges
  the attack surface and the disk bill. Fix (Phase 4): split requirements per target.
- **Redis has no password.** It is reachable only on the compose network, so this is
  defence in depth; adding `requirepass` is a Phase 4 item.
- **F-024.** The migrate service now passes `ARCH40_CONTRACT` through from the
  environment (default `0`, so production does not run the lossy step by accident;
  staging sets `1` per owner decision N-009). RUNBOOK "First deploy" has the exact
  one-time procedure. Production stays open until the owner answers whether a
  production database with real data exists (N-009).

## F-046 — The API's documentation and full route map were public in production (P3, fixed)

**Plain language.** FastAPI ships an interactive page (`/docs`), a second one
(`/redoc`) and a machine-readable description of the whole API
(`/api/v1/openapi.json`). By default all three are open to anyone, with no
sign-in. FlowPilot has hundreds of routes (partner, operator, billing, SCIM), and
that description lists every one with the exact shape of each request. It does not
break anything by itself, but it is a free map for an attacker, and nothing in the
product uses it: the public API's documentation is hosted elsewhere and the web app
never reads the schema. The production ingress even forwarded `/docs` and
`/openapi.json` to the API on purpose.
**Proof.** `tests/security/test_api_docs_not_public_in_production.py` boots the app
in a fresh interpreter per environment. Before the fix, with a complete production
(or staging) configuration, `/docs`, `/redoc` and `/api/v1/openapi.json` all
answered 200 (2 failed). After it, they answer 404, and the control (a development
boot still serves them) passes.
**Fixed.** `app/main.py` turns the three URLs off when `ENVIRONMENT` is `production`
or `staging` (the same set the start-up guard treats as hardened). Development and
test are unchanged, so the schema-driven security sweeps and the existing tests that
read `/api/v1/openapi.json` still run.
**Left as is.** `deploy/Caddyfile` still has `handle /docs*` and `handle
/openapi.json` blocks; the API now answers 404 behind them. They were not removed
because a historical gate script pins that file's content and gate scripts are never
edited (N-010). Harmless, and worth deleting when the gate is retired.

## F-047 — 23 production settings never reached the app (P1, fixed)

**Plain language.** Docker Compose gives a container only the variables listed under
its `environment:`. Anything else in `.env.production` is used to fill in the compose
file itself and then thrown away. `.env.production.template` asks you to fill in 70
variables; 23 of them were not in that list, so the app never saw them and quietly ran
on its built-in defaults. The important ones:

- `GROQ_API_KEY`, `GEMINI_API_KEY`, `LLM_PROVIDER`: with no platform key, every AI
  feature fails for any customer who has not brought their own key.
- `BILLING_GATEWAY` and every `DODO_*` setting: Dodo Payments could not be selected
  or configured at all (the default gateway is Stripe).
- `GATEWAY_PRICE_ID_DEVELOPER` / `_BUSINESS`: the plan-seeding command could not see them.
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `MAX_UPLOAD_SIZE`: the
  template says these are "written out so they are visible", but changing them did nothing.
- the other `BILLING_*`, `CUSTOM_DOMAIN_*` and `TLS_*` tuning values.

Nothing failed loudly. It also hid a safety check: the start-up guard refuses a Dodo API
key with no webhook secret, but it never ran because the container never got the key.
**Proof.** `test_every_setting_the_template_asks_for_reaches_the_containers`
(`tests/infra/test_production_env_template.py`) lists every template variable that no
service receives; it failed naming exactly the 23 above and passes now. Two variables are
exempt on purpose, with a control test that the exemption list only holds real template
entries: `IMAGE_TAG` (read by Compose itself) and `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`
(passed to the one-off seed command only, so a password is not in every container).
**Fixed.** `docker-compose.prod.yml` passes all 23 on, each with the code's own default.
Checked by rendering the file with `docker compose config` and starting the app from
exactly the environment the `web` service receives: it boots in production with
`BILLING_GATEWAY=DODO`, a Groq key, and a Dodo test-mode base URL derived from
`DODO_LIVEMODE=false`; with every optional value blank it also boots; and a Dodo key with
no webhook secret is now refused at start-up as intended.
**Side fix.** Forwarding a blank `GROQ_API_KEY=` gives the app the empty string, not
"unset", and `llm_service.py` only refused None: a blank key built a client with an empty
credential and failed at the first call with the provider's own error. Blank now reads as
"GROQ_API_KEY is not configured" (same for Gemini), with 6 tests (4 failed before).
**Follow-up.** The template listed two of the three price ids the plan seeder reads and
called Enterprise "sales-led", but `scripts/seed_quota_tiers.py` sells it self-serve and
refuses a paid plan with no id, so the documented first-deploy seed would have stopped on
`GATEWAY_PRICE_ID_ENTERPRISE`. Added to the template and to the compose file, with a test
that reads the seeder's own table (it failed before). The plan table and its prices
(Developer $49, Business $299, Enterprise $799 per seat per month) live in that script; I
did not change or judge them, and NEEDS-OWNER N-011 asks you to confirm them before launch.
**Not done.** No guard makes a platform LLM key mandatory: a deployment where every
customer brings their own key is legitimate. The RUNBOOK checklist says what happens
without one.

## Secrets scan of the repository history (Phase 2) — no real credential found

Tool: `docs/hardening/tools/scan_history_for_secrets.py` (prints masked samples only, never
values). It scanned every line ever added in all 113 commits (706,674 diff lines) for Stripe,
AWS, Groq, Google, GitHub and Slack keys, JWTs, private-key blocks, OpenAI-style keys, database
URLs with passwords and secret-looking assignments. `git log --diff-filter=A` shows the only
`.env`-style files ever added are `.env.example` (backend and frontend) and
`.env.production.template`; no `.env`, `.pem` or `.key` file was ever committed.

- **Keys and tokens: none.** No Stripe, Groq, Google, GitHub, Slack, OpenAI-style key and no JWT.
- **AWS:** one access-key id, and it is the one from AWS's own documentation
  (`AKIAIOSFODNN7EXAMPLE`), in a warehouse-sync test.
- **Private-key blocks:** three test files, all fake stubs (`...\nMIIEvQ\n...` or `"A" * 2000`).
- **Database URLs:** development defaults (`postgres:postgres@localhost` in CI and the launch
  scripts), placeholders (`${POSTGRES_PASSWORD}`), and the dev compose file's
  `${POSTGRES_PASSWORD:-flowpilot}` fallback (the scan labels that one "OTHER, length 43"
  because of its shape; it is a template expression). The production compose file requires the
  variable and has no fallback.
- **Secret-looking assignments (20 files):** test passwords and fixtures, a benchmark constant,
  a `localStorage` key name and generated type names. None is a live credential.
- **The real exposure was not a leaked value but the three public defaults in `config.py`**
  (F-003, fixed). They were committed on purpose as defaults, which is worse than a leak
  because the app used them silently.

**Limits, stated plainly.** Files the campaign rules forbid reading (the `apply_*.py` and
`verify_*.py` scripts, `backend/evidence/`, `arch07_*`, `arch08_*`, PDFs, certification reports)
are excluded, so a secret or customer data inside them would NOT be found: only you can check
that (NEEDS-OWNER N-016). Binary files (the deleted `stripe.exe`, a public Stripe CLI build) are
not scanned. Logs and API responses were covered separately: F-041 (the gunicorn access log wrote
capability tokens and query strings) and F-021 (validation errors echoed submitted secrets).

## F-048 — The sign-in allowance is half of what was intended (P3, fixed in Phase 5; the allowance is N-018)
**Plain language.** The sign-in route is limited to 20 attempts per 5 minutes per IP address
(`POLICY_LOGIN_IP`). The limiter is applied twice on that route, once by the global middleware
(the public-route registry maps `/auth/login` to that policy) and once by a `RateLimiter`
dependency on the route itself, and both draw from the same counter. So each attempt costs 2 and
the real allowance is **10 per 5 minutes per IP**, successful sign-ins included.
**Why it matters.** Security-wise it is stricter, not weaker. But everyone behind one public
address (an office, a school, a mobile carrier) shares that allowance: the 11th person to sign in
within five minutes is told to wait, and a single attacker at the same address can lock a whole
company out for five minutes.
**Evidence.** `test_password_guessing_is_cut_off_with_a_429` measures the number of wrong sign-ins
accepted before the 429 (it printed 10 against the real Redis backend) and only asserts it is
between 3 and the documented 20, so it will not break whichever way you decide.
**Not changed.** Raising it to the documented 20 would double an attacker's guesses per address,
and the right rule (count only failures? per account? per address?) is a policy choice → N-018.


---

# Phase 3 findings (browser tests)

Found by the Playwright suite in `frontend/e2e` (see `03-coverage.md`). Screenshots are in
`docs/hardening/e2e-screenshots/`; the full report with traces is the `e2e-report` CI artifact
or `frontend/e2e/playwright-report` after a local run. "Repro" steps work on a fresh clone with
the seed from `backend/scripts/seed_e2e.py`.

## F-049 — Every page logs a 404 for the user's missing avatar (P3, fixed in Phase 5)
**Plain language.** The sidebar shows your avatar. Most people never upload one, so on every page
the browser asks for `/users/{id}/avatar`, gets "404 Not Found" and prints a red error in the
console. The app then shows initials, so a user sees nothing wrong. The comment in
`components/common/Avatar.tsx` says this is deliberate.
**Why it matters.** Error monitoring (Sentry or similar) and anyone debugging will see a red error
on every page view for almost every user, which hides real errors. It also made every browser test
fail, so the suite lists it as a known issue (it is still reported on each test).
**Repro.** Sign in as `c-admin@e2e.example.com`, open any page, open the browser console.
**Evidence.** `tests/known-issues.spec.ts` (proves it still happens); the `known-issue` annotation on
every test. **Fix idea (Phase 4).** Return `has_avatar` in `/auth/me`, or answer 204 instead of 404.

## F-050 — Opening a scanned packet's review screen crashed the whole API (P0, fixed)

**Plain language.** The "Scanned packets" review screen shows small pictures of every page. The
browser asks for all of them at once; the server drew them at the same time on several threads
with PDFium, a PDF library that must never be used by two threads at once. Its memory got
corrupted and the API process died (`free(): unaligned chunk detected in tcache 2`), for every
customer, until someone restarted it.
**Repro (before the fix).** Upload a multi-page PDF; open Scanned packets; click the packet.
**Evidence.** The browser suite stopped with "connection refused" mid-run; the API log ended with
the abort. `backend/tests/services/test_packet_thumbnail_thread_safety.py` renders from 8 threads
in a child process: killed by SIGABRT 3 runs out of 3 before the fix, passes 3 of 3 after.
**Fix.** `app/core/pdfium_lock.py`, a process-wide lock held for the document's whole lifetime in
`render_thumbnail` (commit `834f99e`). The ARCH-43 gate passes 12/12 before and after;
`tests/security/test_plan_gating_server_side.py` passes. The full backend suite was **not** re-run
for this commit (see `03-coverage.md`, "What was not done"). Other call sites: F-066.

## F-051 — Accepting a team invitation fails with HTTP 500 on paid organizations (P1, fixed in Phase 4)

**Plain language.** When someone accepts an invitation, the app records "a seat was added" for
billing. It writes that as an internal event called `billing.seat_added`, but the database only
allows a fixed list of internal event names (`ck_outbox_events_visibility_vocabulary`) and that
name is not on it. The insert is refused, the whole acceptance rolls back, and the invitee sees an
error. Any organization with a live subscription is affected, so **no paying customer can add a
team member through an invitation.**
**Repro.** As `c-owner`: Settings → General → invite a new email. Sign up as that email, verify,
open the invitation link, click Accept → stays on `/invitations/accept`; API log:
`CheckViolation ... ck_outbox_events_visibility_vocabulary ... billing.seat_added`.
**Evidence.** `tests/20-organization.spec.ts` "full member lifecycle through email".
The existing backend test file for seats (`test_arch15_gate_15_3_15_4_subscriptions_seats.py`) is
red for an unrelated, date-dependent reason (a test tier "not in force at 2026-08-01"), which is
why this was not caught. **Fix idea (Phase 4).** A migration adding `billing.seat_added` and
`billing.seat_removed` to the vocabulary constraint (or emitting them with the right visibility),
plus a regression test; check `jit_service.py` and `deprovision_service.py`, which emit the same.

## F-052 — Audit log export never works (P2, fixed in Phase 4)

**Plain language.** On Organization → Audit log, the CSV and NDJSON buttons send `format=CSV`. The
server only accepts lowercase `csv` or `jsonl`, answers 422, and the page throws an uncaught error.
Enterprise customers and auditors cannot export the audit trail.
**Repro.** Audit log → click CSV. **Evidence.** `tests/20-organization.spec.ts` "filter by action
and actor, inspect an entry, export CSV and NDJSON": `422 ... Input should be 'csv' or 'jsonl'`,
plus `pageerror ApiError` and an unhandled rejection.

## F-053 — Viewers see pages they cannot use (P2, error pages fixed in Phase 5; read access is N-019)
**Plain language.** A workspace VIEWER has Workflows, Run history and Review queue in the sidebar,
but the server refuses them (403), so the pages show "Failed to load rules metrics" or "The review
queue could not be loaded". Either the sidebar should hide them, or viewers should get read-only
access. **Repro.** Sign in as `c-viewer@e2e.example.com`; open Workflows. **Evidence.**
`tests/03-role-matrix.spec.ts` "workspace pages shown to a VIEWER must work for a VIEWER".
(Decision needed: should viewers read workflows and the review queue? → N-019.)

## F-054 — Forbidden organization pages say "couldn't be loaded" (P3, fixed in Phase 5)
**Plain language.** A MEMBER who opens an admin-only organization page by URL (email settings,
service levels, developer platform, API keys, webhooks) sees "… couldn't be loaded. Try again",
which suggests an outage. Members and Billing pages already show a proper "requires an owner or
administrator" message; the rest should too. Branding shows the whole edit form, whose saves then
fail. **Evidence.** `tests/03-role-matrix.spec.ts` "forbidden organization pages by URL".

## F-055 — The BILLING role gets a 403 on every organization page (P3, fixed in Phase 5)
**Plain language.** The organization header's notification bell asks for organization
notifications, which the BILLING role may not read, so every page logs a 403 for that role.
**Evidence.** `tests/03-role-matrix.spec.ts` "organization sidebar — A.billing".

## F-056 — The assistant loses the user's question when the AI is unavailable (P2, fixed in Phase 5)
**Plain language.** If the AI provider is down (or no key is set) the server answers 503 "The AI
service is temporarily unavailable". The page shows a short toast for 4 seconds, and the question
the user typed is gone: not in the box, not in the conversation (still "0 msg"). Users will think
the product ignored them. **Repro.** With no `GROQ_API_KEY`, ask any question.
**Evidence.** `tests/11-assistant-intelligence.spec.ts` "when the AI provider is down…";
screenshot `e2e-screenshots/F-056-assistant-question-lost.jpg`.

## F-057 — Forms hide the server's explanation behind "status code 422" (P3, fixed in Phase 5)
**Plain language.** The server explains what is wrong (for example "a Tally target needs
tally.company (the company to import into)", or "this sentence could not be read as a checkable
rule"), but the ERP target form and the corroborator show only "Request failed with status code
422". **Evidence.** `tests/12-processing.spec.ts` "an invalid target shows the server's reason…",
"a rule the engine cannot read is explained…"; screenshot `e2e-screenshots/F-057-erp-422.jpg`.

## F-058 — Healthy pages log 404 errors for "not configured" (P3, fixed in Phase 5)
**Plain language.** Settings → Email asks for the workspace's email override and gets 404 "This
workspace has no email override"; Organization → Branding asks for `/branding/logo` and gets 404.
Like F-049, a normal state is reported as an error in the console and in error monitoring.
**Evidence.** `tests/14-configuration.spec.ts` "every settings section opens", the Branding page
smoke test and `tests/20-organization.spec.ts` branding tests.

## F-059 — Locked pages opened by URL have no upgrade button (P3, fixed in Phase 5)
**Plain language.** Clicking a locked item in the sidebar opens a dialog with "View plans". Opening
the same page by URL (a bookmark, a shared link) shows only a lock card ("It's included on the
Business and Enterprise plans") with no button; Tables shows just a sentence. Data is not leaked
and the server refuses with 402 (good). **Evidence.** `tests/02-plan-matrix.spec.ts` (lock card
shown; no upgrade control asserted); screenshot `e2e-screenshots/F-059-lock-card.jpg`.

## F-060 — "Claim domain" is offered where custom domains are off (P3, fixed in Phase 5)
**Plain language.** With `CUSTOM_DOMAINS_ENABLED=false` (the default) the Branding page still offers
"Claim domain"; the server then answers 501 "Custom domains are not enabled on this deployment".
The button should be hidden or explain this up front.
**Evidence.** `tests/20-organization.spec.ts` "claim a custom domain…".

## F-061 — Capabilities in the Phase 3 brief that the product does not have (P3, partly built in Phase 4)

Not bugs in existing code, but things the brief expected and the tests looked for:
- **Global search (Ctrl+K) only finds pages**, not documents: searching "INV-E2E-1001" says "No
  page matches". (`tests/14-configuration.spec.ts`)
- **Notifications** have "Mark all read" and remove, but **no mark-unread toggle and no category
  filters**. (`tests/10-workspace-documents.spec.ts`)
- **Billing has no promo-code field.** (`tests/20-organization.spec.ts`)
- **Webhooks have no "send test ping"** button (Deliveries, Rotate secret, Delete only).
- **Organization members are invited from workspace settings**, not from Organization → Members.
- **The document viewer has no page image**: the OCR tab shows the extracted text, but there is no
  rendered page with highlighted boxes beside it. (`tests/10-workspace-documents.spec.ts`)
- **No field correction in the document viewer**: extracted values can only be corrected on review
  queue items, which only exist for low-confidence extractions (none with stub OCR, see F-063).
Decide which you want before launch → N-020.
**Phase 4:** built the three you asked for in the Phase 4 brief - global search finds documents
(also by invoice number), entities and cases (F-091); webhooks have "Send test event" (F-080); the
review workbench shows the page image with each agent's reading highlighted (F-092). Still open:
notification filters / mark-unread, promo code at checkout, invite from Organization → Members,
field correction in the document viewer (N-020).

## F-062 — Unsaved workspace settings are lost without warning (P3, fixed in Phase 5)
**Plain language.** The app has an "unsaved changes" guard, but it does not cover Settings →
General: rename the workspace, click Documents in the sidebar, and the edit is gone with no
question asked (the Save button had become active, so the form knew it was changed).
**Evidence.** `tests/14-configuration.spec.ts` "unsaved changes block navigation until confirmed".

## F-063 — Some features cannot be proven end to end in this sandbox (P2, blocked)

**Plain language.** The sandbox runs `ML_STUBS=true` (no real OCR or embeddings) and has no LLM
key. So the processed sample documents produce no entity records, no tables, no three-way match
case and no radar flags, no low-confidence extraction lands in the review queue, and the assistant
cannot answer. The pages for all of these were tested (render, filters, lock states, forms), but
the *results* were not. These rows are `blocked` or only `smoke` in the ledger.
**What to do.** Run `npx playwright test -c e2e` on your own PC with real models and
`E2E_LLM=1` once (RUNBOOK §7 and `frontend/e2e/README.md`), or provide a recorded model
response fixture so CI can cover them.

## F-064 — The `RATE_LIMIT_*` settings do nothing (P3, fixed in Phase 5)
**Plain language.** `config.py` and `.env.example` offer `RATE_LIMIT_LOGIN_IP_PER_5MIN`,
`RATE_LIMIT_GLOBAL_IP_PER_MINUTE`, `RATE_LIMIT_USER_PER_MINUTE` and others, but nothing reads them:
the limits are fixed numbers in `app/core/rate_limit/policy.py`. Changing the setting has no effect
(found when raising it for the test stack had none). Same area as F-048 (N-018).

## F-065 — Members may create ERP postings (P2, fixed in Phase 4 — your decision: Admin/Owner only)

**Plain language.** The Phase 3 brief says Members must not trigger mutating ERP posts. The server
lets a MEMBER whose workspace role is CONTRIBUTOR create a posting (it answered "ERP target not
found", i.e. it got past the permission check); VIEWER is refused (403, correct). Whether
contributors may post to the ERP is a product rule → N-019.
**Evidence.** `tests/03-role-matrix.spec.ts` "ERP posting: a workspace VIEWER and a MEMBER may not post".

## F-066 — Other PDFium call sites have no lock (P2, fixed in Phase 4)

Same class as F-050. Unprotected: `services/redaction/rasterize.py` (2), `redaction/leakcheck.py`,
`tables/reader.py`, `tables/geometry.py`, `corroboration/synthetic.py`, `ocr/paddle.py` (2),
`ocr/pdf_text_layer.py`. Whether they can run concurrently in one process (API thread pool or a
threaded worker) was not tested. Phase 4: a stress test per site, then the same lock.

---

# Phase 4 — live engine hardening (branch `hardening/phase-4-core-engines`)

Every "fixed" below has the four Evidence-Rule steps: a test that failed before the fix (named),
the fix (commit), the same test passing, and the rest of the suite still passing (see
`04-core-engines.md` §6 for the full-suite comparison). Commits are on the branch.

## F-067 — Opening Document settings switched extraction off; verification could not be turned on (P1, fixed)

**Plain language.** The Document settings page saved its form with "summarise" and "extract
entities" unticked by default, so simply opening and saving it turned extraction off for the
workspace, and multi-agent verification had no switch at all in the page. **Fix** `03fff2b`.
**Proof** `tests/engines/test_document_settings_live.py`.

## F-068 — Verification agents were told every document was "Other" (P2, fixed)

The agents re-reading a document were prompted with type "Other" instead of the document's type,
so invoice-specific instructions never applied. **Fix** `d21f95f`. **Proof**
`tests/engines/test_verification_live.py::test_agents_are_prompted_with_the_documents_type`.

## F-069 — A field the agents split on evenly was approved automatically (P1, fixed)

With two agents disagreeing 1–1 on a field, "majority" picked one and the document was released
without a person. Now an even split always goes to review. **Fix** `82f755e`. **Proof**
`test_verification_live.py::test_an_even_split_on_a_field_is_never_auto_approved`.

## F-070 — Extraction memory's nightly sweep crashed on values of different lengths (P2, fixed)

**Fix** `792317e`. **Proof** `tests/engines/test_extraction_memory_live.py::test_values_of_different_lengths_under_one_label_do_not_break_the_sweep`.

## F-071 — The rule extraction memory applied was not the one it measured (P2, fixed)

**Fix** `781a7af`. **Proof** `tests/engines/test_extraction_memory_rules.py::test_a_rule_promoted_at_volume_is_the_one_applied_and_measured`.

## F-072 — Extraction memory never filled a field the model left empty (P2, built)

Learned layouts were measured but never used. An ACTIVE layout now fills fields the model left
empty. **Commit** `65e7884`. **Proof** `test_extraction_memory_rules.py::test_an_active_layout_fills_the_field_the_model_left_empty`.

## F-073 — Cases ignored the model's classification when deciding a document's type (P2, fixed)

**Fix** `f1de2e2`. **Proof** `tests/engines/test_cases_live.py::test_a_documents_type_comes_from_the_models_classification`.

## F-074 — Table exports dropped printed decimals ("1,250.50" became "1250.5") (P3, fixed)

**Fix** `e745c70`. **Proof** `tests/engines/test_tables_live.py`.

## F-075 — The radar flagged a supplier's next monthly invoice as a duplicate (P2, fixed)

Same supplier, same layout, different month and amount → "duplicate". The similarity layer now
also requires compatible dates and totals. **Fix** `22c201b`. **Proof**
`tests/engines/test_radar_live.py::test_the_suppliers_next_monthly_invoice_is_not_a_duplicate`.

## F-076 — A new workspace could not build "only if" conditions on extracted fields (P2, fixed)

The condition builder offered only fields already seen in documents, so a new workspace had none.
Standard fields are always offered now. **Fix** `fcadf73`. **Proof**
`tests/engines/test_automation_live.py::test_a_new_workspace_can_build_data_conditions_before_its_first_document`.

## F-077 — A saved clause check stayed switched off (P2, fixed)

Saving the first sentence of a clause check left it inactive ("Switch the check on when ready"),
so an authored check silently never ran. **Fix** `e8e7592`. **Proof**
`tests/engines/test_clause_assertions_live.py`.

## F-078 — A refused role change hung the request and locked the organization (P1, fixed)

An ADMIN trying to grant ADMIN (only an OWNER may) waited forever: the organization row was locked
`FOR UPDATE` and the independent audit write's foreign-key check waited on that lock while the
request waited on the audit write. **Fix** `9fa6006` (`FOR NO KEY UPDATE` + a bounded wait for
independent audit writes). **Proof** `tests/engines/test_team_and_audit_live.py`.

## F-079 — No service-level (SLO) measurement could ever be recorded (P1, fixed)

Every new measurement window was refused by a NOT NULL constraint, so the 99.9% availability and
latency pages were empty and the hourly recorder failed for every organization. **Fix** `b4e9d13`.
**Proof** `tests/services/test_slo_service.py` (7 red on main).

## F-080 — Webhooks had no "send test event" (built; was F-061)

**Commit** `07f2a9c` (+ migration `p4a2`). **Proof** `tests/engines/test_webhooks_live.py`, which
also proves HMAC-SHA256 signing, retry after backoff and redaction of the signature in the log.

## F-081 — A voided invoice could not be corrected, and a retried invoice job crashed (P1, fixed)

Re-assembling a voided period returned the voided invoice itself; on a retried job the invoice
assembler returned the wrong type and the monthly billing job died on every retry. **Fix**
`5a32dc0`. **Proof** `test_arch15_gate_15_5_15_6_invoices.py::TestGate155Immutability::test_void_and_reassemble_is_the_correction_path`.

## F-082 — Creating a billing account froze the whole organization while Stripe answered (P2, fixed)

**Fix** `667fa5c`. **Proof** `tests/services/test_billing_account_lock.py` (new; red before).

## F-083 — Public API responses carried no rate-limit headers, and their body said "FREE, 0 left" (P2, fixed)

**Fix** `13fb68d`. **Proof** `test_public_api_endpoints.py` (header/body tests) and
`tests/engines/test_api_keys_live.py` (headers count 2, 1, 0 with the real limiter).

## F-084 — A security violation inside an automation action did not stop the rule (P1, fixed)

When an action tried to use a value taken from a document as, say, an email recipient (rule R33),
the action was refused - but a rule set to "continue on error" carried on to its next actions and
the record lost its security marker. **Fix** `5d27323`. **Proof**
`test_arch13_gate_13_5_13_6_engine.py::test_r33_violation_halts_regardless_of_on_error`.

## F-085 — Resolving an already-settled verification answered "not found" (P3, fixed)

**Fix** `b7c08b3`. **Proof** `test_arch13_gate_13_8_verification_api.py::test_resolve_conflict_returns_409`.

## F-086 — Radar and erasure read/deleted text chunks without the workspace filter (P2, fixed)

Correct only while every id was right; no defence if a wrong id arrived, and every query scanned
all partitions. **Fix** `cca3a3b`. **Proof** `test_vector_scoping.py::test_no_unscoped_chunk_access_in_app`.

## F-087 — A retried document could be refused at the spend ceiling for tokens already paid (P2, fixed)

**Fix** `15cb0f3`. **Proof** `test_embedding_metering.py::test_a_replay_at_the_ceiling_is_not_refused` (new).

## F-088 — A tiny "image bomb" avatar could exhaust the server's memory (P1, fixed)

A 6 KB PNG declaring 12000×12000 pixels was accepted and used 1.8 GB of memory. **Fix** `4fec6db`.
**Proof** `test_avatar_upload.py::...::test_a_decompression_bomb_is_refused_before_it_is_decoded`.

## F-089 — SCIM provisioning failed with 500 when the caller's address was not an IP (P2, fixed)

**Fix** `4e53f00`. **Proof** `test_arch16_scim_dunning.py::test_scim_content_type_is_scim_json`.

## F-090 — Saving a BYOK route before adding the provider key answered 500 and broke the list (P2, fixed)

**Fix** `d2fa768`. **Proof** `test_arch23_endpoints.py::test_embedding_route_accepted_for_openai`.

## F-091 — Global search (Ctrl+K) finds documents, entities and cases (built; was F-061)

Across every workspace the user may open and never another; matches file names and extracted
values (invoice numbers); the text is matched literally. **Proof**
`tests/engines/test_global_search_live.py`.

## F-092 — Reviewers see the page with each agent's reading highlighted (built; was F-061)

New `GET …/work-items/{id}/evidence` and `…/pages/{n}.png`; the review workbench shows the page
with one coloured box per agent's reading and says when a reading is not printed at all.
**Proof** `tests/engines/test_review_evidence_live.py`.

## F-093 — The radar did not flag a vendor's changed bank account or round totals (built)

New payment-risk flags (separate store; ARCH-34's pinned vocabulary untouched): BANK_ACCOUNT_CHANGED
(HIGH; accounts shown masked to the last four) and ROUND_AMOUNT (LOW); confirm / dismiss with a
reason; on the Radar page. **Proof** `tests/engines/test_payment_risk_live.py`.

## F-094 — About 230 backend tests were red on main for stale reasons, hiding real coverage (P2, mostly fixed)

They predated deliberate changes (paid-plan gates, removed columns, a renamed field, idempotent
writes, a date baked into fixtures) or used the development database instead of the test one.
Aligned without weakening any assertion; each gate is now covered by an explicit refusal test.
The few left red are design questions (F-095 to F-099, N-021 to N-025).

## F-095 — Is BYOK an Enterprise feature? (fixed in Phase 5, N-021)
The pages call it "Enterprise BYOK" but the server lets any plan store keys and routes.

## F-096 — Locked-out sign-in answers 401, not 429 (closed in Phase 5, N-022)
The brute-force lockout works (proven: `tests/engines/test_login_lockout_live.py`) and answers
exactly like a wrong password; the older test expects 429 with Retry-After.

## F-097 — Oversized avatars: refuse, or shrink? (closed in Phase 5, N-023)
5000×5000 images are shrunk to 1024 px; the older test expects a refusal. (Image bombs are refused
either way, F-088.)

## F-098 — Public token links live under the API-key gateway's `/api/v1/public` prefix (closed in Phase 5, N-024)
Calendar-feed and document-request links are public by design but share the prefix the gateway
test reserves for API-key routes. Moving them would break links already sent.

## F-099 — Remaining stale engineering invariants (fixed in Phase 5)
- `test_owner_set_concurrency::test_for_update_appears_only_in_the_lock_helper` (ARCH-05 gate):
  says no file but the owner helper may lock rows; 14 later services lock their own rows
  legitimately. Proposal: restrict it to locks on organizations / members (N-025).
- `test_storage_boundary::test_no_direct_filesystem_calls_outside_driver`: flags bundled schemas,
  release manifests and a PDF object's `read_bytes()` (not files); needs an allowlist decision.
- `test_pipeline_and_profiles::test_terminal_stages_only_return_to_queued`: the dead-letter retry
  resumes a failed document without `document.queued`; code and test disagree since day one.
- BYOK suites (`test_byok_credential_service.py`, `test_byok_endpoints.py`, 13 tests): assert the
  old "only Groq is routable" rule; ARCH-23 made all six providers routable. Live proof of the
  current behaviour: `tests/engines/test_byok_live.py`.
- `test_storage_validation_ocr::test_registration_does_not_import_paddleocr`: the OCR test module
  itself imports the OCR package (which eagerly loads paddle/torch); registration in a clean
  process loads neither.
- `test_email_change::test_confirming_signs_every_session_out`: timing-dependent - its token has
  no session id, so it fails when minted in the same second as the revocation (passed in the final
  run); real tokens are proven signed out (`tests/engines/test_session_revocation_live.py`).
- `test_pool_profiles::test_unknown_role_warns_outside_production`: passes alone, fails after
  other tests in the same run (order-dependent warning capture).

---

# Phase 5 — production readiness: every open finding worked (branch `claude/blissful-goldberg-hsqxy1`)

Started from `main` at `f86a96c` (Phase 4 merged) with the owner's answers to N-021 to N-025.
"Fixed" below follows the Evidence Rule unless it says otherwise: a test that failed before (named),
the fix (commit), the same test passing, and the rest of the suite still passing (full-suite
comparison in `05-production-readiness.md` §5). Where a fix has no automated test, the entry says
"no automated test" and how it was checked, as CLAUDE.md requires.

## Owner decisions applied (N-021 to N-025)

- **F-095 — BYOK is a Business and Enterprise feature (N-021): fixed.** New `capability.byok` in both
  tiers; the four BYOK writes refuse below Business with 402 `CAPABILITY_REQUIRED`; reads and retiring
  a key stay open after a downgrade (N-003). Sidebar lock, page banner, plan-card label. **Proof** the
  plan-gating sweep with `/byok` as a locked area was red (`PUT /byok/routes` answered 200 to a Free
  tenant), green after; `tests/api/test_byok_plan_gate.py` (8 tests). **Commits** `24a0fb2`, `ea13d14`.
- **F-096 — locked-out sign-in answer (N-022: keep the generic 401): closed.** Behaviour unchanged;
  the stale test now proves the decided behaviour. `3864de5`.
- **F-097 — oversized avatars (N-023: refuse > 50 MP, shrink the rest to 1024 px): closed.** The code
  already did this; the test proves both sides of the line and the stored size. `41e8f99`.
- **F-098 — public token links (N-024: keep them): closed.** The gateway test exempts exactly the two
  token routes and proves they are token links outside the gateway. `23e1c69`.
- **F-099 — remaining stale invariants: all fixed.**
  - ARCH-05 lock invariant narrowed to organization/member rows (N-025), FK-safe locks required: `6f65363`.
  - 12 BYOK tests asserted the ARCH-22 "only Groq is routable" rule: aligned to ARCH-23 (all six
    routable), the unroutable-disclosure rules still proven with a simulated unroutable provider: `947ee81`.
  - Storage-boundary scan: nine non-tenant file reads listed with reasons, pinned to their exact line: `25ad73b`.
  - Pipeline terminal stages: the test now pins the retry-resumes-the-failed-stage design and the
    "never jump to EXTRACTED/COMPLETED" property: `663073c`.
  - OCR import check runs in a fresh interpreter: `8ee83b1`.
  - The email-change sign-out race was a real gap (F-101 below): `306fc51`.
  - The order-dependent pool-profile warning was a real bug (F-100 below): `af6d9c8`.

## New findings from this phase (all fixed)

## F-100 — Running migrations in-process silenced every app logger (P2, fixed)

**Plain language.** `alembic/env.py` loaded its logging file with Python's default
`disable_existing_loggers=True`. Any process that ran migrations itself (the test harness, any script
that migrates and keeps working) switched off every logger the app had already created, so warnings
and errors from those modules vanished. That is why `test_unknown_role_warns_outside_production`
passed alone and failed after any database test. **Proof** red when a DB test runs first, green after.
**Fix** `af6d9c8`. Re-enabling the logs surfaced F-102.

## F-101 — A session-less access token minted in the revocation second outlived the revocation (P3, fixed)

**Plain language.** The revocation cutoff compares whole seconds (`iat < cutoff`), so a token with no
session minted in the same second as a password reset or e-mail change stayed valid until it expired.
Production tokens always carry a session (also revoked through the session row), so the exposure was
test personas and any future session-less issuer. **Proof** `tests/security/test_revocation_cutoff_second.py`
red before, green after. **Fix** `306fc51` (session-less: the cutoff second counts as "before").

## F-102 — One unknown organization made the SLO flush discard every organization's measurements (P2, fixed)

**Plain language.** The SLO recorder wrote all series in one transaction; one series for an
organization deleted between the measurement and the flush failed its foreign key and rolled back the
whole window for everyone ("SLO shutdown flush failed; observations discarded"). Each series now has
its own savepoint and an unknown organization is skipped and counted. **Proof**
`test_a_series_for_a_vanished_organization_does_not_discard_the_rest` red before, green after. **Fix** `deb494c`.

## F-103 — Deleting a user row would have deleted the file records of every document they uploaded (P2, fixed)

**Plain language.** `uploaded_files.owner_id` was `ON DELETE CASCADE` in the database (the model said
`SET NULL`). Every document upload records the uploader as owner, so deleting one user row would have
deleted the company's file records for all their uploads. The app anonymises rather than deletes
users, so this only affected a manual `DELETE`, but that is exactly what an operator does under
pressure. Found by the F-017 drift work. **Fix** migration `p5a1_schema_drift_alignment` (`ad411ec`),
upgrade, downgrade and upgrade run on a real database; the drift gate proves model and database now agree.

## F-104 — Snowflake warehouse sync could not sign with a passphrase-protected key (P2, fixed)

**Plain language.** The connector promises "an unencrypted or passphrase-protected PKCS#8 PEM", but it
handed python-jose the PEM text, which fails on an encrypted key ("Password was not given but private
key is encrypted", reproduced with python-jose 3.3.0). It now signs with the loaded key (PyJWT).
**Proof** `tests/services/test_jwt_library_migration.py::test_the_snowflake_jwt_is_signed_with_the_tenants_key[...]`
(with and without a passphrase). **Fix** `f80b517`.

## F-105 — PDF pages were freed by the garbage collector outside the PDFium lock (P2, fixed)

**Plain language.** PDFium (the PDF library behind thumbnails, OCR, redaction, tables and the
comparison views) must only be used by one thread at a time, so F-050/F-066 put every use behind one
lock. But a PDFium page object is in a reference cycle, so a page that is only dropped is not freed
when the line ends: Python's garbage collector frees it later, on whichever thread happens to be
running, without the lock, and that call goes into PDFium while another thread may be inside it.
That is the same class of fault that crashed the API process in F-050. It surfaced once in the full
suite as "Set changed size during iteration" in the 8-thread stress test. Every page is now opened
with `pdfium_page()`, which closes it inside the lock. **Proof**
`tests/services/test_pdfium_pages_closed_under_lock.py` pauses the collector and checks that no
document closes with a page still open (red at 7 call sites before, green after) and that no file
indexes a PDF document directly (red at 6 places). **Fix** `3b83fc8`.

## F-106 — A client that hung up silently cancelled the request's email (P1, fixed)

**Plain language.** Sign-up, password reset, e-mail change and invitations send their e-mail as a
"background task", which the web framework runs only after the reply has been written to the
browser. When the browser, a phone that switched networks or a proxy closed the connection just
before the reply's last byte was written, the server's write failed, the framework skipped the
background task, and the server swallowed the error: **the person never got the link, and nothing
was logged.** It is not rare: behind the browser suite's proxy it happened to 2 of 4 sign-ups in one
run, and 11 times in two runs of one test. A sign-up that never receives its verification link is a
lost customer. **Proof** `tests/security/test_work_survives_client_disconnect.py` drives the real
sign-up route with a connection that behaves like one the client has left: no verification e-mail
before, one after; e2e "full member lifecycle through email" and "password reset" receive their
mail. **Fix** `8a4253a`: a write to a departed client is now ignored, so the request finishes and
its e-mail goes out; the layer sits outside every other (the ARCH-28 deprecation layer is still the
outermost one that stamps responses).

## F-107 — Every invitee landed on "That workspace is no longer available to you" (P2, fixed)

**Plain language.** After clicking Accept, the page sent the new member to a workspace called
"default", because it expected a field (`workspace_slug`) the server never sent. Every invitee saw
a warning that their access had been revoked, seconds after joining. The server now says which
workspace the invitation granted and the page opens it (or the workspace list, without a warning,
when the invitation granted none). **Proof** `test_response_names_the_granted_workspace_to_open`
and `test_an_organization_only_invitation_names_no_workspace` red before, green after; e2e "full
member lifecycle through email" passes end to end (it had never got past Accept: F-051, then this).
**Fix** `8541f0a`.

## Earlier findings resolved in this phase

- **F-005 (sidebar lock vs server): fixed.** Analytics & BI egress is locked on the warehouse add-on
  (`7adf7b3`); BYOK is locked below Business (N-021). Proof: e2e `02-plan-matrix` now includes both.
  The other three ungated consoles are owner decision N-002.
- **F-007 (repo hygiene): logo/favicon fixed** (1.37 MB each → 14 KB / 12 KB, `1a98cdb`; no automated
  test, compared visually). History rewrite (N-005) and script retirement (N-016) stay with the owner.
- **F-008 / F-054 (no route-level role check; "couldn't be loaded" for members): fixed.**
  `RequireOrganizationRole` wraps the 14 admin-only organization pages and shows "Access restricted"
  with who to ask, before any request (`cc48275`). Proof: e2e `03-role-matrix` "forbidden organization
  pages by URL".
- **F-009 (`/admin` empty): fixed** (`0074f74`; redirect to `/admin/margins`; no automated test,
  checked with the type-checked route table).
- **F-010 (frontend env template): fixed** (`7c7933f`; only `VITE_API_URL` is read; checked by grep).
- **F-014 (slug `request`): fixed** (`39fb0d5`). Proof: `tests/core/test_reserved_slugs_cover_frontend_routes.py`
  reads every top-level route of the web app; red with five missing segments, green after.
- **F-015 (API keys hidden from ADMIN): fixed** (`782d0e3`, N-006). Proof: e2e `03-role-matrix`
  "organization sidebar — A.admin".
- **F-017 (model/migration drift): the 22 real differences fixed** (`b2accf1`, `ad411ec`): 31 lines
  leave the drift baseline (314 → 283). The 283 left are indexes and constraints the database has and
  the models do not declare; they change nothing at run time and the gate stops them growing.
- **F-025 (`organization_addons` model unregistered): fixed** (`b2accf1`; the drift gate proves it).
- **F-026 (storage error = raw 500): fixed** (`02a67de`). Proof: `tests/security/test_upload_storage_outage.py`.
- **F-027 (dev MinIO took the shell's AWS key): fixed** (`f6e4b36`). Proof:
  `tests/infra/test_dev_compose_minio_credentials.py`; rendered with `AWS_ACCESS_KEY_ID` exported.
- **F-028 / F-049 (avatar 404 on every page): fixed** (`a8a05ec`). Proof: `TestHasAvatarFlag`; the e2e
  KNOWN_ISSUES entry is removed and `known-issues.spec.ts` proves no avatar request is made.
- **F-033 (Dodo mode guard): closed.** Dodo's published webhook envelope is `{business_id, type,
  timestamp, data}` with no test/live flag (docs.dodopayments.com, "Webhooks"), so there is nothing in
  the payload to compare; the real N-013 question is answered without a live payload. What keeps a
  test event out of a live deployment is the signing secret, which Dodo issues per mode, and
  `DODO_API_BASE`, which `config.py` already forces to match `DODO_LIVEMODE`. The code now says so
  (`e80c900`, comment only). Keep exactly one Dodo key and secret per environment (RUNBOOK).
- **F-045 (8 GB images): partly fixed** (`3d8cff5`). `scripts/image_requirements.py` derives
  `requirements-web.txt` (web, worker, enrich) and `requirements-ocr.txt` (added in the `ocr` image)
  from `requirements.txt` and the installed dependency graph, so a pin can never differ. Left out
  of the web set: the OCR engine (PaddleOCR, PaddleX, OpenCV, ModelScope and their own
  dependencies), the test tools (moto, pytest) and an unused Kubernetes client: **1.2 GB less in
  each of the web, worker and enrich images**. torch and sentence-transformers stay: the API embeds
  search queries itself (`hybrid_search_service.py`, `chunk_retrieval_service.py`), so the old
  "web: zero ML dependencies" label was wrong and is corrected. **Proof**
  `tests/infra/test_image_requirements.py` (the files match `requirements.txt`; the Dockerfile uses
  them; the API, every non-OCR module and the light and enrich worker start-up checks run with
  everything outside the web set made un-importable), plus a fresh virtualenv built from
  `requirements-web.txt` alone (`pip check` clean, 5.7 GB vs 6.9 GB) in which the full backend
  suite was run (`05-production-readiness.md` §5). **Not done, needs a machine that can reach
  download.pytorch.org (blocked here):** installing the CPU-only build of torch, which would drop
  the 3.7 GB CUDA stack (`nvidia-*`, `triton`) from every image on a server without a GPU.
- **F-039 (dependency advisories): fixed for 12 of 13 packages.** FastAPI 0.136.3 + Starlette 1.7.0
  (`936ff0e`, N-014; pip-audit clean for both), python-jose → PyJWT (`f80b517`; also drops ecdsa),
  pypdf, urllib3, multidict, werkzeug, cryptography, oauthlib, pyarrow (`8bb9c9b`). **Left, with the
  reason:** torch 2.12.1 (2.13 pulls a new CUDA 13 stack; its advisory needs loading untrusted model
  files, which the app never does), setuptools 81 (torch pins < 82), paramiko 3.5.1 (no fixed release).
- **F-048 / F-064 (sign-in limiter counted twice; `RATE_LIMIT_*` ignored): fixed** (`53d9267`, `0f3cfd3`).
  Proof: `tests/security/test_rate_limit_settings.py` (red before for both). Default kept at the
  allowance in effect (10 per 5 min); the owner's N-018 choice is one `.env` line.
- **F-053 (viewer pages fail): fixed as error pages** (`cc48275`): an Access restricted screen instead
  of 403s. Whether viewers get read access stays N-019. Proof: e2e `03-role-matrix` viewer tests.
- **F-055 (BILLING 403 on every organization page): fixed** (`53f383a`). Proof:
  `tests/api/test_billing_role_notifications.py` red before, green after.
- **F-056 (assistant loses the question): fixed** (`1e52864`). Proof: e2e `11-assistant-intelligence`
  "when the AI provider is down…".
- **F-057 (422 hidden behind "status code 422"): fixed** (`84f3074`): the client parser reads the
  `detail={code, message, problems}` shape. Proof: e2e `12-processing` ERP target and corroborator tests.
- **F-058 (404 for "not configured"): fixed** (`07e768b`). Proof: `tests/api/test_not_configured_is_not_an_error.py`
  (3 red before) and the e2e settings and branding tests.
- **F-059 (no upgrade button by URL): fixed** (`4192ad8`). Proof: e2e `02-plan-matrix` asserts "View plans".
- **F-060 ("Claim domain" where domains are off): fixed** (`903ea97`). Proof:
  `test_branding_says_whether_custom_domains_are_served`; e2e `20-organization` expects the message.
- **F-062 (unsaved guard misses workspace General): fixed** (`32b3721`). Proof: e2e `14-configuration`
  "unsaved changes block navigation until confirmed".

---

# Final release (2026-10-06, branch `hardening/final-commercial-release`)

Found by the live end-to-end pass (the full browser suite with a model attached, under the
production Content-Security-Policy), by the OWASP ASVS Level 2 audit in `05-release-readiness.md`,
and by the owner decisions taken under founder authority (NEEDS-OWNER.md). All fixed. "Proof" is the
test that failed before the change and passes after it; the full suites pass with every change.

## F-108 — A legal hold did not stop three of the ways a document is destroyed (P1, fixed)
**Plain language.** A legal hold exists so that evidence is never destroyed while a dispute or
investigation is open. Holding a document stopped the review queue, but not: deleting it from its
page, the nightly retention purge, or a GDPR erasure of the person who uploaded it. Separately,
bulk delete let any member delete documents uploaded by other people. **Fix** (`2d60e40`): every
destroying path asks the retention service first and refuses with 409 `RETENTION_HOLD` (the purge
skips held items and held workspaces; erasure refuses a subject with held documents); bulk delete
deletes only the caller's own uploads unless they are a workspace admin. **Proof**
`tests/api/test_legal_hold_enforcement.py`: 7 of 9 red before.

## F-109 — The nightly backup verify could fail although the backup was good (P2, fixed)
`pg_restore --list` stops reading once it has the table of contents; `openssl` then wrote into a
closed pipe, reported "bad decrypt", and the verify deleted a good backup. A race: 4 of 20 runs here,
almost every night at production size. **Fix** (`6ce327e`): the rest of the stream is drained, so
the whole file is still decrypted and checked; a corrupted backup still fails. **Proof**
`tests/infra/test_compose_backup_scripts.py`: 20/20 on three consecutive runs (16/20 before).

## F-110 — Organization notifications: "Next page" showed the same page (P3, fixed)
The page offset was not part of the cache key, so the next page came back from the cache.
**Fix** (`e3dc770`, with the inbox filters of N-020 item 2). **Proof** the browser notification
tests; `tests/api/test_notification_inbox_filters.py` for the new filters.

## F-111 — The review hub said "Agents disagreed" for items where nobody disagreed (P2, fixed)
Calibrated autonomy parks a held extraction, an audit sample and a memory trial as review items;
the hub labelled all of them as a disagreement between agents, so reviewers could not tell routine
sampling from a real conflict. **Fix** (`b44fddd`, migration `p6a2`): the reason is read from the
item (real disagreement, audit sample, calibration hold, memory trial). **Proof**
`tests/services/test_review_extraction_reasons.py`: 3 of 8 red before.

## F-112 — A question typed while "New" was creating a conversation went to the old one (P2, fixed)
Found only with a model attached: the answer appeared lost. **Fix** (`975d919`): New clears the
selection at once and the new conversation is selected before the list refreshes. **Proof** the
assistant browser tests (the second test's question landed in the first test's conversation).

## F-113 — The first document of a new workspace skipped its AI steps (P1, fixed)
The workspace's AI defaults were created lazily with no provider, and the enrichment step refused
to run for that first document. A new customer's first upload, the most important impression, came
back without classification or extraction. **Fix** (`6465b6d`). **Proof**
`tests/services/test_first_document_enrichment.py`.

## F-114 — Log lines dropped their context (P3, fixed)
Values passed as `extra=` (ids, reasons, counts) never reached the log line, and there was no
structured format for a log service. **Fix** (`c12c9f1`): every `extra` field is appended as
`key=value`, secret-looking keys are masked, long values truncated; `LOG_FORMAT=json` for one JSON
object per line. **Proof** `tests/core/test_log_context_formatter.py`.

## F-115 — A goods receipt was a "duplicate" of the purchase order it quotes (P2, fixed)
The radar's duplicate check used the first document number on the page; a goods receipt quotes its
PO number, so every receipt looked like a duplicate. **Fix** (`cec4b18`): each document role reads
its own number. **Proof** `tests/engines/test_radar_live.py` (new case).

## F-116 — A review-hub tab clicked while the page loaded was lost (P3, fixed)
**Fix** (`66130a3`): the view lives in the URL (`?view=`), so it survives loading, reloads and
links. **Proof** browser test `13-automation-review`.

## F-117 — A wrong password in "Confirm it's you" signed the user out (P2, fixed)
The API client treated every unrefreshable 401 as a lost session, including the answer to a
sign-in attempt. On the sign-in page that only replaced the server's message; inside the step-up
dialog (billing portal re-authentication) one typo ended the session the dialog protects.
**Fix** (`19aac28`): a 401 from `/auth/login` or `/auth/login/mfa` is an error to show.
**Proof** the two-factor browser test (a wrong code shows the server's message and stays on the
page). **Unverified** for the step-up dialog itself: it opens only when a session is more than five
minutes old at the billing portal, which no test can arrange; the code path is the same.

## F-118 — A refused sign-up still said "Check your email" (P2, fixed)
sonner 2's `toast.promise` returns the toast id, not the request, so the sign-up page's `await`
never failed. A too-easy password, the rate limit or a server error all showed the error and then
"Check your email". **Fix** (`34def1f`): `.unwrap()`. **Proof** browser test "a common password is
refused at sign-up" (failed before).

## F-119 — Any 8-character password was accepted; no strength meter (P2, fixed; ASVS V2.1)
**Fix** (`f48235b`): at least 12 characters, and zxcvbn (breached passwords, names, words,
keyboard patterns, the user's email) must score 3 of 4; no composition rules. Sign-up checks
before the account lookup (no enumeration), reset checks before the link is spent. (`7242b3d`): a
strength meter on sign-up, reset and change; a show-password control on sign-in and reset.
Existing passwords keep working. **Proof** `tests/api/test_password_policy.py` (7 of 8 red before);
browser tests for the meter, the refusal and the reveal button.

## F-120 — A session refreshed daily never required signing in again (P2, fixed; ASVS V3.3.2)
Each refresh started a new 14-day window. **Fix** (`01a4bfa`): sign in again 12 hours after
signing in and after 30 idle minutes (`SESSION_ABSOLUTE_LIFETIME_HOURS`,
`SESSION_IDLE_TIMEOUT_MINUTES`; 0 turns either off). **Proof**
`tests/services/test_session_lifetime.py` (2 of 5 red before).

## F-121 — Every page was rebuilt when the profile arrived, losing what was typed (P2, fixed)
**Plain language.** The console formats every date in the user's own time zone. It rendered the
page with the browser's clock first, then, when the profile (time zone, language) arrived a moment
later, threw the whole page away and built it again. Every user has a saved time zone, so this
happened on every page load: whatever was typed or selected in that moment vanished (an
organization name being edited, a file chosen for upload), and every page fetched its data twice.
Found by the full browser run under load (two tests failed this way). **Fix** (`2a8bb7c`): the
console waits for the profile (a failed read falls back to the browser's settings), then renders
once. **Proof** browser test `known-issues` "F-121": with the profile answering 2 s late, the typed
organization name was reset before the change and is kept after it.

## F-122 — API responses carried no Cache-Control (P3, fixed; ASVS V8.2.1)
**Fix** (`c634ee1`): every `/api` and `/scim` response without its own `Cache-Control` gets
`no-store`; images, logos, avatars and streams keep theirs. **Proof**
`tests/api/test_api_cache_control.py` (2 of 3 red before).

## F-123 — SSO advertised localhost on every deployment (P2, fixed)
`app/api/v1/saml.py` built the SAML entity ID, ACS and SLO URLs from
`getattr(settings, "PUBLIC_API_URL", "http://localhost:8000")` and sent
`getattr(settings, "OIDC_REDIRECT_URI", "")` to the identity provider. Neither name was a declared
setting and `Settings` ignores undeclared variables (`extra="ignore"`), so on a real server SAML
metadata pointed identity providers at `http://localhost:8000/api/v1/saml/acs`, OIDC sent an empty
`redirect_uri`, and no environment variable could change it. Enterprise SSO could not complete
anywhere but a developer laptop. Found by the configuration audit (every key read by the app).
The same was true of five identity settings the env templates document (SCIM token lifetime,
rotation overlap and page size; domain re-verification grace and interval) and four tuning values.
**Fix** (`c1068be`): all declared at the defaults the code already used; `public_api_base` is
`PUBLIC_API_URL`, else `FRONTEND_URL` in staging/production (Caddy serves the API on the web app's
host), else `http://localhost:8000` (development unchanged); `oidc_redirect_uri` defaults to
`<base>/api/v1/oidc/callback`. A ratchet test fails on any new `getattr(settings, "NAME")` of an
undeclared name. **Proof** `tests/core/test_sso_public_addresses.py`: 11 red before (production ACS
was `http://localhost:8000/api/v1/saml/acs`), 11 green after; the related suites (config, guard,
template, SAML, SCIM, identity: 341 tests) pass.

## F-124 — SCIM token pepper is a constant (P3, unverified)
`scim_service._pepper()` uses `getattr(settings, "SCIM_TOKEN_PEPPER", None) or
getattr(settings, "JWT_SECRET_KEY", "")` and then `str(...)`. `SCIM_TOKEN_PEPPER` is not declared
(so always `None`) and `JWT_SECRET_KEY` is a pydantic `SecretStr`, whose `str()` is the mask
`**********`, so the HMAC key for every SCIM token is that constant. Impact is small: each SCIM token
carries 320 random bits, so a leaked hash still cannot be guessed. **Not fixed here** because
changing the key invalidates every SCIM token already issued; the right moment is before the first
customer configures SCIM (use `API_KEY_PEPPER` or a declared `SCIM_TOKEN_PEPPER`). Exempted by name in
the F-123 ratchet test until then.

## F-125 — Production Stripe webhook secret was the local CLI secret (P2, owner action)
The supplied `.env.production` carried the same `whsec_` value as `.env`: 64 hex characters, the
format `stripe listen` prints for forwarding to a developer machine. Stripe signs deliveries to a
Dashboard endpoint with that endpoint's own secret, so on the server every event would fail
signature verification and no checkout, renewal or failed payment would ever be recorded. The
finalized file leaves `STRIPE_WEBHOOK_SECRETS` blank on purpose: the start-up guard then refuses to
boot until the endpoint's secret is filled in (verified by rendering the file with
`docker compose config` and building `Settings` from the result).

## F-126 — A concurrent refresh revokes the session the other caller just received (P3, confirmed, open)
**Seen** once in a full browser run of this branch (13-automation-review "an upload fires the
'Document uploaded' rule…": a 401 on `/me/context`); it passed in every other full run (the final
one included) and in three repeats of its file.
The test opens a page and reloads it at once, so the first page's `/auth/refresh` is
cut off by the browser while the server is still processing it. **Evidence** (trace + API log,
2026-10-07 07:20:15): the new page's refresh rotated `e5ba…` → `0097…` and the page received
`0097`'s access token; the cut-off refresh then presented `e5ba…` inside the 10 s grace, and
`_handle_rotated_token_replay` rotated the chain tip `0097…` → `bdd4…`, which revokes `0097`
(`SESSION_CONCURRENT_REFRESH`); the page's next request carried `0097`'s token and was refused
(`AUTH_REJECTED … reason=session_revoked`); the app refreshed again and recovered. Real users meet
the same race with two tabs refreshing together, or a reload during a refresh: one refused request
and a console error, then recovery. **Not introduced here**: `session_service.py`, `deps.py`,
`auth.py` and the client's refresh code are unchanged on this branch. **Not fixed here**: the
remedy is a session-design choice (let an access token of a session revoked only by ROTATION live to
its expiry, or have the grace path branch a sibling session instead of rotating the tip), each with
a security trade-off for revoked devices, so it needs its own failing test and review.

**Fixed (final systemic polish, `cbcf88e`).** The failing tests are
`tests/services/test_concurrent_refresh_f126.py` (4 of 9 failed). The design keeps the two
properties the rotation tests protect (one live session per sign-in; a refresh token presented
twice outside the grace window ends the sign-in):
- an access token of a session retired only by ROTATION stays valid until it expires while its
  sign-in still has a live session (that is the other tab's case: no more 401 after the other
  tab refreshes);
- a token a racing tab retired before its own holder presented it (B in the trace) is served on
  its first presentation, at any age; its second presentation outside the grace is reuse;
- sign-out and "revoke this device" now end the whole sign-in, so the first point cannot keep an
  older access token alive (this exposed F-130);
- the rotation lookup takes a row lock, so two simultaneous refreshes queue instead of forking.
**Proven live** against the running API (2026-10-07): the trace's every step answers 200; the
only refusals in the log are the two checks made after signing out.

## F-127 — The review-queue browser test depends on how many runs the database has seen (P4, test only, open)
**Seen** when 13-automation-review was repeated three times against the same database
("Review queue › type tabs…", 3rd repeat: no button named /Packet split/). **Evidence** (snapshot of
the failure): the queue held 52 items, "page 1 of 3", 25 on the first page; 15 of them were HIGH
duplicate-number findings for `disputed-*.pdf`, one per earlier run of the "disputed extraction"
test, which uploads a fresh copy each time (the radar rightly flags it). The only packet-split item
(the tile still read "Packet split 1") had been pushed to page 2. Passes on a fresh database, as in
CI and in both full runs of this branch; the queue's page size and ordering are unchanged on this
branch. **Not changed here**: the remedy is test isolation (open the "Packet splits" tab before
looking for the item, or clear the run's disputed copies), which belongs in a test-only change.

**Fixed (final systemic polish, `dcc6f57`, `d68d215`).** The test now opens the "Packet splits"
tab before looking for the item. Behind it were two real defects:
- the extraction workbench's list (`GET /verifications`) sorted by `created_at` only and paged by
  offset. Rows written in one transaction share `created_at`, so their order changed between
  requests: the cursor pointed at another document after a refetch and a row could land on two
  pages or none. Seventeen more offset-paged lists had the same flaw (Run history, anomalies, ERP
  postings, corroboration runs, obligations, tables, notifications, conversations, the public
  API's documents, and the SCIM user and group listings an identity provider pages through).
  Each now ends its sort on a unique key. Proof: `tests/api/test_verification_queue_order.py`
  (2 failed) and the source guard `tests/security/test_paged_lists_have_a_stable_order.py`
  (failed on all 18 sites).
- the review hub stayed on a page emptied by resolutions: an empty list and, once everything fit
  on one page, no pager to leave by. Proof: the browser test "a page emptied by resolving its
  items steps back" (failed before).

## Final systemic polish and live engine hardening (2026-10-07)

How these were found: the owner clicked "Reindex knowledge base" on a running server and the
worker crashed (F-128), which no test had caught because the job was marked **untested** in the
coverage ledger. So this pass drove the live stack (API, worker loop, Redis, Postgres, the model
stand-in, the production bundle with the production CSP) the way a person would: a crawler opened
all 39 pages and clicked every non-destructive button (on the populated workspace and on a
brand-new empty one), a sweep called every parameter-free GET on three workspaces (populated,
second, empty: 305 calls, **zero 5xx**), and the named secondary actions were triggered one by
one. The API and worker logs were watched throughout, and the jobs table is the record of every
background job the live system ran: **all SUCCEEDED, none failed or dead** (33 job types).

### F-128 — "Reindex knowledge base" crashed every worker job (P2, fixed)
**Plain language.** The button said "Queued N documents" and nothing happened: each job died
with a spend-limit error and retried until dead. Re-embedding is metered on a non-billable meter
(the tenant already paid to embed), and the spend guard asked for the tenant's limits on it; a
non-billable meter cannot carry a limit, so the guard called it misconfigured.
**Fix** `5e6f997`: limits are looked up only for meters that can carry one. **Proof**
`tests/engines/test_knowledge_reindex_live.py` runs the button's request and the jobs as the
worker does; it failed with the exact live error. **Live**: 8 of 8 then 16 of 16 jobs
`reindex.complete`, no error.

### F-129 — A later reindex queued nothing (P3, fixed)
Jobs were keyed per document forever, so after the first run (or F-128's crash) every click
answered "Queued N" and queued nothing. Now a document is skipped only while its reindex still
waits or runs (a double click), and the backfill meter counts each run. `695e40a`.

### F-130 — Sign-out did not end the session on the server (P2, fixed)
**Plain language.** "Sign out" cleared the cookie in the browser, but the server never learned
which session to end: the refresh cookie was scoped to `/api/v1/auth/refresh`, so it was never
sent to `/api/v1/auth/logout`. The session stayed live for 14 days, stayed in "Active sessions",
and a copy of the refresh token kept working (ASVS V3.3.1). Found while testing F-126; the old
logout tests passed because the browser's cookie was gone. **Fix** `dfbd797`: the cookie is
scoped to `/api/v1/auth` (still never sent to the rest of the API); old-path cookies are
cleared. **Proof** `tests/services/test_sign_out_reaches_the_server.py` (3 of 4 failed).

### F-131, F-132, F-133 — Archiving was not reversible (P2/P3/P3, fixed)
**Plain language.** "Archive" is described as reversible and the "no access" page says an owner
or admin can restore. In fact: restoring a workspace was refused by the same check that blocks
archived workspaces (F-131), and its only Restore button sat on its own unreachable settings
page; for members an archived workspace stayed in the switcher; an archived organization had
no restore at all (F-132); and sign-in could land in an archived organization (F-133).
**Fix** `d3bd89a`, `e7fc1ec`, `fae4a50`: restore routes for both (organization: owner, typed
slug; workspace: owner/admin, workspace limit applies), archived workspaces listed for owners
and admins, the picker shows archived organizations apart with badges and Restore, sign-in skips
archived organizations. While archived, every request into it is still refused (reads and
writes), people keep their sessions (they belong to the person), and API keys deactivated by the
archive stay deactivated after a restore. **Proof** `tests/api/test_archive_lifecycle.py` (6 of
8 failed), the tenant self-check (2 cases failed), `e2e/tests/16-lifecycle.spec.ts` (failed on
the old picker).

### F-134 — Archived tenants kept their background work (P2, fixed)
The nightly anomaly sweep and the procurement re-score (both emit billable usage) walked every
workspace, scheduled warehouse exports kept pushing an archived organization's data, and queued
automations, ERP deliveries and engine jobs ran for it. **Fix** `493efb7`:
`app/workers/tenant_gate.py` skips tenant-activity jobs for a tenant that is not active (the job
succeeds with outcome SKIPPED), and the three cross-tenant sweeps filter archived tenants.
Billing, usage, compliance and housekeeping still run. **Proof**
`tests/engines/test_archived_tenants_live.py` (5 of 5 failed).

### F-135 — Theme toggle and white flash (P3, fixed)
From "system" on a light computer the header toggle went to light (nothing happened); its icon
read the stored choice, not the screen; and the theme was applied only after the app's code
loaded, so dark-mode users saw a white page first. `22528cd`: the toggle and icon use the
resolved theme; `public/theme-boot.js` applies it before first paint (same-origin, as the CSP
requires). **Proof** `e2e/tests/15-theme.spec.ts` (2 of 3 failed).

### F-136 — An old profile picture came back after a reload (P3, fixed)
The signed-in user is cached in the browser and a restored session refreshed it only when
nothing was cached, so a picture or name changed elsewhere never showed until the next sign-in;
and avatar and logo were cached for five minutes at a fixed address. `9e25b07`: the user is
refreshed on every session restore; both images are `no-cache` with an ETag (304 when
unchanged). **Proof** `e2e/tests/17-identity-images.spec.ts` (3 of 3 failed).

### F-137 — Dialogs ignored the keyboard (P3, fixed)
Eleven dialogs did not close on Escape, did not move focus in, and let Tab walk into the page
behind (WCAG 2.1.2, 2.4.3). The API-key and webhook secret panels were marked modal while
inline. `ca3b053`: one hook (`useDialogFocus`) applied to all eleven; the secret panels are
labelled regions. **Proof** `e2e/tests/18-dialog-keyboard.spec.ts` (2 of 2 failed).

### F-138 — DPA export bundle not downloadable on local storage (P3, fixed)
Found by the crawler: "Download" answered 409 "cannot mint presigned URLs" where storage is the
local disk (development and test; production refuses local storage). `e72a65a`: the API streams
the archive itself in that case (owners/admins, audited). **Proof**
`tests/api/test_compliance_export_stream.py` (2 of 2 failed) and a browser download test.

### F-139 — The invite form offered a role the inviter cannot grant (P3, fixed)
Found by the role audit. The server lets an owner invite Member, Billing or Admin and an admin
only Member or Billing (an admin cannot create a peer). The form offered Admin to everyone, so
an admin who chose it was refused after filling it in. The form now offers exactly the roles the
inviter may grant (the same rule, `canAssignOrganizationRole`). **Proof**
`e2e/tests/19-invite-roles.spec.ts` (the admin case failed). The rest of the role matrix held:
the server enforces organization scope (billing, members, SSO, compliance, keys) and workspace
scope (documents, review, workflows) separately, `tests/security/test_role_matrix.py` and the
browser role matrix pass, and both members pages now explain the two kinds of role.

### F-140 — Run history never named the rule (P3, fixed)
Found on the new Run history screenshots: every chain read "Rule abf060bb" although the rules had
names. The executions API filled `rule_name` from a `rule` relationship the execution model does
not have, so it was always null. The names are now looked up for each page in one query.
**Proof** `tests/engines/test_automation_live.py::test_run_history_names_the_rule_that_ran`
(failed with `{None}`); live, the API returns the rules' names.

### Seen live, not defects
- `CRITICAL llm.settle_price_unavailable` for provider `local`: by design (ARCH-50), an unpriced
  operator model is counted as UNKNOWN, never zero, and alerts until the operator prices it. In
  the test stack there is no price for the model stand-in. Owner note N-031.
- `ERROR billing.seat_disclosure_unpriced` when a seat change is previewed: the price book has
  no `billing.seat` entry, so the disclosure says "unpriced". A pricing decision: N-030.
- Billing portal 503 (no Stripe key in the sandbox), identity-domain verification 409 (no DNS
  TXT record can exist here), custom domains 501 (not enabled on this deployment): each refuses
  with a message that says exactly why.

## Built in this release (owner decisions, not defects)
- **Two-factor sign-in** (N-017, `df65332`, `c47a990`): authenticator app (TOTP), ten recovery
  codes, required at sign-in and in the step-up dialog; wrong codes throttled per address and
  capped per account. Proof `tests/core/test_totp.py` (RFC vectors), `tests/api/test_mfa.py`, and a
  browser test that turns it on, signs in with an app code and a recovery code, and turns it off.
- **CSP enforced** on every host (F-044, `c47ae3f`) and a **Permissions-Policy** (`7f8de01`).
- **Notification filters and mark as unread, promo code at checkout, invite from Organization →
  Members, page image beside the fields with in-place correction** (N-020; see the table).
- **Redis password, uptime heartbeat, image pinning steps** (N-017, `2053201`, `4ed724e`, RUNBOOK).

## Still open (not defects of the code; each has an owner and a reason)
- **F-006** — the production compose stack has not been started on a real server (no Docker daemon
  in this environment). RUNBOOK "First deploy" is the check.
- **F-039** — three Python advisories accepted with reasons: torch 2.12.1 (needs loading untrusted
  model files, which the app never does; 2.13 brings a new CUDA stack), setuptools 81 (pinned by
  torch), paramiko 3.5.1 (no fixed release; the SFTP client is egress-checked with pinned host keys).
- **F-045** — images still carry the CUDA build of torch; the CPU build needs download.pytorch.org.
- **F-011, F-017, F-030** — the harmless long tail (defaults not written in the template, 283
  ratcheted index/constraint drift lines, a 30-minute test suite).

---

## Live feedback and Tier-1 elevation (2026-10-07)

How these were found: the owner's live testing after PR #9 (F-141, F-142, F-143), then an audit
of the whole product for the same classes of gap: a mutation that can fail silently, a page that
can take the shell down, background work that can be stranded, a delete the database refuses or
that does not delete what it says. Every item below has a test that failed on the previous code
(backend pytest or the browser suite), as the Evidence Rule requires; F-145 was found by a static
scan of all 267 mutations and its browser proof covers the bulk path (the rest are the same
one-line pattern, reviewed by reading).

### F-141 — Reindex gave no feedback (P2, fixed)
**Plain language.** After "Reindex knowledge base" the page went quiet: the button could be
clicked again, and nothing said whether the background work was running, done or broken.
**Fix** `ac12570`: `GET …/work-items/knowledge-base/reindex/status` reports the active jobs, the
latest run's counts (waiting, completed, failed) and when the last settled run finished. The
card polls every 2.5 s only while a run is active, disables the button (spinner) while a run or
the request is in flight, shows a progress bar, then "Last completed on <time>", and toasts the
start and the end. **Proof** `tests/engines/test_knowledge_reindex_status_live.py` (5 tests; the
route did not exist) and two browser tests (a real run to its completion toast; the running state).

### F-142 — Archived access looked active (P3, fixed)
`/me/workspaces` skipped archived workspaces and never read the organization's status (archiving
an organization leaves its workspaces' own status unchanged). It now returns archived grants with
the organization's name and status and an `archived` flag; the panel shows a muted ARCHIVED badge
with the reason and who can restore, and no longer counts an archived organization's workspaces
as reachable. **Proof** `tests/api/test_me_workspace_grants_archived.py` (3/3 failed) and a
browser test with an archived workspace and an archived organization. `42d3f5b`.

### F-143 — Broken pictures (P3, fixed)
**Plain language.** If a stored picture could not be shown, the sidebar showed a broken-image
icon, and the profile page kept downloading it again forever. **Fix** `a0ee2e7`: one
`useImageFallback` hook (remembered per image, so a new upload is tried again; a broken cached
copy is evicted), used by the avatar, the header logo, the workspace switcher logo and the
settings previews, which fall back to the initials badge; a non-image response (an HTML error
page answered with 200) is refused before it becomes an image. The same fallback covers the page
images (`15c971e`): the document viewer, review evidence, corroborator and redaction studio now
say a page could not be displayed, and a packet-split thumbnail whose request failed no longer
spins forever. **Proof** three browser tests that serve undecodable bytes; on the old code the
profile page had requested the picture 1,155 times.

### F-144 — One page could take the application down (P2, fixed)
The only error boundary sat above the router. Now each layout's content area has its own: a
crashing page shows an in-page card (Try again, Reload) with the sidebar intact, and the next
route clears it; a missing page file after a deploy is recognised as "a new version is
available" (one automatic reload, then the card). **Proof** a browser test that removes a page's
code file mid-session; on the old code the whole app was replaced. `5b91104`.

### F-145 — Mutations that failed silently (P3, fixed)
A scan of every `useMutation` found 20 with no failure feedback (bulk actions, entity re-resolve,
domain verify and SSO bind, IdP activate / role mapping / dry run, marketplace uninstall, preset
apply and enable, SLO reset, analytics test / pause / reset / delete, holiday calendar default /
delete, feed revoke) and two clipboard copies that could reject unhandled (one claimed success
regardless). Each now toasts the server's reason, confirms success, and the bulk bar and presets
show which action is running. `19b012d`; browser proof for the bulk path in `6346f92`.

### F-146, F-147 — Two sweeps nobody ran (P3, fixed)
`executor.reap_stranded` (an automation run whose worker was killed stays RUNNING; this times it
out) and `sweep_due_deliveries` (an email whose job died is never retried; this re-enqueues it)
both existed, one with its own test, and nothing called either. The ten-minute
`pipeline.sweep_stuck` job (light profile, already failing stranded documents) now runs both and
reports the counts. **Proof** `tests/services/test_automation_reaper_scheduled.py`. `a9f1e39`,
`57ad3c4`. A scan of every `reap_*`/`sweep_*`/`purge_*` function found two more uncalled ones,
both housekeeping with read-time checks already in place (expired invitations, expired
sessions/tokens); the invitation case is now visible in the UI (F-148).

### F-148 — Expired invitations looked pending (P3, fixed)
Accepting is refused at `expires_at`, but the Members list kept saying "Pending … expires <past
date>". The row now carries an Expired badge and says to resend (which issues a new link and
expiry). Browser proof. `9ac4681`.

### F-149 — Bulk actions were unreachable (P2, fixed)
**Plain language.** The product could delete, reprocess, export and tag many documents at once,
but the Documents table had no way to select them. **Fix** `6346f92`: a checkbox per row and a
page select-all for contributors and above; the action bar reports each run and keeps refused
rows selected; viewers no longer see Delete, Retry or selection (the server refuses all three);
a sortable "Uploaded" column. **Proof** four browser tests, all failed on the old code.

### F-150 — A rule that had run could not be deleted (P2, fixed)
**Plain language.** "Delete rule" failed with a server error for any rule that had ever run,
because its run history must outlive it. **Fix** `078b21f`: deleting marks the rule deleted
(migration `p7a1_automation_rule_soft_delete`, additive; one Alembic head) and deactivates it;
it disappears from every list, lookup and trigger, cannot be edited or re-enabled, and Run
history keeps its runs as "<rule> (deleted)". **Proof** in `tests/engines/test_automation_live.py`
(failed with the foreign-key violation). Drift check: no new drift.

### F-151, F-152 — Deleted files were kept (P2, fixed)
**Plain language.** Deleting a document, and erasing a person under GDPR, removed the database
records but left the original files in storage. **Fix** `65e773f`, `d628560`: both now delete the
stored files once the deletion is committed (`app/services/storage_cleanup.py`; storage errors are
logged, never raised). A file a supplier invoice keeps as its source document is a financial
record and is kept, as the erasure already does for financial tables. **Proof**
`tests/engines/test_document_delete_releases_file_live.py` (single delete, bulk delete, erasure
failed; the financial-record guard passes).

### F-153 — Members were bare email addresses (P3, fixed)
The member summary now says `has_avatar`; Organization → Members shows the picture (or initials),
the display name and the email. **Proof** `tests/api/test_member_list_identity.py`. `2a6a899`.

### F-154 — Every tab was "FlowPilot AI" (P3, fixed)
The workspace header sets "<page> · <workspace> · FlowPilot AI" from the navigation model the
breadcrumb uses; the organization console "<page> · <organization> · FlowPilot AI"; leaving
restores the product name (`useDocumentTitle`). Browser proof. `c857fa0`.

### Polish in this pass (no defect number)
Automation shows "No automation rules yet" with "Create your first rule" for a new workspace
(it used to suggest clearing filters that were never set) and "—" instead of 0% rates when
nothing has run; the Documents table shows a compact upload date and stops wrapping its headers;
the reindex card keeps its button beside the heading.

### Dependencies
`npm audit` (an advisory CI step) found a new high advisory in `source-map-js`; the non-breaking
fix (1.2.1 → 1.2.2, `6208434`) is applied. Five high advisories remain, all in Tailwind CSS 3's
build-time file watching and globbing (`braces`, `micromatch`, `chokidar`, `fast-glob`): they run
only on the build machine against the project's own config, nothing of them ships in the bundle,
and their fix is the Tailwind 4 migration (a rewrite of the CSS configuration). Accepted, like the
Python advisories under F-039; plan the Tailwind 4 move as its own change.

### Observations (not defects)
- Archived organizations count towards an account's limit of three organizations (restoring one
  therefore never exceeds the limit). Workspaces work the other way (archived ones do not count;
  restore checks the limit). Both are consistent; a browser test that created an organization per
  run had to reuse one instead (`b8954b8`).
- Two more uncalled sweeps exist (expired invitations, expired sessions/tokens). Both are
  enforced at read time already, so they are table housekeeping; the visible symptom (an expired
  invitation reading as pending) is fixed in the UI (F-148).

### Owner decisions taken (N-026 to N-031)
See NEEDS-OWNER.md, "Live feedback and Tier-1 elevation". N-030 and N-031 are implemented
(`53bb7d4`, proof `tests/services/test_price_book_seed_seat_and_local.py`, 6 tests failed first).


## Phase 1 — Document intelligence (2026-10-08)

Branch `hardening/phase-1-document-intelligence`. How these were found: the whole stack live
(Postgres 16 + pgvector, Redis, the API, the worker on every queue, the model stand-in, the
built app) with the API and worker logs watched for tracebacks and 5xx throughout, then each
Phase-1 page driven in a browser as an owner, a contributor, a viewer and a Developer-plan
customer: uploads (single, many, corrupt, duplicate, very long names), filters and search with
wildcard characters, a phone-width pass, the plan-gated pages while their plan loads, and the
document, table, case and packet screens. Every item below has a test that failed on the
previous code before its fix (pytest or the browser suite), as the Evidence Rule requires.

### F-155 — A filtered Documents list reported the wrong total (P2, fixed)
**Plain language.** Searching or filtering the Documents list showed the right first page but
"page 1 of 7" for three matches; the later pages were empty. The count ignored the filters.
**Fix** `b014c74`: the page and its total share one filter function. **Proof**
`tests/api/test_work_item_list_filters.py`.

### F-156 — "_" and "%" in a search box matched everything (P3, fixed)
The text went into a SQL `ILIKE` pattern unescaped, so `_` (any character) and `%` (anything)
listed every document, entity and obligation. A shared helper (`app/utils/like.py`) escapes the
text; global search, which did it by hand, now reuses it. `b014c74`, `440d0a5`. **Proof**
`tests/api/test_work_item_list_filters.py`, `tests/engines/test_search_wildcards_live.py`.

### F-157 — Paging could repeat one document and skip another (P3, fixed)
Rows with the same sort value (same size, same name) had no tie-breaker, so their order could
change between pages. The document id now ends the sort. `b014c74` (same proof file).

### F-158 — "Duplicate detection" was a setting nothing read (P2, fixed)
Document settings offered it, on by default; five copies of one file became five unrelated
documents. Intake now finds the earliest document in the workspace with the same SHA-256 (under
an advisory lock, so copies uploaded at the same moment are caught) and records it
(`work_items.duplicate_of_work_item_id`, migration `p8a1`, additive). The list shows a Duplicate
chip, the document page names the original, the upload toast says which document it repeats.
Copies are flagged, not refused: the forensic radar and repeat uploads rely on them being kept
(the setting's text now says so). `53a3c6c`. **Proof** `tests/engines/test_upload_duplicates_live.py`.

### F-159 — A long file name lost its extension (P4, fixed)
Names over 255 characters were cut at 255, extension included. `fit_filename` keeps it. `53a3c6c`.

### F-160 — One refused file silently dropped the rest of a multi-file upload (P2, fixed)
A corrupt PDF or a type the workspace does not accept threw out of the upload loop: the files
after it were never sent and vanished from the list, the overview did not refresh, and the error
escaped as an unhandled promise rejection. Each file now uploads on its own and keeps its own
outcome (the refused one stays with the server's reason and Retry). The tray states this
workspace's own types and size limit (it always said "100 MB"), the Documents page can upload,
and viewers are not offered an upload the server refuses. `8e014bf`. **Proof** browser suite
`22-phase1-documents` (5 of 6 failed on the old bundle).

### F-161 — An empty workspace showed "Success rate 100%" (P3, fixed)
With nothing finished the rate is now absent and the overview shows a dash. `732b383`.
**Proof** `tests/api/test_dashboard_overview.py`.

### F-162 — The bulk CSV export let a document plant a spreadsheet formula (P2, fixed; security)
Extracted values such as `=HYPERLINK(...)` went into the CSV as they were and ran when the file
was opened in Excel or Sheets. The bulk export now uses the same `safe_text` as the table,
obligation and audit exports. `2bd86c5`. **Proof** `tests/api/test_bulk_export_csv_injection.py`.

### F-163 — A currency written in words stopped every engine after extraction (P1, fixed)
**Plain language.** When the model wrote the currency as "US Dollars" (it often does), saving it
into a three-letter column failed, and because that row is written first, the document silently
skipped anomaly scanning, three-way matching, entity resolution, tables, obligations and case
assembly. `normalize.currency_code()` reads a currency written any way as its ISO code, or
nothing (never a guess, with a warning). `672430e`. **Proof**
`tests/engines/test_currency_words_live.py` (both failed: no role row, `dispatch_failed` logged).

### F-164 — Paying customers saw an upgrade message while a page loaded (P2, fixed)
Ten plan-gated pages (Tables, Cases, Scanned packets, Extraction memory, Entity graph,
Obligations and their detail pages) showed "… is included on the Business and Enterprise plans"
with View plans until the plan arrived (over a second on a slow answer). They now show the
page's outline until then. `1fbb0a6`. **Proof** browser test F-164 (failed on the old bundle).

### F-165 — The closed phone menu was focusable and shaded every page (P3, fixed)
Off screen but not inert: its shadow ran down the left edge, and the first Tab press went into
navigation nobody could see. Closed, it is now inert and hidden from assistive technology.
`4fe647f`. **Proof** browser test (failed: "Tab 1 moved focus into the closed drawer").

### F-166 — Documents was unreadable on a phone (P3, fixed)
Eight fixed columns on a phone cut names to "po...." and printed headers over each other. Below
the tablet breakpoint each document is a card with its full name and actions. `6307d7b`.
**Proof** browser tests "Phone width" (both failed on the old bundle).

### F-167 — Cases and packet review offered actions the role could not take (P2, fixed)
A viewer saw the template editor, Save draft, Publish, Retire and Open case; on a case, Close,
Re-check, Add/Remove document, Withdraw and Request upload link; on a scanned packet, boundary
editing, Approve and Reject. The server refuses all of them (templates need a workspace admin,
the rest a contributor), so the person met a 403 after acting. The controls now follow the role
with a line saying who can act; Cases also confirms each action and labels empty columns.
`4340396`. **Proof** three browser tests (`22-phase1-documents`, F-167; all failed before).

### F-168 — The browser-suite seed uploaded its samples again once a workspace was large (P4, test only, fixed)
The global setup looked for its sample documents on one page of the workspace, asked for with
`pageSize=100`, a parameter the API does not have (it pages with `limit`, default 50). Past 50
documents the samples fell off that page and every run uploaded them again, which broke the
tests that expect exactly the two sample invoices. Each sample is now looked up by name.
`86b2941`. The copies already made in the long-lived local database are flagged duplicates
(F-158 working as designed); a fresh database, as in CI, has none.

### F-169 — Hovering the entity graph restarted its layout (P3, fixed)
**Plain language.** Moving the pointer over a record made the whole graph start moving again
for several seconds, so the record you were reaching for slid away. Hover was React state the
drawing depended on; each hover rebuilt the simulation and the layout effect restarted it. Hover
and search now only redraw. With it: labels carry a halo so edges no longer cross them, touching
nodes keep a ring between them, the legend lists only kinds present, and "Find a record" dims
the rest. `e3bdf90`. **Proof** browser test "Entity graph (Phase 1, F-169)" counts animation
frames after one hover: 66 on the previous code, under 10 now.

### F-170 — A page loading during sign-out sent the revoked session (P3, fixed)
Seen in the full browser suite: the overview mounted just after Sign Out was clicked, asked for
its data with the session the server had just ended, got 401, and the client then tried a refresh
(401 again). Harmless to the person, but the same moment could leave any page in an error state.
While a sign-out is in progress the API client now sends nothing but the sign-out itself.
`25ee4b4`. **Proof** browser test "Signing out (F-170)" (holds the sign-out's answer for 1.5 s
while the overview opens): failed before with both 401s, passes now; `30-auth` passes (13).

### Batch operations verifier: a corrupted package answered 500 (P2, fixed before release)
Found while testing the new module below: one changed byte inside a zip entry raised a CRC
error. It is now reported as tampering (or "not a package" for an unreadable manifest).
`74e13d7`. **Proof** `test_a_corrupted_byte_is_tampering_not_a_server_error`.

### Verification (Phase 1)
- Backend, full suite: 3,450 passed, 1 failed, 9 skipped. The one failure was the
  storage-boundary guard flagging the export-package writer's in-memory `write_bytes` method;
  renamed (`8b4eac7`), the guard and the batch suites pass (69). The guard is unchanged.
- Browser, full suite (production CSP, model stand-in): 333 passed, 5 failed, 1 skipped. One
  failure was F-170 (fixed, `30-auth` 13/13 after). Four are data only on the long-lived local
  database (second copies of the samples from F-168; tests that expect exactly two sample
  invoices); they pass on a fresh database. No traceback or 5xx in the API or worker logs
  during the run.
- Build, both `tsc` projects, lint, self-checks, no source maps, encoding: clean. One Alembic
  head (`p8a2`); drift check: no new drift.

### Checked and not defects
- Every verification reads "agents disagree" (a review hold) while no calibration model exists:
  the calibrated-autonomy design, not a fault.
- `obligations.extract_document` jobs run later than the rest: scheduled delay by design.
- The chunk-size warning in `npm run build` predates this branch (no dependency changed).

### Built in this phase (features, not defects)
- **Batch processing & document dispatch engine** (`66dc0bb`, `7ccac8c`): batches with live
  progress; confidence analytics (histogram, weakest fields, per-type straight-through,
  throughput); schema self-healing with undo; dispatch to straight-through, review or exception
  lanes with reasons, per a workspace policy; export packages with a SHA-256 manifest,
  `SHA256SUMS` and a verifier (VERIFIED / TAMPERED / UNRECOGNISED / INVALID). Capability
  `capability.batch_dispatch` (provisional plan placement: N-032). Migration `p8a2`.
  **Proof** 53 + 7 backend tests, 4 browser tests (`23-batch-operations`), real worker build.
- **Document workbench** (`f20c7c1`): fitted, sticky page viewer with zoom, pan, page jump and
  keyboard shortcuts; per-field confidence; compact header. New read route
  `GET …/work-items/{id}/confidence`.
- **Tables** (`3f729fd`): sticky header, a Σ row that adds each additive column and checks it
  against the table's total row, grouped CSV/XLSX/JSON export, list polish.
- **Overview** (`c2dba06`): page heading, five KPI cards (Failed was returned by the API and never
  shown; it links to the failed documents), document types from the classifier's label beside
  file formats, activity that opens the document, a one-row upload zone.

## Phase 2 — enterprise processing and TruthMesh

Branch `hardening/phase-2-enterprise-processing-and-truthmesh`. Found by driving the running stack
(Postgres 16 + pgvector, Redis, API, worker, Vite, the local model stand-in) through the Phase 2
pages as each role — three-way matching and tolerance policies, ERP posting, process intelligence,
the forensic radar and payment risk, the document corroborator, workflows and run history, the
review hub, clause assertions, redaction and the assistant — while the API and worker logs were
watched for tracebacks and 5xx (none appeared). Every numbered item has a test that failed on the
previous code before its fix, as the Evidence Rule requires.

### F-171 — A matching case that compared no line was "Matched" and could be approved (P1, fixed)
**Plain language.** When extraction found no line items on an invoice, its purchase order and its
receipt, the three-way match compared nothing, found nothing wrong, called the case MATCHED and
offered "Approve match" — an approval resting on no evidence. Such a case now carries a
"nothing compared" finding, stays in Needs review, and approving it needs a written reason; the
calibration labels no longer learn that an empty case is clean. `2959130`. **Proof**
`tests/engines/test_phase2_live_defects.py`, browser `24-phase2-processing` (Three-way matching).

### F-172 — The matching queue printed every amount in rupees (P2, fixed)
The currency was a constant in the queue. Cases now carry the currency of their documents, and the
queue and the comparison print it. `dd59cae`. **Proof** as F-171.

### F-173 — Payment risk missed the changed bank account it exists to catch (P1, fixed)
INV-E2E-1002 asks to be paid into a new account, and the radar said "No invoice changed its bank
account": the model names the field `vendor_bank_account`, which the check did not read. It now
reads the account fields the extractors actually produce (vendor, supplier, beneficiary, payee,
IBAN, remit-to; nested too). `672fdda`. **Proof** `tests/engines/test_phase2_live_defects.py`,
browser `24-phase2-processing` (radar). The older `12-processing` test of the same name only
checked that the radar was not empty, which a duplicate finding satisfied.

### F-174 — The internal vendor key was shown to people (P3, fixed)
The queue and the radar's evidence printed "name:acme industrial supplies". They print the name as
the documents spell it. `dd59cae`, `672fdda`. **Proof** as F-171 and F-173.

### F-175 — Assistant sources shared one React key; the citation drawer squeezed its text (P3, fixed)
Sources carry no id, yet the inline [n] buttons and chips used one as their key (a console error on
every answer) and the drawer printed an empty "Citation ID". On short or narrow screens the passage
sat in a nested box that could be squeezed until the text broke letter by letter. Keys come from the
document and passage; the drawer is rebuilt with one scroll region that keeps its width, previous /
next, copy and open. `64475dd`. **Proof** browser `24-phase2-processing` (F-175, at 1440 and 390 px).

### F-176 — The extraction workbench named documents by an id fragment (P3, fixed)
"d48d4635" and "Document d48d4635" where a reviewer needs the file name. `ce2fb80`. **Proof**
`tests/api/test_verification_names_document.py`, browser F-176.

### F-177 — Process intelligence counted an event once per object it touched (P3, fixed)
"181 events" and, in the same card, "Documents · 182 events": a finding that names two documents is
one event. `f652ae3`. **Proof** `tests/services/test_process_object_event_counts.py`.

### F-178 — In the review hub, "a" (assign to me) also accepted every disputed value (P1, fixed)
**Plain language.** With an extraction item open, the hub's "a" shortcut assigns it to you. The
embedded workbench listened for the same key as "accept all", so one keypress assigned the item
*and* approved every value the agents disagreed on, recorded as your decision. ("e", collapse, also
switched the workbench to editing.) Inside the hub the workbench now leaves the keyboard to the hub.
`fe2d386`. **Proof** browser `24-phase2-processing` (F-178): it sent the resolve request before the
fix.

### F-179 — The rule builder called an empty rule "Ready to save" (P4, fixed)
The summary rail only heard about problems after a first save attempt. It now lists what is left,
neutrally, before one ("3 things left before it can be saved"). `44107eb`. **Proof** browser F-179.

### F-180 — A clause check's Workflows card said "No trigger (paused)" (P3, fixed)
Clause checks listen to "document processed" (one of the two events that trigger covers), so the
rule list's exact match found no trigger; the card also read as a rule with no actions. It now names
the trigger and says the steps are a graph. `cab5c67`. **Proof** browser F-180.

### F-181 — Holding an arrow key in the Redaction Studio left a smear of regions (P3, fixed)
Arrow keys place a copy of the selected region a point away. Each key repeat saved a new region (all
burned into the output) although the code meant to save once on key-up. The offset now accumulates
while the key is held, with a dashed preview, and is saved once. `3bbfff6`. **Proof** browser F-181
(five keydowns saved three regions before the fix, one after).

### Smaller corrections found during the visual pass (no failing test written: unverified)
Fixed while reworking the pages; each is a wording or display correction checked by eye in the
running app, not by a test that failed first, so per the Evidence Rule they are **unverified**:
- Run history: a chain's duration added its runs' durations, overstating chains whose rules ran side
  by side; it is now first start to last finish (`c8c674c`).
- Redaction Studio: the counts under "Regions on this page" were the whole document's; the confirm
  read "Apply 1 redactions?" and promised a download that applying does not start (`29f4a98`). The
  breadcrumb stopped at the workspace name (`46f0cf5`).
- Radar: the severity select clipped ("Any severit"); a reason typed for one finding carried over to
  the next one opened (`6a1f82f`).
- Rule builder: the "On failure" select clipped ("Stop the ru") (`44107eb`).
- ERP posting: "No postings." above "0 posting(s)" (`c05ed77`).

### Fixed while building TruthMesh (before release)
Seen on the live mesh, each covered by `tests/services/test_truthmesh_engine.py`: a payee-account
change counted once per copy of the invoice (its identity is now the vendor and the two accounts); a
procurement policy labelled "Purchase Order" by the classifier became an order (a role of OTHER now
beats a transactional label); termination exposure counted the order and its invoices twice; an
amount change rippled to sibling invoices; a document centroid came back as text (`67bba71`,
`1b6e5c4`).

### Verification (Phase 2)
- Backend, full suite: **3,479 passed, 0 failed, 9 skipped** (50 min). Phase 1 ended at 3,451
  tests (3,450 passed, 1 failure since fixed); the 28 more are this phase's (3 live-defect, 1 API,
  1 process-count, 19 TruthMesh engine, 4 TruthMesh live-pipeline).
- Browser, full suite on a fresh database (production preview, CSP enforced, model stand-in):
  **360 passed, 0 failed, 1 skipped** in 14.3 min (the skip is by design: the "provider is down"
  test runs only without the model stand-in). No traceback, no ERROR-level line and no 5xx in the
  API or worker logs during the run.
- Build, both `tsc` projects, lint, self-checks, no source maps, encoding: clean. One Alembic head
  (`p9a1_truthmesh_engine`); migration up / down / up checked.

### Checked and not defects
- Every matching case reads "Needs review" with "none read" lines: the model stand-in extracts no
  line items, so nothing can be compared (F-171 is what makes that visible).
- The process event log stops at the last sweep; documents uploaded since appear after the next one
  (Sweep now, or the scheduled sweep).
- Clause checks start switched off: by design, a check is turned on after its clause is written.
- The chunk-size warning in `npm run build` predates this branch.

### Built in this phase (features, not defects)
- **TruthMesh — the cross-document digital twin** (`90ca4ac`, `1b6e5c4`, `b0ab2e3`, `67bba71`,
  `33132cc`, `901a77a`). Every processed document becomes a twin (kind, parties, numbers, amounts,
  dates, terms) linked to the others by what ties them — the numbers they cite, shared parties, and
  meaning (pgvector centroids of the document's own chunks, the local MiniLM embeddings; no paid API)
  — with typed, directed relations (bills against, issued under, governed by, version of…).
  Relation-aware rules find conflicts across documents (eleven kinds: exceeds its authority,
  cumulative overrun, duplicate billing, amounts disagree, contradictory dates, outside the
  agreement's term, party not on the agreement, payee account changed, currencies disagree,
  conflicting terms, no authorising document) with stable fingerprints, so a person's "resolved" or "not a conflict" (with a reason) survives
  every rebuild. A what-if engine ripples a delay, an invoked clause, an amount change, a termination
  or a counterparty default through the graph, ring by ring, with breaches and money at risk. The
  cockpit shows a size-weighted risk index, money at risk, a layered document graph, a discrepancy
  matrix and a CSV audit export. Capability `capability.truthmesh` (Enterprise, provisional: N-033),
  migration `p9a1`, 12 routes, worker jobs (index on enrichment, rebuild). **Proof** 19 engine unit
  tests, 4 live-pipeline tests (`tests/engines/test_truthmesh_live.py`, including workspace
  isolation), 5 browser tests (`25-truthmesh`), and the plan matrix and smoke suites.
- **Review hub** (`55b3ab2`): fields to decide as one diff grid (a column per agent, the characters
  that differ from the proposal highlighted, disagreements first, a confidence bar); open counts on
  the tabs; a tab bar that shows when it scrolls; severity stripes; the workbench uses the full width.
- **Workflows and Run history** (`cab5c67`, `44107eb`, `c8c674c`): honest rule cards and builder
  rail; Run history with a page title, a run summary (completed share, blocked, failed, median and
  p95) and a time axis per chain.
- **Forensic radar** (`6a1f82f`): severity tiles that filter, a findings grid, a duplicate matrix.
- **Redaction Studio** (`29f4a98`): toolbar with zoom, single-key shortcuts, regions for this page
  or all grouped by detector with one switch per group, precision legend.
- **Process intelligence** (`0f50386`): a top-to-bottom discovery map with activities in words;
  compact exception-agent proposals.
- **Three-way matching, radar, assistant, review** (Phase 2 fixes above): the queue shows the
  vendor, the document numbers and amounts in the case's currency.

## Phase 3 — final commercial hardening (2026-10-08)

Branch `hardening/phase-3-final-commercial-hardening`. Found by driving the running stack (Postgres
16 + pgvector, Redis, API, the real worker loop, Vite, the local model stand-in) through every
organization console, the identity, billing, marketplace, autonomy and audit hubs, the platform
admin consoles, workspace settings, lifecycle, sign-in and the app shell, as eleven seeded people
across the four plans and every organization and workspace role, while the API and worker logs
were watched for tracebacks, ERROR lines and 5xx (none appeared outside deliberate restarts). A
crawler clicked every control a page offers (414 actions as the Enterprise owner alone, with zero
console errors and zero failed requests) and recorded every refused request per role; every page
was also loaded at 390 px and 768 px. All 13 scheduled sweeps were run by hand with their cron
arguments, and 225 background jobs of 35 types ran to SUCCEEDED (none failed or dead). Every
numbered item has a test that failed on the previous code before its fix, as the Evidence Rule
requires.

### F-182 — The nightly compliance sweep never ran (P1, fixed)
**Plain language.** The retention promise ("documents older than N days are removed") and the
expiry of old data-export archives depend on a nightly job. Its cron line passed one argument the
script does not act on alone, so on a deployed server it printed "Nothing selected" every night and
did nothing. The line now selects the sweeps (`compliance --all --purge --apply`); the purge still
acts only for organizations that turned auto-purge on, and never on documents under a legal hold.
`485b9c2`. **Proof** `tests/operational/test_sweeper_entrypoints.py` now runs every scheduled line
with exactly its arguments (the compliance line failed, the other 16 passed).

### F-183 — The retention purge left the purged documents' files in storage (P1, fixed)
The purge deleted the database rows with its own SQL; the uploaded file stayed in object storage and
its upload record stayed live, so a document the retention policy had "removed" was still held.
It now marks each upload deleted in the same transaction and deletes the stored objects after the
commit, through the same helpers as the delete button (F-151). `c65c2b4`. **Proof**
`tests/engines/test_retention_purge_releases_file_live.py` (a real processed document, backdated past
a 90-day policy: the file was still in storage before the fix).

### F-184 — Reading the last unread notice stranded the page on "Nothing matches" (P3, fixed)
With "Unread only" on, reading the only notice on the last page left "25 total · 25 unread" above
"Nothing matches these filters" with no pager. The page now steps back to the last page with
notices, and the previous page stays on screen while the next one loads. `c4559c4`. **Proof** browser
`26-phase3-consoles` (F-184), with 30 seeded organization notices.

### F-185 — Every organization console's top bar said "Settings" (P3, fixed)
Notifications, Billing and Webhooks all read "<Organization> › Settings", as a second `<h1>`. It is
now a breadcrumb: the organization (a link to General) and the current page. `8f62f38`. **Proof**
browser F-185.

### F-186 — Revenue operations: Cancel on a prompt still sent the action; one click ended a contract (P2, fixed)
"Mark paid" and "Void" asked through `window.prompt`, and Cancel still sent the request (an empty
reference: a 422 and "The action failed."); "Cancel contract" asked nothing. The actions now ask in a
dialog with a text field (confirm disabled until valid), nothing is sent on Cancel, and each success
is confirmed. Drafting a contract picks the organization by name from a new superadmin route,
`GET /admin/revops/organizations`, instead of a pasted UUID. `ebbb54e`. **Proof** browser F-186,
`tests/engines/test_revops_organization_picker_live.py`.

### F-187 — Billing named meters by their internal keys (P3, fixed)
Limits read "*", "llm.input_token", "ocr.page" with "Overage: allow_and_bill"; the seat card read
"Plan: enterprise · active · $ billing". One helper now names every meter and billed event as the
plan cards do ("Total spend (all usage)", "AI tokens in", "Document pages processed"),
"Beyond this, usage is billed as overage", "Enterprise plan · active · billed in USD". `fb74970`.
**Proof** browser F-187.

### F-188 — Requiring single sign-on with no identity provider locked the whole organization out (P1, fixed)
The switch was accepted with no identity provider connected; every member's password session was
then refused and there was no SSO to sign in with (the owners too, with break-glass off). The server
now refuses (409, with the reason) until an active provider exists; the Security tab says why and
asks before requiring SSO. `a1e4e8c`. **Proof** `tests/engines/test_require_sso_needs_an_idp_live.py`.

### F-189 — Two of the eight service levels were never measured (P2, fixed)
"Job completion rate" and "Job end-to-end p95" had no recorder: every dashboard said "No traffic in
this window" however many documents were processed (Enterprise buys a priority SLO). The worker now
records each organization job's terminal state and its enqueue-to-finish time. `a6a7b9a`. **Proof**
`tests/engines/test_job_slos_are_measured_live.py`.

### F-190 — The BYOK page counted the self-hosted model as "on your own keys" (P2, fixed)
The self-hosted model is priced at a declared zero with the same cost basis as a tenant's own key
(N-031), so an organization with no key stored read "On your own keys 86%". Only calls the metering
zeroed for the tenant's key count now. `8bdf313`. **Proof**
`tests/api/test_byok_endpoints.py::TestSavingsCountsOnlyTenantKeys`.

### F-191 — Archiving an organization said it could not be undone (P3, fixed)
"Reactivation is a support request, not a button" and "Archive permanently", although an owner
restores an archived organization from All organizations & workspaces (F-132). The page now says so.
`bf97f28`. **Proof** browser F-191.

### F-192 — The Billing role could not read usage (P2, fixed)
The role exists for a finance contact, yet the usage endpoints admitted owners and admins only: the
page showed "couldn't be loaded" three times. Reads now admit OWNER, ADMIN and BILLING
(`RequireOrgUsageReader`); the page offers each action only to the role the server allows (plan and
payment: owner; spend limits: owner and admin). `ff692f6`. **Proof**
`tests/api/test_billing_role_reads_usage.py`, browser F-192.

### F-193 — A spend limit you set disappeared from the page (P2, fixed)
After saving, the form showed only the plan's limits, and "Currently in force" ignored the limit just
set. Limits you set are now listed, and "Currently in force" is the tighter of the two. `ff692f6`
(shared component with F-192). **Proof** browser F-193.

### F-194 — A member opening Billing by its address got a broken page (P3, fixed)
The route had no role guard: "Loading billing…" forever, with every request refused. It now shows
Access restricted before any request is made. `37013ed`. **Proof** browser F-194 (976 problems before).

### F-195 — A failed request was re-asked about 30 times a second (P1, fixed)
The query client refetches on every mount; Billing hid its body while a query was pending, and a
child reading the same query remounted on each answer. A 403 (or an outage) looped for as long as the
page was open: 976 failed requests in 15 seconds from one tab, a load multiplier on the API during
any incident. A failed query is no longer fetched again because another component mounted
(`retryOnMount: false`); Billing shows the failure with "Try again". `e7c289a`. **Proof** browser F-195.

### F-196 — Every new workspace was set to an AI model the provider had retired (P1, fixed)
New workspaces were given `mixtral-8x7b-32768`, retired by Groq in March 2025 (and
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` on 16 August 2026): in production the first
document in any new workspace would have failed at the provider. New workspaces take the platform's
listed default (`openai/gpt-oss-20b`); BYOK suggests the models Groq serves; a data-only migration
(`q1a1_retired_groq_models`) moves existing workspaces off retired models. `e053c34`. **Proof**
`tests/engines/test_new_workspace_model_is_served_live.py`.

### F-197 — Workspace settings asked everyone for the organization's invitations (P3, fixed)
A contributor got a 403 and "No active pending invitations found."; a workspace admin who is not an
organization admin was offered an invite form the server refuses. The directory is now shown to
organization owners and admins; others are told who manages invitations. `fb156e0`. **Proof**
browser F-197.

### F-198 — Matching tolerances offered preview and publish to people who cannot (P3, fixed)
### F-199 — The next tolerance version started from zeros and reset every other tolerance (P2, fixed)
The "Next version" form always started from zeros, so changing one field and publishing silently
reset price slack, quantity slack and the candidate window for every case scored afterwards. It now
starts from the version in force; contributors and viewers read it without publish controls.
`00fb9a2`. **Proof** browser F-198, F-199.

### F-200 — The assistant offered a viewer New, Delete and Send, all refused (P3, fixed)
Those are now offered to contributors and above; a viewer is told what they can do. `861101c`.
**Proof** browser F-200.

### F-201 — On a plan without custom branding every brand control sent a refused save (P3, fixed)
The controls are now read-only with a line saying why; removing a logo on file stays possible.
`afaff67`. **Proof** browser F-201 (three 402s before).

### F-202 — Unit economics reported the self-hosted model as BYOK traffic (P2, fixed)
As F-190, for the operator's margin view: "BYOK traffic · 110 events" with no tenant key stored.
`edc3152`. **Proof** `tests/services/test_cogs_margin_service.py::test_f202…`.

### F-203 — The audit log pushed the whole page sideways on a phone or tablet (P3, fixed)
Screen-reader-only labels inside an unpositioned scroller escaped its clipping, making the page about
800 px wide at 390 px. The scroller (and the shared `SCROLL_X` class every console table uses) is now
positioned. A crawl of every page at 390 and 768 px found no other overflow. `0061c17`. **Proof**
browser F-203.

### F-204 — An organization's maximum session age was shown as in force but never applied (P2, fixed)
**Plain language.** The Security tab of Enterprise identity read "Sessions end after N hours" from
the organization's policy, and the owner can set that limit; nothing enforced it, so sessions lasted
the platform's 12 hours whatever the organization asked. With no limit set, the tab said "No maximum
session age is set" although every session ends after 12 hours. Now: refresh enforces the strictest
limit among the organizations a person is an active member of (the platform's still applies); the
endpoint validates the value (5 minutes to a year, a real integer: it stored 60 seconds or text
before) and accepts null to remove it; the read reports the platform's limits; the tab states the
limit in force and lets the owner pick a shorter one. `03bccf8`, `4d09072`, `2b87b9d`. **Proof**
`tests/services/test_organization_session_limit.py`, `tests/engines/test_session_age_policy_live.py`,
browser F-204, and live: a session aged past a 1-hour limit was refused at refresh (401,
`organization_session_limit` in the log).

### F-205 — Anyone could sign a person out through the public SAML logout endpoint (P2, fixed)
**Plain language.** Single logout lets the identity provider end a person's sessions. The endpoint
read the session index from the request and revoked every matching session without checking who
sent it, so anyone who knew a session index (it passes through the person's browser) could sign
them out, repeatedly. A logout request is now accepted only when signed by a live certificate of an
active identity provider named in its Issuer (same algorithm allowlist and certificate windows as
sign-in), and only that provider's sessions end; anything else gets 403. `064cb55`. **Proof**
`tests/engines/test_saml_logout_must_be_signed_live.py` (an unsigned and an attacker-signed request
both signed the person out before), the 324 existing SAML tests, and live (403 from the running API).

### F-206 — Signing out on purpose sent you back to where you were, and could send a request without a token (P3, fixed)
**Plain language.** Clicking Sign Out cleared the session and, in the same step, the "signing out"
marker, while the workspace was still on screen. The route guard then read the exit as an expired
session and wrote `/login?redirect=<workspace>` (every time, on `main` too), the opposite of the
documented intent that a deliberate sign-out does not take you back; and any query the page
refetched in that moment went out without a token (a 401 the strict browser policy caught about
once in ten runs: `/me/profile`, another time `/dashboard/overview`). A deliberate sign-out now
keeps the marker raised, so the request guard of F-170 holds every request and the guard treats the
exit as voluntary, until the sign-in layout mounts and lowers it before its own queries run; a new
session lowers it too, so a later involuntary expiry still keeps its destination. `00bae2a`.
**Proof** browser 30-auth "signing out on purpose lands on a plain sign-in page" (failed with
`?redirect=%2Fcaretakers-global%2Ffinance`); it, "sign out ends the session" and F-170 passed 30 of 30
repeats.

### Smaller corrections (no failing test written: unverified)
Wording and display corrections checked by eye in the running app, not by a test that failed first,
so per the Evidence Rule they are **unverified**:
- Members: roles in words; a refused role change or removal shows the server's reason instead of a
  guess (`8ec6258`, `cdf4a22`). Ownership transfer names the candidates.
- Identity: the Security tab describes signed single logout instead of an inert "session sync" flag
  that nothing reads (`f27dd46`); SCIM token failures say so (`8ec6258`).
- Service levels: each target described in the customer's words, not "claimed jobs reaching
  SUCCEEDED rather than DEAD" or "see `slo_recorder`" (`5015a54`).
- Unit economics: a zero-revenue banner and the rate card in dollars per million tokens (`4802ccb`).
- Sentence case on the sign-up screen and workspace Settings → General; the invite form's email and
  role labels are attached to their fields (`cddb596`, `396c386`).
- The organization sidebar shows a truncated entry's full name on hover (`166bf08`).

### Verification (Phase 3)
- Backend, full suite: **3,527 passed, 0 failed, 9 skipped** (48 min). Phase 2 ended at 3,479 passed;
  the 48 more are this phase's (live-defect, session-limit, SAML logout, sweeper, billing-role,
  BYOK, margin and RevOps tests). No backend file changed after this run.
- Browser, full suite on a fresh database (production preview, CSP enforced, model stand-in):
  **380 passed, 0 failed, 1 skipped** in 12.3 min (the skip is by design: the "provider is down" test
  runs only without the model stand-in). The first full run had 2 failures, both fixed before this
  one: the API-keys console header added in this branch read "shown once", the phrase a test uses to
  detect the one-time secret panel (`a29d764`, header reworded, test unchanged); and F-206. No
  traceback, no ERROR-level line and no 5xx in the API or worker logs; 403 background jobs of 38
  types, all SUCCEEDED.
- Build, both `tsc` runs, lint, self-checks, no source maps, encoding, `npm audit --omit=dev` (0),
  `pip-audit` (the same 5 accepted advisories): clean. One Alembic head (`q1a1_retired_groq_models`);
  migration up / down / up checked; drift 283 known, 0 new.

### Checked and not defects
- AI settings answered 404/409 in the platform organization's workspace: the seed creates that
  workspace directly, without the service that creates its settings row; product-created workspaces
  have one.
- Retention purge "missing": it exists, run by cron (F-182 was its arguments).
- "Paid checkout is not configured: STRIPE_SECRET_KEY…" on the plan cards: the development stack has
  no Stripe key by design (F-125 is the owner's test-mode setup).
- The build's chunk-size warning predates this branch (the entry chunk is 412 KB gzipped; the item is
  in the release-readiness open list).

### Built in this phase (elevation, not defects)
- **One console header and real tabs** (`7270773` and the console commits): a shared `PageHeader`
  (icon, organization eyebrow, title, one-line description, actions) on every organization console,
  the identity and billing hubs, marketplace, autonomy, the audit log and the platform admin
  consoles; URL-synced, keyboard-operable tabs (`useUrlTab`, `TabList`, `TabPanel`: roving tabindex,
  arrow keys, Home/End) on Analytics, Enterprise identity, Sovereign and Revenue operations.
- **Two-party ownership transfer** proven end to end in the browser (`a3961a0`): two people who sign
  up for the test, the offer, the acceptance and the role swap.
- **Organization session limit** as a real control (F-204), and signed single logout (F-205).

## Final systemic polish & traceback sweep (2026-10-09)

Live stack: Postgres 16 + pgvector 0.8.0, Redis, uvicorn API, the real worker (`--loop all
--profile all`), local model stand-in, production bundle. Full browser suite: **380 passed, 0 failed,
1 skipped** (by design). API and worker logs: **0 tracebacks, 0 5xx responses, 0 ERROR lines**;
**400 jobs, all SUCCEEDED**. No live defect surfaced in this sweep.

| ID | Severity | Area | Finding | Evidence | Status |
|---|---|---|---|---|---|
| F-207 | Low (polish) | Corroborator | Materiality showed as a raw 0–1 score ("0.90") in the comparisons list ("Highest" column), the run summary, the difference detail and the matrix. Now a percentage ("90%"), and the column reads "Highest materiality". | `e2e/tests/12-processing.spec.ts` "compare the PO…": failed on `main` code (`Received: "0.90"`), passes after the fix; the whole file 11/11. | Fixed |

## Live bug hunt: fuzzing, double clicks and dev-mode rendering (2026-10-09)

**How these were found.** Earlier sweeps clicked every page and called every parameter-free GET,
and found nothing new. This pass attacked the live stack (API, the real worker `--loop all
--profile all`, Postgres 16 + pgvector 0.8, Redis, the model stand-in, Vite on :5173) the ways real
people and real networks do, while tailing the API and worker logs:

1. **An API fuzzer** over all 609 operations in the OpenAPI document, as real seeded users
   (Enterprise owner, Developer viewer, platform super-admin, and a throwaway Enterprise tenant for
   writes): ids that are not UUIDs, random UUIDs, harvested real ids, edge query values, and for
   every write a valid body, an edge-value body (20 000 characters, NUL, emoji, 2^63, dates in
   year 9999), a wrong-type body, malformed JSON and an array. Every 5xx was traced to its source.
2. **A double-click sweep**: every POST/PUT/PATCH sent 3 to 8 times at once with the same body.
3. **Odd uploads and races**: empty, truncated, encrypted and fake PDFs, 1x1 and 6000x6000 images,
   Unicode and 300-character names, duplicates; documents deleted 0 to 5 s after upload while
   their jobs ran. All handled (clear 400s, clean job completion, nothing orphaned).
4. **The full browser suite against the Vite dev server** (not the production bundle), where React
   reports problems that production builds stay silent about.

| ID | Severity | Area | Finding | Evidence | Status |
|---|---|---|---|---|---|
| F-208 | Medium | Enterprise identity | A domain, connection or SCIM key id that is not a UUID reached Postgres from 8 identity routes and returned a 500 with a traceback; role mappings and the mapping dry-run read their bodies unchecked (`payload["attribute_name"]`, `int(priority)`, `.items()`), and the JIT default role was stored unvalidated. Now 404 for a bad id and 422 naming the field. | `tests/api/test_identity_admin_malformed_input.py`: 29 of 30 failed before, 30 pass. Live: `POST …/scim-keys/not-a-uuid/rotate` 500 → 404. | Fixed |
| F-209 | High | Team | Inviting an address that already had a pending invitation (to change the role, or because the email never arrived) crashed: the service called `invitation_crud.update_invitation_status`, which never existed. The new invitation now supersedes the old one (revoked, with who revoked it). | `tests/api/test_reinvite_pending_email.py` failed with the live `AttributeError`, passes. | Fixed |
| F-210 | Low | Batch operations | The batch progress tile put a progress bar (`<div>`) inside a `<p>`; React flags invalid nesting in development. Only visible with the dev server, which is why preview runs never saw it. | `e2e/tests/23-batch-operations.spec.ts` in dev mode failed on it; 4 of 4 pass after. | Fixed |
| F-211 | High | Enterprise identity (SSO) | The connection builder offers "Metadata URL: the server fetches and parses it" and lets the owner create a SAML connection with nothing else; the server stored the URL and never read it, so the insert broke `ck_idp_saml_fields` and returned a 500 (an OIDC connection without issuer or client ID broke `ck_idp_oidc_fields`). The server now fetches the metadata through the SSRF-safe identity client and fills the entity ID, SSO/SLO locations and signing certificates; a connection still missing what its protocol needs is a 422 with the reason, which the builder shows. | `tests/api/test_idp_connection_from_metadata.py`: 10 of 10 failed before, pass. Live in the browser: "The metadata could not be fetched: …" instead of a 500. | Fixed |
| F-212 | **Critical** | Whole API | **Two quick clicks on "Change role" froze the entire API** (every endpoint, health check included) until a restart, with the organization row locked in Postgres. 129 routes were `async def` but did synchronous database work on the event-loop thread; the role change returned on its "unchanged" path holding the organization lock, and the next click waited for that lock *on the event loop*, so the first request could never finish: a permanent self-deadlock. A real change raced by its repeat froze the same way. Routes that never await are plain `def` again (FastAPI's thread pool), the role change ends its transaction on every early exit, and a guard test fails on any new `async def` route that does not await. | `tests/engines/test_concurrent_requests_do_not_freeze_the_api_live.py` (real uvicorn, 4 clicks at once): before, 1 of 4 and 0 of 4 answered and health timed out; after, 4 of 4 in 5 s. `tests/core/test_async_routes_must_await.py`. Live: a `py-spy` dump showed the event-loop thread blocked in `lock_organization_for_owner_change`. | Fixed |
| F-213 | Medium | Billing | "Reconcile seats" let gateway exceptions out of the route: with no Stripe key (every environment until F-125 is done), an outage, or an unknown subscription, a 500 with a traceback. Now 503 / 409 with a code and a message the seat manager shows; nothing recorded. | `tests/services/test_seat_sync_gateway_errors.py`: 3 of 3 failed before, pass. | Fixed |
| F-214 | High | Whole API | Once requests ran in parallel, the same request twice at once returned a 500 on 8 routes (holiday calendars, BYOK routes, tolerance policies, invitations, tags, packet splits, case rule results, automation triggers): both passed the existence check and the second INSERT met a unique constraint. A global handler now answers a unique or foreign-key conflict with 409 CONFLICT (other integrity refusals 422), the BYOK PUT is a true upsert, and 28 error handlers no longer read expired ORM attributes after a failed flush (which turned the conflict into `PendingRollbackError`). Expected refusals are logged as warnings, not ERROR tracebacks. | `tests/engines/test_concurrent_duplicates_are_not_500s_live.py` (8 concurrent): invitation race 500 on every run before; 3 of 3 runs green after. | Fixed |
| F-215 | High | Worker (OCR) | Once an OCR job had loaded Paddle, stopping the worker (every deploy, scale-down or restart) aborted it with Paddle's "C++ Traceback … Termination signal" instead of a graceful drain: `import paddle` replaces Python's SIGTERM handler, and the lazy import ran on a supervisor thread where Python cannot restore it, so the job in progress was cut off and sat CLAIMED until its lease expired. A worker whose profile allows Paddle now loads the OCR provider on the main thread at startup, switches Paddle's handlers off, then installs graceful shutdown. | `tests/operational/test_worker_graceful_shutdown_with_paddle.py` (real `app.worker.main` in a subprocess): C++ abort before, exit 0 after. Live: with Paddle loaded, SIGTERM logged `worker.shutdown.requested` and all five loops drained. | Fixed |

Not defects, recorded for the owner: the `batch.expand_archive` and `work_items.bulk` job handlers
are registered but nothing enqueues them (bulk actions run inline; zip archives are not accepted
by the upload path), so the ledger rows for them stay `untested`. The browser test
"a deploy while the tab is open" (F-144) only works against the production bundle (it removes a
hashed chunk) and fails by design under the dev server.

## Invitations, the workspace team and the event loop (2026-10-10)

**How these were found.** The owner reported a "zombie" pending invitation and an empty Actions
column, and asked for the remaining async routes, the invitation flows for existing and new people,
and the invitation email. Each item was driven live (API, worker, Vite dev server, real mail sink)
and, where it was a defect, proven by a failing test first. Two more defects turned up on the way
(F-220 while converting the uploads, F-221 while tracing the accept path, F-225 while
following a sign-up through verification). An independent adversarial review of the session's diff
then found F-226 to F-229, each proven and fixed. Its fifth point (each refused sign-up while an
organization is full emailed the inviter again) is F-234, fixed with the owner decisions N-034 to
N-039 (F-230 to F-235) on 2026-10-10.

| ID | Severity | Area | Finding | Evidence | Status |
|---|---|---|---|---|---|
| F-216 | Medium | Team | Workspace Settings listed every invitation the organization ever sent as "pending": an accepted invitation whose person was later removed still showed with Resend and Revoke, both refused ("already accepted"), and a revoked one never left the list. The list endpoint now takes `?status=` (validated, repeatable; no filter keeps the full history), the pending lists ask for `PENDING`, the page keeps only pending rows, labels Resend/Revoke per address and marks a lapsed one "Expired; resend to renew it". | `tests/api/test_pending_invitations_are_only_pending.py` (filter and 422 failed before); `e2e/tests/27-invitations-and-team.spec.ts` showed the reported list in the browser before, passes after. | Fixed |
| F-217 | High | Team | Removing a member (Members page, leaving, or directory deprovisioning) left any invitation still pending for their address: it held a seat and **its link let the removed person straight back in** (accept reactivates a deactivated membership). Removal now revokes every pending invitation for the address in that organization, with one REVOKED audit record each (reason MEMBER_REMOVED). | `tests/api/test_member_removal_withdraws_invitations.py`: 3 of 3 failed before (pending, rejoined with 200, deprovision left it pending); pass after. | Fixed |
| F-218 | High | Whole API | F-212 made the routes that never await plain `def`. The routes that do await (uploads, the assistant stream, upload parts, webhooks) and **the sign-in dependency every authenticated request runs** still did their database, storage and model work on the event loop: while one waited (a lock, a slow query, the model call, up to 25 s per attempt for an ordinary assistant message), every other request in the process waited too. The blocking work now runs in the threadpool: the sign-in lookups (the principal is still set on the request's own context), the tenant context dependencies, the public API key dependency, the assistant message (`send_chat_message` was a coroutine that never awaited), the document, avatar and public-request uploads, upload parts (which also end their transaction before the body arrives), both webhook receivers, the automation rule test, the stream's preparation, retry reseal and billing settle (shielded so a disconnect cannot leave a reservation unsettled), and live-collaboration publishes. The F-212 guard now checks the route's own body (not a nested generator), the app coroutines it awaits and async dependencies. Left on the loop deliberately: the stream's Redis frame buffer and generation-slot calls, each bounded by the 250 ms Redis socket timeout and self-disabling after a failure. | `tests/engines/test_blocking_io_does_not_freeze_the_api_live.py` (real uvicorn; one blocking step per route slowed to 3 s, `/health` must answer in under 1 s): **10 of 10 failed before** (`/health` 3.0 to 6.1 s), pass after. `tests/core/test_async_routes_must_await.py` failed three ways before (the stream route, `send_chat_message`, six dependencies). Security and public API suites (470) and the stream, upload, webhook, automation, collaboration and assistant suites (398) pass. | Fixed |
| F-219 | Medium | Team | Workspace Settings → Team members: the Actions column was empty for the owner, the organization admins and the current user, and held only a Remove link for others; the role control sat in the Role column. Each row now says something: the role control (Admin, Contributor, Viewer, only roles the actor may grant) and Remove for a member whose workspace grant the admin may change; a muted badge ("Owner", "Org admin", "You", "Org admins only") otherwise. Someone who cannot manage the team gets no Actions column and a "Leave workspace" line under the table. The table is named by its heading; its headers are column headers. | `e2e/tests/27-invitations-and-team.spec.ts` "F-219": failed on the old page (no Owner badge, no role control in the Actions cell, Actions column shown to a contributor), passes now with a role change and a removal driven through the new controls. | Fixed |
| F-220 | Medium | Cases (public link) | An empty file, or one over the size limit, sent to a public document-request link answered **500**: the spool ran outside the route's `try`. Now 422 `FILE_REJECTED` / 413 `FILE_TOO_LARGE`, and the link stays open for a corrected upload. | `tests/engines/test_public_document_request_refusals.py`: 2 of 2 failed with the unhandled `FileValidationError`, pass after. | Fixed |
| F-221 | Medium | Team / seats | The last seat could never be filled by an invitation. The seat count is members plus pending invitations, so it already includes the invitation being accepted or resent; checking those with ">=" counted it twice: the invitee was told "no seats available" and the inviter could not resend. Accept and resend are now refused only past the limit; a new invitation, and an acceptance after the seats were filled another way, are still refused. | `tests/services/test_invitation_last_seat.py`: accept and resend failed with `SeatLimitExceededError` before, pass after; the two refusal cases pass before and after. | Fixed |
| F-222 | Medium | Invitations (new user) | Someone invited with no account had to leave the invitation, register on the general page typing the address, wait for a verification email, sign in and find the invitation again, whose token lived only in the first tab. `POST /auth/register/invitation` takes the token and a password (no address: it is the invitation's, so a request naming one is a 422), creates the account already verified (the emailed token proves the address), accepts and opens a session in one transaction; an address with an account answers 409 `INVITATION_ACCOUNT_EXISTS`, and a weak password, expired or used invitation or full organization creates nothing. The preview says whether the address has an account; the page shows a summary card and either the sign-up form (address filled in and locked) or "Sign in as <address>" (the sign-in page fills the address from `?email=`). Its preview type matches what the server sends. Case A (an existing user in other organizations) was already sound and is now pinned: they get exactly the granted role and workspace, nothing else changes. | `tests/api/test_invitation_signup.py`: 7 failed before (no route, no `has_account`), 8 pass; `e2e/tests/28-invitation-acceptance.spec.ts` "Accepting an invitation" (newcomer signs up and lands in the workspace; existing account signs in, returns, joins) failed on the old page, passes now. | Fixed |
| F-223 | Low | Invitations (email) | The invitation email was a div layout with a `<style>` block (Outlook's Word engine and some Gmail clients ignore both: no width, a flat link for a button) and did not show the workspace and role as a card. It is now a table layout with inline styles, a 600 px shell that narrows on phones, a FlowPilot header, a card (organization, role badge, each workspace with its role badge), a bulletproof button (VML for Outlook), preview text, dark-mode colours for Apple Mail and a security footer (real expiry and how long is left, do not forward, reply to reach the inviter). The invite panel said links last "7 days"; they last `INVITATION_TTL_HOURS` (72 by default), so it now points at the expiry shown per invitation. | `tests/templates/test_invitation_templates.py`: four new tests failed on the old template, pass; the zero-workspace test's "no `<table>` at all" became "no workspace list" (layout tables are now expected), stated in the commit. Rendered in Chromium at 760 px, 375 px and in dark mode. | Fixed |
| F-224 | Medium | Invitations / sign-in | "Sign out and switch account" on the invitation page only cleared the tab: the session and its refresh cookie outlived it, so the account the person meant to leave was still signed in. It now ends the session on the server first. | `e2e/tests/28-invitation-acceptance.spec.ts` "Switching account": `/auth/refresh` answered 200 after the switch before, 401 after. | Fixed |
| F-225 | Low | Sign-up | A sign-up that began somewhere (an invitation, a deep link: `/register?redirect=…`) lost its way after verification: the server puts the validated destination in the verification link's fragment, but the verify-email page cleared it and "Continue" always opened a bare sign-in. The page now reads it before clearing, validates it again, and signs in with `?redirect=` (or goes straight there when already signed in). | `e2e/tests/30-auth.spec.ts` "returns there after verifying": failed before (the link carried the destination, Continue dropped it), passes with the other sign-up tests. | Fixed |
| F-226 | **High** | Invitations / identity | **An organization that sends mail through its own SMTP could create a verified account for any address it invited.** Invitations go through the organization's own server when it has one (HARDENING-T2:D22), so whoever runs it reads every accept link. With the token alone they could sign up through the one-step invitation sign-up (F-222) and get a verified, signed-in account for an address they do not control, or accept as an unverified account they had registered for it and have the address marked verified (that path predates F-222); the preview also told them whether an address had an account; and a later single-sign-on login by the real owner attaches to the existing account by email. The send now records, and commits before the link leaves, that it went through a server FlowPilot does not run (`organization_invitations.delivered_off_platform`, migration `r1a1`); such a token opens no one-step sign-up (409 `INVITATION_SIGNUP_UNAVAILABLE`, answered before anything about the address), verifies nothing on acceptance, and the preview answers `has_account: null`. The page then offers sign-in or the ordinary sign-up (address filled in, back to the invitation after verification, F-225). | `tests/engines/test_invitation_token_trust.py`, on the old code: the relay operator's sign-up answered 201 with a session, the squatter's acceptance marked the address verified, the resent token kept the mark; all pass now. e2e "organization's own mail server" failed on the old page (it offered the one-step sign-up), passes. Migration up/down/up, no new drift. | Fixed |
| F-227 | Medium | Invitations | After "Sign out and switch account" (F-224), a newcomer whose address had no account was sent to a sign-in page for an account that does not exist, losing the one-step sign-up. The page now stays, signed out, and shows the sign-up (sign-in only when the address has an account). | e2e "to an address without an account" failed before (sign-in page), passes; the with-account case passes before and after; both still check the old session no longer refreshes. | Fixed |
| F-228 | Low | Invitations | A sign-up refused because the invitation was revoked, used or unknown meanwhile left an error under a form that could never succeed; a failed profile read right after a successful sign-up reported a failed sign-up. Now "Invitation not available" with the reason; the profile case loads the destination (the session exists). | e2e "withdrawn while its sign-up form is open" failed before, passes. | Fixed |
| F-229 | Low | Live collaboration | F-218 moved the presence publish to worker threads, so two announcements for one workspace could publish out of order (an older snapshot last, with the digest holding the newer one, so the self-healing tick did not correct it). Announcements for a workspace now run one at a time. | `tests/services/test_collab_presence_order.py`: published [2, 1] viewers before, [1, 2] after. | Fixed |
| F-230 | Low | Invitations | N-037 decided: invitation links lasted 72 hours; they last 7 days (`INVITATION_TTL_HOURS` 168). | `tests/api/test_invitation_owner_decisions.py::test_an_invitation_link_lasts_seven_days` (3 days before). | Fixed |
| F-231 | Medium | Invitations | N-036 decided: accepting an older invitation moved an admin back down to the invitation's role. Accepting now only raises an active member's role; a deactivated member rejoins with the invitation's role. | `test_accepting_never_lowers_an_existing_role` (MEMBER before, ADMIN now); the raise and rejoin tests pass. | Fixed |
| F-232 | Medium | Invitations | N-034 decided: an invited address with an account nobody verified was sent to sign in to an account its owner never created. A platform-delivered invitation's sign-up takes it over: new password, verified, every old session ended. Verified accounts still sign in; off-platform invitations (F-226) unchanged. | `test_the_sign_up_takes_over_an_account_whose_address_was_never_verified` (`has_account` true and 409 before); `test_a_verified_account_is_still_sent_to_sign_in`. | Fixed |
| F-233 | Medium | Invitations, SSO | N-035 decided: an organization that requires SSO could be joined with a password account, which its policy then refused. The preview says `sso_required`, the password sign-up is refused (409 `INVITATION_SSO_REQUIRED`) and the page offers "Sign in with single sign-on". | `test_an_organization_that_requires_sso_is_joined_through_sso` (no `sso_required`, 201 before); browser 28-invitation-acceptance "sends the invitee to single sign-on". | Fixed |
| F-234 | Low | Invitations, mail | While an organization was full, every refused sign-up or acceptance sent the inviter another "no seats" email (only the rate limit bounded it). One notice per invitation per 24 h (Redis `SET NX`; without Redis, as before). | `test_a_full_organization_tells_the_inviter_once_not_on_every_attempt` (3 emails before, 1 now). | Fixed |
| F-235 | Medium | SSO | N-039 decided: the first SSO sign-in linked to an existing account by email even if its address was never verified, so whoever registered it kept its password and sessions. The link now takes the unverified account over (verified, password unusable, sessions ended, audited); verified accounts are linked untouched. | `tests/services/test_jit_unverified_account.py` (unverified and old password still valid before). | Fixed |

**CI.** Backend CI had been red on `main` and on every pull request since the platform mail relay
became mandatory: the backend steps declared no `PLATFORM_SMTP_*`, so 24 tests failed and the run
stopped at its fifth failure. The three backend steps now declare a relay at a dead port (nothing in
them sends mail). `pip-audit (advisory)` is red on `main` too (torch 2.12.1, setuptools 81.0.0,
paramiko 3.5.1 advisories); it is outside the CI gate and upgrading torch is the owner's call (N-038).

**Owner decisions applied.** N-032 and N-033: Batch operations and TruthMesh are on Business and
Enterprise (the TruthMesh capability joined the Business tier; tests and browser checks updated).

## Pre-launch campaign, session 1: tenancy, roles, plans, seats, billing and usage limits (2026-10-11)

**How these were found.** Reading the money and access model end to end (organization creation,
the seat check, the seat events, the quota layer, every upload path), then proving each suspicion
with a failing test on the real API (and, for races, a real uvicorn) before fixing it. Owner
decisions taken under delegated authority are N-040 to N-047 in NEEDS-OWNER.md; the generated
source of truth is `docs/hardening/TENANCY-AND-PLANS.md`.

| ID | Severity | Area | Finding | Evidence | Status |
|---|---|---|---|---|---|
| F-236 | **High** | Plans | **A new organization was put on no plan at all.** `POST /organizations` never assigned a tier, so every organization created through the product resolved none: AI on the platform account was refused (no `llm.platform_key` entitlement) and the metered limits fell back to the platform defaults (2,000 OCR pages and 2,000,000 input tokens a month, 20x Free). New organizations now start on Free; the plan seed puts organizations created before on Free on the next deploy. | `tests/api/test_new_organization_starts_on_free.py`: "the new organization resolves no plan at all" before; 2 pass. | Fixed |
| F-237 | **High** | Seats | **Seats were never enforced.** `organizations.seat_limit` was the only ceiling, nothing wrote it, and the check returned early when it was NULL: every organization, Free included, could add unlimited members, and a paid plan's purchased seats were never consulted. SSO/SCIM provisioning had its own cap reading a tier row named `seats` that no tier ever carried; SCIM reactivation had no check. One check (`seat_capacity_service`) now bounds invitations (issue, resend, accept), SSO and SCIM provisioning and SCIM reactivation, under a per-organization lock; a refusal is 409 `SEAT_LIMIT_EXCEEDED` with `details` (reason, plan, capacity, used, remedy), and a SCIM 403 error body for SCIM. (While building it, taking the new seat lock after superseding a pending invitation deadlocked two concurrent re-invitations; the seat lock now comes first. Caught by `test_a_raced_invitation_is_never_a_500` before commit.) | `tests/services/test_seat_capacity_every_path.py`, `tests/api/test_seat_limit_refusal_api.py`: 5 failed before (a third person on Free got 201, a paid org past its seats, SSO and SCIM reactivation past them); pass. Live race `tests/engines/test_concurrent_quota_live.py`: 8 invitations for the last seat, exactly one 201. | Fixed |
| F-238 | High | Seats, billing | **There was no way to buy a seat, and detected seat drift was never corrected.** The only seat operation was the owner's "Reconcile", which set the gateway to the number of active members (releasing the seat a pending invitation held); `billing.seat_added/removed` and the drift sweep's `billing.seat_sync_needed` were internal outbox events, which nothing consumes. Now `PUT /billing/seats` buys or releases seats (owners, admins, billing managers; refused at a price other than the one shown, below the seats in use, or without a paid plan); checkout cannot sell fewer seats than are in use; reconcile keeps invitation seats; the drift sweep reports only members past the purchased seats and queues a real `billing.seat_sync` job. Billing → Seats and the invite panel show capacity, used and available. | `tests/services/test_seat_purchase.py` (3 drift/reconcile tests failed; purchase tests failed with AttributeError), `tests/api/test_seat_purchase_api.py` (6 failed: no route, no checkout floor). Browser `e2e/tests/31-seats.spec.ts`. | Fixed |
| F-239 | **High** | Plans, Free | **Free was a free product, not a taste.** Only tokens, OCR pages and storage were metered; documents, assistant messages, file size, pages per document and workspaces were unlimited on every plan, and storage was sampled but never enforced at upload. New meters `document.upload` and `assistant.message`, a static plan-limit vocabulary (`limit.seats`, `limit.workspaces`, `limit.file_size_mb`, `limit.pages_per_document`), and one admission service (`plan_admission`) enforced at the intake every upload path shares (browser, API key, multipart sessions, batch archives in the worker, public document-request links), at re-processing (single and bulk), workspace creation and restore, and both assistant routes. Every refusal is a 402 with `details` (reason, limit key, ceiling, current, resets_at, remedy). | `tests/api/test_free_plan_limits.py`: 11 failed on the previous code; 15 pass. `tests/engines/test_public_document_request_plan_limits.py`. | Fixed |
| F-240 | **High** | Uploads (abuse) | **A resumable upload session had no total size.** Parts were bounded at 64 MB each, but up to 10,000 parts reached storage before the assembled file was checked: one session could park 640 GB, past any plan. The session now records each part's size (migration `s1a1`); a part that would take the total past the declared size or the plan's file size is refused (413) before it is stored, and a declared size past the plan is refused at creation (402). | `test_multipart_parts_cannot_add_up_past_the_plan_file_size` and `test_a_multipart_session_larger_than_the_plan_allows_is_refused_at_creation` failed before; pass. `tests/engines/test_batch_ingestion_live.py` still passes (smaller-than-part-size parts). | Fixed |
| F-241 | Medium | Plans, Free (abuse) | **Each Free organization had its own Free allowance**, and an account may own three: three times the Free plan. The allowance is now one pool per owner account across all their Free organizations, archived ones included, counted and locked together. | `test_a_second_free_organization_shares_the_owners_allowance` failed before; passes. | Fixed |
| F-242 | Medium | Pipeline, quotas | **A document could be accepted and then stranded.** OCR pages were checked only when the worker ran, so documents accepted while pages remained could all land in `QUOTA_BLOCKED`. Pages are now reserved at upload (used pages plus the pages of documents still waiting for OCR) and re-processing is checked before it is queued; an accepted document is never stranded by a count. | `test_ocr_pages_are_reserved_at_upload_so_nothing_is_stranded_in_processing`, `test_reprocessing_is_refused_up_front_when_the_pages_no_longer_fit` failed before; pass. | Fixed |
| F-243 | Medium | Assistant | Assistant messages were unlimited on every plan, and the streaming route answered a quota refusal with a plain-text 402 (no machine-readable reason). Each message is charged when accepted, before the model runs; both routes answer the standard 402 envelope. | `test_free_allows_30_assistant_messages_and_refuses_the_31st[messages/stream, messages]` failed before; pass. | Fixed |
| F-244 | Medium | Plans | **The plan ladder was not monotonic**: Developer's input tokens were ALLOW_AND_WARN (free overage, bounded only by the cost ceiling) while Business billed them, so the cheaper plan was more generous. Developer tokens now refuse (raised to 15M/2M so the user-facing counts are reachable). | `tests/scripts/test_plan_ladder_is_monotonic.py` fails on the old seed ("business llm.input_token is ALLOW_AND_BILL where developer is ALLOW_AND_WARN"), passes. | Fixed |
| F-245 | **High** | Plans, money | **Enterprise lost money when used to its limits.** Allowances were per organization while the price is per seat, so a one-seat Enterprise organization could use 1,000,000 OCR pages and 10 TB of storage for $799: about $2,310 of cost, a −189% margin. Paid allowances are now per seat, pooled across the organization, and Enterprise storage is 1,000 GB per seat; every paid plan keeps at least 60% margin at its ceilings. | `tests/scripts/test_plan_unit_economics.py` fails on the old seed (−189%) and on per-seat 10 TB storage (37%); passes. | Fixed |
| F-246 | Low | Public links | A public document-request upload was capped only at the platform's 100 MB and, once uploads are charged, a refusal would have shown the anonymous uploader the organization's plan message. The spool is capped at the organization's plan; a refusal says only "this organization cannot accept this document right now, contact the sender", and the link stays open. | `tests/engines/test_public_document_request_plan_limits.py`: the refusal leaked the plan wording before; 2 pass. | Fixed |
| F-247 | Low | Access screens | Eleven lock and paywall cards said "Ask an organization owner" without saying who that is. The member-readable billing summary now names the people who can change the plan (the owners, the only role that can) and every card and the payment-failed banner names them; a first version also named billing managers, who buy seats but cannot change the plan, and `test_only_people_who_can_change_the_plan_are_named` failed on it. The summary is readable by every role: the first browser run showed the billing role (a finance contact) refused with a 403 on three pages, because the summary had been written for members only. | `test_the_member_billing_summary_names_who_can_change_the_plan`, `test_every_organization_role_can_read_who_to_ask[BILLING]` (failed first); browser checks in `32-free-plan`, `03-role-matrix` (A.billing), `26-phase3-consoles`, `31-seats`. | Fixed |
| F-248 | Medium | Usage screens | A member uploading documents never saw where the organization stood (the usage screens are for owners, admins and billing), and the upload screens capped files at the workspace setting only. A member-readable `GET /workspaces/{id}/usage/plan-allowance` feeds an allowance line on the upload screen and the assistant (warning at 80%, a stop with "Upgrade" or the names of who to ask at 100%), and the upload tray refuses files over the plan's size before sending them. The usage dashboard lists the customer-facing meters first, warns at 80% and links "Upgrade plan" at a reached ceiling. | `test_every_member_sees_the_allowance_the_refusals_use`; browser `e2e/tests/32-free-plan.spec.ts`. | Fixed |
| F-249 | Medium | Roles, obligations | **An organization owner or admin could not subscribe to a workspace's obligations calendar.** Owners and admins are admins of every workspace without an explicit grant, but the feed table's foreign key pointed at the explicit grant, so the request failed with a misleading 409 "changed by another request". The feed now belongs to the organization membership (removing the member still deletes it) and effective workspace access is checked when it is issued and on every poll. Review assignment and obligation ownership keep requiring an explicit grant on purpose (you assign work to the workspace's team). | `tests/engines/test_member_removal_revokes_access.py` (failed first with the 409; now proves an admin's API key, calendar feed and session all stop working on the request after removal, and a demotion applies on the next request); migration `s1b1`. | Fixed |
| F-250 | Medium | Billing, overage | **Gemini usage past a billed token allowance was never billed.** The platform pays for Groq and Gemini, but only Groq had an overage rate; past a Business or Enterprise ALLOW_AND_BILL token ceiling, Gemini tokens raised a CRITICAL `quota.overage_unpriced` and billed nothing. Tokens on a customer's own key (OpenAI, Anthropic) raised the same alarm. Gemini now has overage rates (at least twice its cost); own-key tokens bill no overage by decision (N-048). | `tests/scripts/test_every_overage_is_priced.py` (failed first for Gemini, OpenAI and Anthropic); `tests/api/test_paid_overage_policies.py` (Gemini overage not billed and own-key alarm both failed first; the file also proves WARN continues free and shows OVER, and REFUSE answers a 402 with the way on, on the shipped plans). | Fixed |
| F-251 | Medium | Plans, money | **The margin check priced tokens at the cheapest model.** The unit-economics test (F-245) used Groq's 20B model, but the platform also serves Groq's larger models and Gemini on its own keys at up to three times the cost; a seat whose tokens all went there kept 44% on Enterprise. Enterprise now includes 500M input / 125M output tokens per seat (was 1B / 250M), billed past that (N-046). | `test_a_paid_seat_on_the_dearest_platform_model_still_keeps_half[enterprise]` failed first (44%); ladder and tenancy-document tests pass with the new figures. | Fixed |
| F-252 | Medium | Plans, Free | **Free could build and run unlimited automations** (emails, webhooks, AI extraction on every document), against the brief's Free plan. Automations are now a capability from Developer up (N-049): the API refuses creating, changing, test-running and marketplace installs with a 402, the worker skips rules of an organization without it, and after a downgrade rules can still be seen, switched off and deleted. The Automation page and the marketplace show the plan banner and disable what would be refused. | `tests/engines/test_free_has_no_automations.py` (failed first: Free created a rule, 201); browser `32-free-plan` (automations). Five existing tests that install or test-run rules on an organization with no plan now put it on Developer first; their assertions are unchanged. | Fixed |
| F-253 | **High** | Billing | **A paying customer could be charged twice.** The plan page offered "switching to a paid plan starts a new checkout"; a checkout creates a new gateway subscription and charges for it, while the database holds one live subscription per organization, so the second was charged and could never be recorded and the first kept charging. Checkout is now refused (409 PAID_SUBSCRIPTION_ACTIVE) while a paid subscription is live, and the plan page sends the owner to the billing portal to change plans (N-050, with a one-time portal setting for the owner). | `tests/api/test_checkout_while_subscribed.py` (failed first: the checkout passed every check and went to the gateway); 159 checkout-related tests pass. | Fixed |
