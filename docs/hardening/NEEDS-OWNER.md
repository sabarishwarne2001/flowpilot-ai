# Decisions only the owner can make

_FlowPilot AI was renamed Servabo on 2026-10-16. This file is history and keeps the old name._

Each item says what the choice is, why it matters, and what the campaign does
until you decide. Nothing here blocks work: the campaign continues with other
items.

## N-001 — Branch naming for this campaign (RESOLVED)
CLAUDE.md says to work on `hardening/<topic>` branches. The cloud session that
ran Phase 0 was given the branch `claude/new-session-uaku80` by the session
harness and may push only there. Phase 0 changes only `docs/hardening/`, so
its work sits on that branch.
**Default until you decide:** each phase pushes to the branch the session
allows, and the PR title names the intended `hardening/<topic>`. If you want
real `hardening/*` branches, start the next session with that branch name, or
rename the branch on GitHub.

**Resolved 2026-09-30.** You asked for `hardening/baseline`, and this session's
push to `hardening/baseline` was accepted, so real `hardening/<topic>` branches
work. Phase 1 is PR #2 from `hardening/baseline`.

## N-002 — Which organization pages should be plan-gated?
Today these have **no plan gating** anywhere (UI or API): Enterprise BYOK &
models, Partner marketplace, Service levels, Data governance & compliance.
"Analytics & BI egress" is gated on the server (Business add-on) but not
locked in the sidebar (F-005).
**Decide:** for each page, which plan should include it? The campaign will then
make the sidebar and the server agree.

**Phase 5 status.** BYOK is decided (N-021: Business and Enterprise) and done. "Analytics & BI
egress" is now locked in the sidebar where the warehouse add-on is missing, matching the server
(F-005). **Still open:** Partner marketplace, Service levels and Data governance have no plan gating
anywhere; they stay open on every plan until you decide.

## N-003 — What may a customer do after downgrading?
Reads and deletes of premium data (API keys, webhooks, analytics destinations,
custom domains, branding, email settings) are allowed on any plan, while
creates and updates need the plan. That looks deliberate: a customer who
downgrades can still see and clean up. (F-004)
**Decide:** is "read + delete after downgrade, no create/update" the policy?
If yes, Phase 2 tests will enforce exactly that.

## N-004 — License for the repository (DECIDED)
`LICENSE` is empty. A public repository with no license is "all rights
reserved" by default. That protects you, but it can confuse contributors and
enterprise buyers' legal review.
**Decide:** keep the repo private or proprietary, or pick a license. This is
not something the campaign should choose for you.

**Owner decision 2026-09-30: proprietary, all rights reserved** (commercial,
closed-source SaaS). Phase 2 writes `LICENSE` accordingly (repo hygiene).
**Still needed:** the exact legal name of the copyright holder (you, or your
company). Until you say otherwise, the notice will read "FlowPilot AI". Also:
the repository is currently public on GitHub. For closed-source software,
consider making it private; that is a GitHub setting only you can change.

## N-005 — Rewrite git history to drop `backend/stripe.exe`?
The 38 MB binary is deleted from the current tree but stays in history
(commits `d790047`, `1884891`). Removing it needs a history rewrite and a
force-push to `main`. Every existing clone and fork then has to re-clone.
**Decide:** rewrite (smaller, cleaner repo) or leave it (no disruption). The
campaign will not force-push `main` either way. It will only add ignore rules.

## N-006 — Who may mint API keys and manage webhooks: OWNER only, or ADMIN too? (DECIDED for API keys)
The sidebar shows API keys, Webhooks, Audit log and Enterprise identity only
to OWNER. The API lets ADMIN create, rotate and delete API keys, manage webhook
endpoints and export the audit log. (F-015)
**Decide:** which is the intended rule? The campaign will then make the UI and
API match, with tests.

**Owner decision 2026-09-30: OWNER and ADMIN may both create API keys.** The API
already allows this; the sidebar must show API keys to ADMIN (F-015, Phase 4).
Webhooks, audit log and enterprise identity were not covered and stay as they
are until you decide.

**Phase 5 status.** Done: the sidebar shows API keys to ADMIN (F-015, `782d0e3`). Webhooks, audit
log and enterprise identity stay OWNER-only in the sidebar (an ADMIN who opens them by URL is served,
as the API allows) until you decide them.

## N-007 — Where will production run? (DECIDED)
Sweepers, retention and backups run from host cron (`backend/deploy/cron.d`).
The production compose file does not run cron (F-006).
**Decide:** a single VM with Docker Compose plus host cron, or a managed
platform (Render, Fly.io, Railway, Kubernetes)? The answer decides how Phase 2
fixes scheduling and backups.

**Owner decision 2026-09-30 (confirmed again at the Phase 2 kickoff): a Linux
host running Docker Compose (a VPS).**
Phase 2 therefore keeps `docker-compose.prod.yml` and adds scheduling for the
sweepers and backups that today live in host cron (F-006). The options are
installing `backend/deploy/cron.d` on the VPS or adding a scheduler container;
Phase 2 proposes one with a test.

## N-008 — Which MinIO image should the project trust?
MinIO stopped publishing `minio/minio` and `minio/mc` on Docker Hub, and they
now return "not found". Phase 1 switched both compose files to pinned
`pgsty/minio` and `pgsty/mc` images, a community rebuild of MinIO from source,
so that `docker compose up` works again (F-018 item 7).
**Decide:** keep the pgsty images, build MinIO yourself, or (recommended by the
existing comment in `docker-compose.prod.yml`) use a managed object store in
production (AWS S3, Cloudflare R2 or Backblaze B2) and keep MinIO for
development only.
**Default until you decide:** pgsty images, pinned by release tag, for dev and
for the optional `self-hosted-storage` profile in production.

**Owner decision 2026-09-30 (Phase 2 kickoff): approved, keep the pinned
community rebuild `pgsty/minio` (which includes `mc`).** Nothing to change.
The recommendation to use a managed object store in production stays open as
an option, but is not required.

**DECIDED (campaign session 4):** production uses a managed object store (AWS S3, Cloudflare R2 or
Backblaze B2), as `.env.production.template` recommends; the app refuses to start in production
without `S3_BUCKET` (F-351). The pinned `pgsty/minio` image stays for development and for the optional
`self-hosted-storage` profile. Reason: a single server's disk is a single point of loss for customer
documents, and a managed store's signed download links reach the customer's browser directly (MinIO's
internal address does not; F-356 streams files where links are not available).

## N-009 — When should production run the lossy ARCH-40 "contract" migration? (DECIDED)
The newest migration, `arch40_step3_contract_ai_settings`, drops three unused
`ai_settings` columns. It refuses to run unless `ARCH40_CONTRACT=1` is set,
so that "add new columns" and "drop old columns" ship in different deploys.
Tests, CI and local development now set the flag (their databases are empty or
disposable). Production does not, so on a **new** production database the
`migrate` container fails today (F-024).
**Decide:** (a) Is there a production database with real customer data yet?
(b) If not, the simplest answer is to set `ARCH40_CONTRACT=1` in production
too. If yes, run the step once, deliberately, after a backup, then set the
flag permanently.
**Default until you decide:** production compose is unchanged. Phase 2 will not
change it without your answer.

**Owner decision 2026-09-30 (Phase 2 kickoff): keep `ARCH40_CONTRACT=1` enabled
for dev, test and staging.** You did not say anything about production, so
Phase 2 treats production as still open and does the safe thing: the flag is
read from the environment (default `0`), so staging sets it in its own `.env`
and production only runs the contract step when you deliberately set it after
a backup (procedure in `docs/RUNBOOK.md`). **Still needed from you:** whether
a production database with real customer data exists yet. If it does not, you
can set the flag in the production `.env` for the first deploy and then remove
it.

**Owner decision 2026-10-02 (Phase 3 kickoff): DECIDED. This is pre-launch; no
production database with real customer data exists.** So the first production
deploy may set `ARCH40_CONTRACT=1` in the production `.env` (nothing real can be
lost), run `migrate` once, and then remove the flag again. Phase 3 did not change
any compose file; the runbook procedure still applies.

**Phase 5 note.** Nothing changes: production still runs the contract step only when you set
`ARCH40_CONTRACT=1` for the first deploy (RUNBOOK 9.2 step 6). The schema-alignment migration added in
Phase 5 (`p5a1_schema_drift_alignment`) is not lossy and runs on every deploy.

## N-010 — Which verification gates are still authoritative?
33 of the 78 `verify_*.py` gates fail (F-029). Several look stale: they
expect columns that later migrations removed. CLAUDE.md forbids reading or
editing `verify_*.py`, so the campaign cannot tell a stale gate from a real
regression by looking inside it.
**Decide:** may the campaign (a) read the failing gates to triage them, and
(b) retire gates that check a schema the product no longer has? Until then CI's
`backend-gates` job stays red, and each failing gate is listed by name in
`01-baseline.md`.

**Owner decision 2026-09-30 (Phase 2 kickoff): (a) yes, the campaign MAY READ
failing `verify_*.py` gate files to diagnose what they test. It must NEVER edit,
weaken, delete or skip a gate script to make it pass.** (b) was not granted:
no gate is retired. A stale gate is reported here and in FINDINGS.md, and you
decide. The exception covers reading failing gates only; `apply_*.py`,
`backend/evidence/`, `arch07_*`, `arch08_*`, PDFs and certification files stay
off limits.

**Phase 2 note.** I did not use the reading exception. Every failing gate is
either a stale check (a column or route that later work removed) or already
logged as a finding (F-022, F-023, F-025). Phase 2 only made sure it did not add
a failing gate: the gate list before and after is compared in
`02-security-deploy.md`.

## N-011 — Confirm the plan table and prices before launch (DECIDED)
The plan names, limits and prices live in `backend/scripts/seed_quota_tiers.py`.
At the time of writing: **Free** $0; **Developer** $49; **Business** $299;
**Enterprise** $799 per seat per month, and Enterprise is sold **self-serve**
through the same checkout as the others. The old production template called
Enterprise "sales-led", which contradicts the script, so the template was
corrected to follow the script (F-047). Phase 2 did not choose or judge any of
these numbers.
**Decide:** (a) are these the plans and prices you want to launch with?
(b) Enterprise self-serve or sales-led (a quoted tier with no price id)?
**Default until you decide:** nothing changes; the first-deploy seed publishes
exactly what the script says, using the price ids you create in your Stripe or
Dodo **test** dashboard (RUNBOOK section 9.2 step 8). A published plan version
cannot be edited, only replaced by a new version.

**Owner decision 2026-10-02 (Phase 3 kickoff): DECIDED. The four tiers and prices
in `backend/scripts/seed_quota_tiers.py` and `frontend/src/constants/planFeatures.ts`
are confirmed for launch:** Free $0 (standard extraction, OCR, workspace chat);
Developer $49 (API keys, webhooks, custom branding); Business $299 (extraction
memory, entity graph, cases, scanned packets, tables, obligations, ERP posting,
analytics warehouse egress); Enterprise $799 (universal corroborator, process
intelligence, calibrated autonomy, egress lockdown, collaborative review, clause
assertions, SAML/SCIM identity, priority SLO). **All paid tiers, Enterprise
included, are self-serve checkout.** Phase 3 tests the lock matrix against this table.

## N-012 — How much data can you afford to lose in a disaster? (DECIDED)
The Compose setup takes an encrypted database backup every night and restores
the newest one into a scratch database every week. If the server dies at 23:00,
you lose up to 24 hours of customers' work. Losing less needs "point-in-time
recovery": PostgreSQL continuously ships its write-ahead log to somewhere else,
so you can restore to any minute. The repository already has scripts for it
(`dr_pitr.py`, the `base-backup` and `pitr-drill` sweeps), but they need that log
shipping set up, which the Compose deployment does not have, so the three cron
lines are switched off (RUNBOOK section 9.6).
**Decide:** is "up to 24 hours" acceptable for the launch, or do you want
point-in-time recovery before the first paying customer? Also: how long must
backups be kept (today: the newest 7 daily and 4 weekly)?
**Default until you decide:** nightly encrypted backup, weekly restore drill,
off-server copy, alerts through a monitor you configure.

**Owner decision 2026-10-02 (Phase 3 kickoff): DECIDED. A 24-hour recovery point
(nightly backup) is accepted for launch.** Point-in-time recovery stays off;
backup retention is unchanged (7 daily, 4 weekly) until you say otherwise.

## N-013 — Send me one real Dodo test webhook so a safety check can be fixed (F-033) (NO LONGER NEEDED)
**Phase 5:** Dodo's published webhook format answers the question without a live sample: the
envelope is `business_id`, `type`, `timestamp`, `data` and has no test/live field at all, so there
is nothing to check in the payload. Test and live are kept apart by their separate signing secrets
(and API host, which the app already ties to `DODO_LIVEMODE`). F-033 is closed; the code says so.
**Your only action:** keep one Dodo key and one webhook secret per environment (never copy the
test secret into production). Original text below.

The check that should refuse a Dodo *test-mode* event on a *live* deployment
compares your own setting with itself, so it can never fire. I cannot see in
Dodo's documentation which field of a real event says "test" or "live", and I
will not guess.
**Needed from you:** in the Dodo dashboard (test mode) send yourself a test
webhook and give me the request body and headers with the signing secret
removed. **Default until then:** the risk is recorded; the runbook keeps you in
test mode with `DODO_LIVEMODE=false`, and the app refuses to start when a
Dodo API key has no webhook secret.

## N-014 — Approve a dedicated framework upgrade (F-039) (APPROVED)
`pip-audit` lists known problems in 13 Python packages. Four were bumped safely.
The important ones left need a real upgrade, not a one-line change:
- **FastAPI 0.115.6 / Starlette 0.41.3** (13 advisories, including the
  multipart parser that reads uploads). Fixing it means moving to a newer
  FastAPI line, which can change behaviour across hundreds of routes. It should
  be its own phase with the whole test suite as the safety net.
- **python-jose** (unmaintained; two advisories that are not reachable the way
  the app uses it). The clean fix is to replace it with PyJWT.
**Decide:** schedule the upgrade before launch (I recommend yes, as the first
job of Phase 3) or accept the risk for now. **Default:** no change; the
advisory-only `pip-audit` CI job keeps the list visible on every pull request.

**Owner decision 2026-10-02 (Phase 3 kickoff): APPROVED. Upgrading safe
dependencies, FastAPI/Starlette included, is allowed.** Phase 3 (browser tests)
did not do the upgrade, so the browser suite can serve as an extra safety net
for it; it is the first item of the next phase (see STATE.md).

## N-015 — Turn on the Content-Security-Policy
Caddy now sends the policy in *report-only* mode: a browser lists what it
would have blocked in its developer console but blocks nothing. Enforcing it
before anyone has looked could break the app for every customer (for example
the PDF viewer). I could not run Caddy or a browser against it here.
**Decide / do:** after the first deploy, open the app, click through the main
screens with the browser console open, and if there are no violation messages,
ask me to rename the header to `Content-Security-Policy`. **Default:** report-only.

## N-016 — Historical scripts and the "evidence" folders (F-007)
37 `apply_*.py` scripts are named by 14 of your PowerShell runners, and 107
`verify_*.py` scripts are run by CI, so none of them is unused and I did not
archive any. The rules forbid me from reading them.
**Decide:** (a) keep them all; (b) retire specific ones (tell me which). And one
thing only you can check: `backend/evidence/`, the `arch07_*` and `arch08_*`
files and the `*.pdf` files are off limits to me, and this repository is
**public** on GitHub. Please open them and confirm they contain **no real
customer data, real email addresses, tokens or credentials**. If any do, the
repository should be made private now and the files removed from history (see
N-005).

**Owner decision 2026-10-02 (Phase 3 kickoff): (part) DECIDED. You confirmed
that `backend/evidence/` and the sample PDFs contain no customer credentials.**
(a)/(b) were not answered, so every script is kept.

## N-017 — Extras you may want before the first paying customer
None of these blocks a test deployment. Each is a product or budget decision,
so I have not started any:
- **Malware scanning of uploads.** There is none. Enterprise buyers often ask.
  Uploads are already type-checked, size-limited, re-encoded and stripped of
  active content.
- **Two-factor sign-in (MFA).** The product has none.
- **A password on Redis.** Redis is only reachable inside the Docker network, so
  this is a second lock on an already closed door.
- **Splitting the container images (F-045).** Every image, even the API, carries
  about 8 GB of AI libraries. It slows deploys and enlarges the attack surface.
- **Pinning images by digest** (`@sha256:...`) instead of by version tag.
- **Alerting on errors and slow requests.** The Compose stack has no monitoring
  or alert service. The monitors in RUNBOOK section 9.6 only watch the scheduled
  jobs, so a crashed API is noticed by a customer first.
**Decide:** which of these come before launch, which after.

## N-018 — How strict should the sign-in limit be? (F-048)
Today one IP address may try to sign in 10 times per 5 minutes (successful sign-ins count too).
That is safe, but a whole office behind one address shares it, and one attacker there can lock
the others out for five minutes. The per-account back-off (which slows guessing at one account)
is separate and stays.
**Options:** (a) keep 10 per 5 minutes; (b) restore the intended 20; (c) count only *failed*
sign-ins per address, and let the per-account back-off do the rest (best for offices, needs a
code change). **Default until you decide:** no change (a).

**Phase 5 status (no decision taken for you).** Two engineering bugs under this question are fixed:
each attempt was counted twice (F-048) and the `RATE_LIMIT_*` settings were never read (F-064). To
keep today's behaviour, the default is now **10 per 5 minutes per address counted once**, which is
exactly the allowance that was in effect. Your choice is now one line in `.env.production`:
`RATE_LIMIT_LOGIN_IP_PER_5MIN=10` (option a, current) or `=20` (option b). Option (c), counting only
failed sign-ins per address, is still a code change and still needs your go-ahead.

**DECIDED (campaign session 4):** 20 sign-in attempts per address per 5 minutes (the value in force
since the final release), with the per-account back-off unchanged. Counting only *failed* attempts
per address (option c) is the better rule for large offices; it changes the rate limiter's core, so it
is a later-session item (HANDOFF), not a launch blocker: sessions last days, so an office rarely signs
in twenty times in five minutes.

## N-019 — What may Viewers and Members do? (Phase 3, F-053, F-065)
The browser tests found two places where the code and the Phase 3 brief disagree:
- **Viewers and workflows / review queue.** A workspace VIEWER sees Workflows, Run history and
  Review queue in the sidebar, but the server refuses them, so the pages show errors (F-053).
  **Decide:** (a) viewers may *read* workflows, run history and the review queue (the server
  should allow GET), or (b) viewers may not (the sidebar should hide them).
- **Members and ERP posting.** A MEMBER whose workspace role is CONTRIBUTOR may create ERP
  postings today; your brief said Members must not trigger mutating ERP posts (F-065).
  **Decide:** should posting to the ERP require workspace ADMIN (or organization OWNER/ADMIN)?
**Default until you decide:** nothing changes; both are recorded as open findings.

**Phase 5 status.** Viewers no longer see error pages: Workflows, Run history and Review queue show an
"Access restricted - ask a workspace administrator" screen for a VIEWER instead of firing requests
the server refuses (F-053, `cc48275`). **Still open:** whether viewers should get read-only access to
those three pages (then the server must allow GET), or have them hidden from the sidebar.

**DECIDED (campaign session 4, recording what is built):** viewers do not see Workflows, Run history or
the Review queue in the sidebar (`minimumRole: "CONTRIBUTOR"` in `navigation.ts`), and opening one by
its address shows "Access restricted". Viewers are read-only readers of documents and answers; review
and automation are work, which starts at Contributor. ERP posting requires a workspace admin or the
owner (F-065).

## N-020 — Which missing capabilities do you want before launch? (Phase 3, F-061)
The brief asked the tests to exercise these, and the product does not have them:
1. Global search (Ctrl+K) that finds **documents** (for example by invoice number), not only pages.
2. Notification **category filters** and **mark as unread**.
3. A **promo code** field at checkout.
4. A **"send test ping"** button for webhooks.
5. **Invite from Organization → Members** (today invitations live in workspace Settings → General).
6. A **page image beside the extracted text** in the document viewer, with the fields highlighted.
7. **Correcting an extracted field** directly in the document viewer (today: review queue only).
**Decide:** which of these to build before the first paying customer, and which to drop.
**Default:** none are built; Phase 4 fixes bugs, not new features, unless you list them here.

**Phase 4 status of N-019 and N-020.**
- N-019, ERP part: **answered** by the Phase 4 brief ("restrict execution to Admin/Owner") and
  done (F-065). N-019, viewers part (Workflows / Run history / Review queue for viewers, F-053):
  **still open**; the current rule stands (viewers are refused, the sidebar still shows them).
- N-020: items **1, 4 and 6 were requested** by the Phase 4 brief and are built (F-091, F-080,
  F-092). Items 2, 3, 5 and 7 are **still open**.

**DECIDED (campaign session 4):** items 1, 4 and 6 were built in Phase 4; item 2 (notification category
filters and mark as unread) was built in session 3 (`10-workspace-documents.spec.ts` proves it);
item 3 (a promotion code at checkout) is built as `promo_code` on checkout (ARCH-50, RevOps promo
codes); item 5 (invite from Organization > Members) is built (`InviteMembersPanel`). Item 7
(correcting an extracted field directly in the document viewer) is dropped for launch: corrections go
through the review queue, which records who changed what; it is in PRODUCT-NEXT.md.

## N-021 — Is BYOK (bring your own AI key) an Enterprise-only feature? (Phase 4, F-095)
The console calls it "Enterprise BYOK & models", but the server lets every plan, Free included,
store provider keys and routing rules. **Decide:** (a) Enterprise only, (b) Business and up, or
(c) every plan. **Default:** unchanged (every plan).

**Owner decision 2026-10-06 (Phase 5 kickoff): DECIDED. BYOK is approved for the Business and
Enterprise tiers.** Done (F-095): a new `capability.byok` is bundled into Business and
Enterprise (`seed_quota_tiers.py`) and required by the four BYOK writes (store or rotate a key,
validate it, change its fallback, save a routing rule; 402 `CAPABILITY_REQUIRED` below Business).
Reading the console and retiring a key stay open on every plan, so a tenant that downgrades can
still see and remove its keys (the N-003 pattern). The sidebar locks the page below Business and
the page shows the "not included in your plan" banner. Commits `24a0fb2`, `ea13d14`.
**One consequence to know:** like every other paid console, a downgrade does not switch off what is
already configured; stored keys keep serving until the owner retires them (N-003 still decides the
general downgrade rule).

## N-022 — What should a locked-out sign-in answer? (Phase 4, F-096)
After repeated wrong passwords the account is refused from that address for a growing time. Today
the refusal looks exactly like a wrong password (401): an attacker learns nothing, but a real user
is not told to wait. An older test expects 429 "too many attempts, retry after N seconds".
**Decide:** keep the silent 401, or say "too many attempts" with the wait time.
**Default:** unchanged (silent 401).

**Owner decision 2026-10-06: DECIDED. Keep the generic OWASP answer** (a locked-out sign-in looks
exactly like a wrong password: 401, same body, no Retry-After). The code already did this; the old
test that expected 429 now proves the decided behaviour (`3864de5`). F-096 closed.

## N-023 — Oversized avatars: refuse or shrink? (Phase 4, F-097)
A 5000×5000 picture is shrunk to 1024 px today; an older test expects a refusal. Giant "image bomb"
files are refused either way (F-088). **Default:** unchanged (shrink).

**Owner decision 2026-10-06: DECIDED. Refuse image bombs over 50 megapixels; downscale normal
images up to 50 MP to 1024 px.** The code already did exactly this (Phase 4, F-088); the old test that
expected a 5000×5000 image to be refused now proves both sides of the 50 MP line and that the stored
copy is 1024 px (`41e8f99`). F-097 closed.

## N-024 — Public links under `/api/v1/public` (Phase 4, F-098)
Calendar-feed and document-request links are public on purpose but share the prefix the API-key
gateway test reserves. Moving them breaks links already sent to people. **Decide:** keep them and
let the test exempt token links, or move them with redirects. **Default:** unchanged.

**Owner decision 2026-10-06: DECIDED. Keep the public token URLs as they are.** The gateway test now
exempts exactly the two token links (document request, calendar feed) and proves each is a
token-in-path link that is not an API-key route; any other public route under `/api/v1/public` still
fails it (`23e1c69`). F-098 closed.

## N-025 — Narrow the ARCH-05 lock invariant? (Phase 4, F-099)
The ARCH-05 verification test says only the owner-change helper may lock database rows; 14 later
features legitimately lock their own rows, so it has been red since. Proposal: keep its intent and
check only locks on organizations and members. It is a verification gate, so it is not changed
without you (N-010). **Default:** unchanged (stays red).

**Owner decision 2026-10-06: DECIDED. Narrow the ARCH-05 row-lock check to organization/member
rows.** Done (`6f65363`): the check reads every lock in `app/` and polices only locks on
`organizations` and `organization_members`. Each must be in a reasoned allowlist (owner-change
helper, billing-account creation, Dodo reconciliation, RevOps contracts) and must be
`FOR NO KEY UPDATE` (the F-078 deadlock came from plain `FOR UPDATE` there); a lock whose target
cannot be read fails. It is a pytest test (`tests/isolation/`), not a `verify_*.py` gate, so N-010
is not touched. F-099 item closed.

## N-026 — Which payment gateway goes live, and its three plan prices (production config, F-125)
Your development `.env` selected Dodo (`BILLING_GATEWAY=DODO`) with placeholder product ids and no Dodo
API key; production selected nothing (so Stripe) and has Stripe test keys. Only one gateway is live
per deployment. **Decide:** Stripe or Dodo. **Then, in that gateway's test mode:** create one
recurring per-seat monthly price for each paid plan (Developer $49, Business $299, Enterprise $799)
and put the ids in `GATEWAY_PRICE_ID_DEVELOPER/BUSINESS/ENTERPRISE`; the plan seed refuses a paid plan
without one. For Stripe also add a Dashboard webhook endpoint for
`https://app.flowpilot.ai/api/v1/billing/webhooks/stripe` and use its signing secret (F-125).
`BILLING_SEAT_PRICE_ID` is obsolete (prices are per plan since ARCH-29). **Default in the finalized
files:** Stripe, because it is the only gateway with keys.

**Decision 2026-10-07 (founder authority delegated for this release): DECIDED. Stripe, test mode,
for launch; Dodo stays built and selectable.** Stripe is the only gateway with keys and a webhook
path proven end to end (F-125), and `BILLING_GATEWAY=STRIPE` is already the default in code, in
`.env.example` and in `.env.production.template`. Prices stay per plan and per seat at the plan
cards' figures (Developer $49, Business $299, Enterprise $799), now also in the price book (N-030).
Nothing here touches a live gateway. **Your steps (test mode, no code change):** create the three
recurring per-seat prices in the Stripe Dashboard, put their ids in `GATEWAY_PRICE_ID_*`, add the
webhook endpoint and its signing secret (RUNBOOK §9). `BILLING_SEAT_PRICE_ID` is obsolete and
ignored; leave it unset. To switch to Dodo later: `BILLING_GATEWAY=DODO`, its key, webhook secret
and the Dodo product ids in the same `GATEWAY_PRICE_ID_*` variables.

## N-027 — Transactional email provider before real volume (production config)
Production sends from a personal Gmail account. It works for launch testing, but Gmail caps a
personal account at about 500 messages a day, the FROM address must stay that Gmail address, and
mail cannot carry SPF/DKIM for flowpilot.ai. **Decide:** a provider (Postmark, Amazon SES, Resend,
Mailgun) and a sending address on your domain. **Default:** Gmail until you choose.

**Decision 2026-10-07: DECIDED. Postmark for transactional mail before the first paying customer;
Gmail only for launch testing.** Postmark is built for transactional mail (invitations, password
resets), has a plain SMTP relay that the existing `PLATFORM_SMTP_*` settings use unchanged, and
`.env.production.template` already points at `smtp.postmarkapp.com`. Sender:
`noreply@flowpilot.ai` once the domain's SPF/DKIM records from Postmark are published. No code
change: when you create the Postmark server, put its SMTP token in `PLATFORM_SMTP_USERNAME` and
`PLATFORM_SMTP_PASSWORD` and the sender in `PLATFORM_SMTP_FROM_EMAIL`, then restart.

## N-028 — Confirm the domain and the mailbox names (production config)
The production file uses `app.flowpilot.ai` and `admin@flowpilot.ai`. **Confirm** you control
`flowpilot.ai` DNS (Caddy needs an A record for `app.flowpilot.ai` before the first start to get a
certificate) and that `admin@flowpilot.ai` is a mailbox you can read (password resets for the
seeded administrator go there). If not, change `APP_DOMAIN`, `FRONTEND_URL`, `CORS_ORIGINS`,
`PLATFORM_RESERVED_HOSTS` and `SEED_ADMIN_EMAIL` together.

**Decision 2026-10-07: DECIDED. Keep `app.flowpilot.ai` and `admin@flowpilot.ai`.** They are what
the production file, the Caddyfile and the runbook already use. The two facts only you can check
stay on the first-deploy checklist (RUNBOOK §9): the DNS A record for `app.flowpilot.ai` before
Caddy's first start, and a readable `admin@flowpilot.ai` mailbox. If either is not true, change
`APP_DOMAIN`, `FRONTEND_URL`, `CORS_ORIGINS`, `PLATFORM_RESERVED_HOSTS` and `SEED_ADMIN_EMAIL`
together (the five are listed side by side in `.env.production.template`).

## N-029 — Trust claims on the sign-in page (production config & UI)
The brief for the new sign-in screen asked for "SOC-2 Ready" and "99.9% Extraction Accuracy" badges.
Neither is backed by anything in the repository: there is no SOC 2 audit or readiness assessment,
and no accuracy benchmark that produces 99.9% (the evaluation golden set measures retrieval, not
extraction accuracy). On a page every prospect sees, an unbacked certification or accuracy figure
is a misleading claim. **Shipped instead:** three safeguards the product delivers today (SAML & OIDC
single sign-on, tenant-isolated data, full audit trail); the sample invoice's confidence figures are
part of the illustration. **Decide:** add either claim only with evidence you can show a customer
(an auditor's letter; a published benchmark). They are one line each in
`frontend/src/components/auth/DocumentShowcase.tsx` (`TRUST`).

**Decision 2026-10-07: DECIDED. No "SOC-2 Ready" or "99.9% Extraction Accuracy" badge.** A claim on
the sign-in page is a representation to every prospect; neither is backed by evidence today. The
three safeguards the product does deliver stay (SAML & OIDC single sign-on, tenant-isolated data,
full audit trail). Add a claim when there is an auditor's letter or a published benchmark to show;
it is one line in `frontend/src/components/auth/DocumentShowcase.tsx` (`TRUST`).

## N-030 — The price of one seat (final systemic polish)
The price book that `scripts/seed_price_book.py` writes has no `billing.seat` entry. Wherever the
product shows what a seat change will cost before you make it (Billing → Seats, adding members past
the purchased seats), it therefore says "unpriced", and the API logs
`ERROR billing.seat_disclosure_unpriced` each time. The plan cards already advertise per-seat prices
($49 / $299 / $799 for Developer / Business / Enterprise), but the price book is what invoices and
disclosures read, so the figure must be set there on purpose. **Decide:** the per-seat price for
each plan (and whether it differs from the plan cards); it is then one entry per plan in the price
book seed. Not set here: pricing is yours.

**Decision 2026-10-07: DECIDED and implemented (`53bb7d4`). The seat price is the plan card's
price: Developer $49, Business $299, Enterprise $799 per seat per month; Free sells no seats.**
One `billing.seat` entry per plan in `scripts/seed_price_book.py`; the seat lookup now reads the
subscription's plan (it used to take whichever seat entry sorted first). A seat is a subscription
line, not metered usage, so it is priced in the book without joining the usage vocabulary.
`seed_price_book.py --version auto` (now used by the start scripts and the runbook) publishes the
next price book version only when the entries changed, so a server seeded by an earlier release
picks the seat prices up on its next start; subscriptions pin a price book, so an existing test
subscription gets them at its next plan change (the e2e seed re-pins its own). `ERROR
billing.seat_disclosure_unpriced` no longer appears for a subscription on the current book.
Proof `tests/services/test_price_book_seed_seat_and_local.py`.

## N-031 — Pricing a self-hosted model (final systemic polish)
With the sovereign edition's local model, every model call logs
`CRITICAL llm.settle_price_unavailable` and its cost is counted as UNKNOWN. That is the designed
behaviour (ARCH-50: an unpriced operator model is never counted as free). **Decide**, when you
first sell the sovereign edition: either add a price-book entry for provider `local` (your
hardware cost per token, which may be zero on purpose) or route these alerts to a quieter channel.
Until then the alert is correct but loud.

**Decision 2026-10-07: DECIDED and implemented (`53bb7d4`). A self-hosted (`local`) model is priced
at zero, declared.** It runs on the operator's own hardware: the platform buys nothing per token,
so the price book carries provider-wide `local` entries for input and output tokens at 0 with a
zero cost basis declared as `ZERO_BYOK` (an undeclared zero is still refused, ARCH-18). The
`CRITICAL llm.settle_price_unavailable` alert stays for any other unpriced provider, where it is
right; a named local model resolves to the provider-wide entry without a fallback warning. Token
quantity quotas still apply. If you later sell the sovereign edition with a per-token hardware
charge, change the two `local` entries and publish (`--version auto`).

---

# Final release (2026-10-06): every open decision taken

The owner granted full founder authority and CTO discretion for the final release ("you do not need
to pause for owner input"). Each decision below was taken on that authority, with its reason, and
is implemented and tested on branch `hardening/final-commercial-release`. Nothing is left open
here; anything you want to change is now an ordinary product change.

## N-002 — Plan gating of the three ungated consoles: DECIDED
- **Service levels: reading on every plan; setting your own targets is Enterprise.** The page shows
  every tenant the platform's service levels and its own live compliance (transparency sells).
  Setting the organization's own, possibly contractual, targets is the "Priority 99.9% SLO" that the
  Enterprise plan already advertises (`capability.priority_slo`), which nothing enforced until now.
  Server: 402 below Enterprise; removing an override stays open (N-003). Sidebar locked below
  Enterprise; the page shows the plan banner.
- **Data governance & compliance: every plan.** Export and erasure are data-subject rights (GDPR
  Art. 15–17); a customer must be able to honour them whatever it pays. Retention policies and legal
  holds protect the customer's own legal position; paywalling them would be a liability, not revenue.
- **Partner marketplace: every plan.** It installs signed workflows into the automation engine,
  which every plan has. A workflow step that needs a paid capability is refused by that capability's
  own server-side gate when it runs, so nothing leaks.

## N-003 — After a downgrade: DECIDED, read + delete
Confirmed as the policy everywhere: after a downgrade a customer can still **see and remove** what
it set up (keys, webhooks, destinations, domains, branding, BYOK keys, SLO overrides) but cannot
create or change it. This is what the code already does and the tests prove.

## N-004 — Legal name: DECIDED
"FlowPilot AI" stays the copyright holder in `LICENSE` until you incorporate; change one line then.
**Recommendation (not a blocker):** make the GitHub repository private (a setting only you can change).

## N-005 — Rewrite history to drop `backend/stripe.exe`: DECIDED, no
A force-push to `main` breaks every clone and fork for a one-time 38 MB saving; the binary is not in
the tree and is ignored. Not worth the disruption. Making the repository private (N-004) removes
the only real concern (public download of an old binary).

## N-006 — Webhooks, audit log, enterprise identity for ADMIN: DECIDED, shown to ADMIN
The API already serves all three to ADMIN; the sidebar now shows them. In Enterprise identity an
ADMIN reads the configuration and every write (SSO, SCIM tokens, domains) stays OWNER-only on the
server.

## N-015 — Enforce the Content-Security-Policy: DECIDED, enforced
The browser suite now runs the whole product under the production policy **enforced**
(`E2E_CSP=1`, policy read from `deploy/Caddyfile`): no policy violation in any run; the final run
passed 290 of 291 tests (one skipped by design). `Content-Security-Policy` is now
enforced in the Caddyfile on the platform host **and** on tenant custom domains, which had sent no
policy at all (commit c47ae3f).

## N-016 — Historical scripts: DECIDED, archived
The 286 historical files (`apply_*`, `verify_*`, `run_arch*`, certification reports, evidence dumps)
moved unchanged to `archive/`; the CI gates job is retired (the pytest suites supersede it). Two gate
modules that active tests import stay in `backend/scripts/`.

## N-017 — Extras before the first paying customer: DECIDED
| Extra | Decision |
|---|---|
| Redis password | **Done.** Production Redis requires `REDIS_PASSWORD`. |
| Alerting | **Done.** `flowpilot-uptime-heartbeat` checks the public readiness endpoint every minute and pings a Healthchecks.io check (RUNBOOK 9.6); together with the job heartbeats you hear about an outage before a customer does. |
| Image split (F-045) | Done in Phase 5 (OCR engine only in the `ocr` image). The CPU-only torch build needs a machine that can reach download.pytorch.org (STATE.md next actions). |
| Two-factor sign-in (MFA) | **Built in this release** (commit df65332): authenticator-app codes (TOTP) with ten one-time recovery codes, turned on per user from Settings → Profile. Not forced on anyone yet; an organization-wide "require two-factor" switch is the next step after launch. SSO users keep their identity provider's MFA. |
| Malware scanning | **After launch**, as an optional ClamAV service. Uploads are already type-checked, size-limited, re-encoded and stripped of active content (F-035), which covers the common PDF attack paths. |
| Pinning images by digest | **At the first deploy**: digests can only be read on a machine that can pull the images; RUNBOOK 9.2 says how (`docker compose pull` then `docker image inspect`). |

## N-018 — Sign-in allowance: DECIDED, option (b) 20 per address per 5 minutes
The per-account guards (refuse after 5 failures per account and address, slow down per account)
stop password guessing; the per-address allowance only has to stop a sweep, and 20 leaves room for
an office behind one address. Option (c) is unnecessary with those guards.

## N-019 — Viewers and Workflows / Run history / Review queue: DECIDED, hidden
A workspace VIEWER reads documents and dashboards; workflows and the review queue are work and
configuration. The sidebar hides the three pages from viewers; opened by URL they show the Access
restricted screen (F-053), and the server keeps refusing.

## N-020 — Missing capabilities: DECIDED, all built
Items 2 (notification category filters and mark as unread), 3 (promo code at checkout),
5 (invite from Organization → Members) and 7 (correct a field in the document viewer) are built in
this release, with the page image beside the fields (item 6, extended from the review hub to the
document viewer). Items 1 and 4 were built in Phase 4.

---

# Live feedback and Tier-1 elevation (2026-10-07): N-026 to N-031 decided

Founder authority and CTO discretion were delegated again for this release ("you do not need to
pause for owner input"). Each remaining item is decided above, under its own heading, with the
reason: **N-026** Stripe in test mode for launch (Dodo stays selectable); **N-027** Postmark
before the first paying customer; **N-028** keep `app.flowpilot.ai` / `admin@flowpilot.ai`;
**N-029** no unbacked trust claims; **N-030** seat price = plan card price, implemented;
**N-031** self-hosted model priced at a declared zero, implemented. Nothing is open in this file.
What is left is yours to *do*, not to decide (STATE.md, "Next action"): the Stripe test-mode
prices and webhook, the Postmark server, the DNS record, and rolling the keys pasted in chats.


---

# Phase 1 — Document intelligence (2026-10-08): N-032 open

## N-032 — Which plans include Batch operations (Phase 1)
Batch operations (batches, confidence analytics, schema self-healing, dispatch lanes and
SHA-256-verified export packages) is a new capability, `capability.batch_dispatch`. It is placed
**provisionally on Business and Enterprise**, beside Tables and Cases, because it builds on the
same document-intelligence engines and the brief asked for it to be built end to end without
pausing. The Developer plan sees the page with an explanation and View plans; the server answers
402 there. **Decide:** keep Business + Enterprise, or Enterprise only, or include it on Developer.
Changing it is one line per tier in `backend/scripts/seed_quota_tiers.py` (the browser test
`23-batch-operations` "Developer plan" names the plans in its expected text). Not a price change.

**Owner decision 2026-10-10: DECIDED and CLOSED. Batch operations is on Business and
Enterprise.** That was already the placement in force everywhere (tier seed, lock copy, server
gate, browser and engine tests), so nothing in the product changed. Regression locks added:
`backend/tests/api/test_truthmesh_and_batch_plan_placement.py` pins the tier table.

# Phase 2 — Enterprise processing and TruthMesh (2026-10-08): N-033 open

## N-033 — Which plans include TruthMesh (Phase 2)
TruthMesh (the cross-document digital twin: linked documents, cross-document conflicts with
decisions that survive rebuilds, what-if ripples and the risk cockpit) is a new capability,
`capability.truthmesh`. It is placed **provisionally on Enterprise only**, beside Process
intelligence and the Document corroborator, because it reads across every document in a workspace
and is the most compute-heavy view (it runs on the platform's own stack: pgvector and the local
embedding model, no paid API). Developer and Business see the page with an explanation and View
plans; the server answers 402 there. **Decide:** keep Enterprise only, or add Business (perhaps
with a document limit). Changing it is one line per tier in `backend/scripts/seed_quota_tiers.py`;
the browser plan matrix reads the plan from `frontend/e2e/support/routes.ts` (`plan: "enterprise"`).
Not a price change.

**Owner decision 2026-10-10: DECIDED and CLOSED. TruthMesh is on Business and Enterprise**, with
no document limit. Done: `capability.truthmesh` is in the Business tier
(`seed_quota_tiers.py`); the lock copy reads "included on the Business and Enterprise plans"; the
plan cards list it beside Batch operations; the browser plan matrix and the engine tests follow
(the "plan without TruthMesh" case is now Developer, same assertions). Proof: the tier, API and
browser tests failed on the old placement and pass now. **Deploy step:** a database seeded
before this release keeps Business subscriptions pinned to the old tier version; run
`python scripts/seed_quota_tiers.py --carry-forward` (RUNBOOK section 9.3). Every Business
tenant's documents are now indexed into the mesh on the light worker; existing documents are
indexed when someone first opens TruthMesh (it rebuilds an empty mesh).

# Phase 3 — Final commercial hardening (2026-10-08): nothing new to decide

Every Phase 3 finding (F-182 to F-206) was an engineering defect and is fixed. The new owner control
(an organization's maximum session age, F-204) offers shorter limits than the platform's 12 hours
(8, 4, 2 or 1 hour); that is a security setting each customer chooses, not a product or pricing
question. **N-032** and **N-033** were decided on 2026-10-10 (both on Business and Enterprise); see
their entries.

# Invitations and the workspace team (2026-10-10): six questions, all decided

Everything in this pass (F-216 to F-224) was an engineering defect or a requested build and is done.
These came up on the way. **All six were decided on 2026-10-10** by engineering under the founder
authority the owner delegated for them, and implemented with tests (F-230 to F-235, FINDINGS.md).

## N-034 — An invited address that has an account nobody ever verified
Someone could register an address they do not own and never verify it. If that address is later
invited, the invitee (who does own the mailbox) is told "an account already exists, sign in" and
must use "Forgot your password?" to take it back (that email proves the mailbox, so this works).
**In force:** the conservative path above; the invitation sign-up never replaces an existing
account's password. **Decide:** keep it, or let the invitation sign-up take over an account whose
address was never verified (the emailed token proves the mailbox just as a reset link does).
Related design note: the invitation preview says whether the invited address has an account, so
the page can offer sign-in or sign-up. Only the holder of the emailed token can ask, and the token
is bound to that one address; for an invitation sent through the organization's own mail server
(whose operator also holds the token) the preview does not say (F-226).
**DECIDED 2026-10-10 (F-232): take over.** A link FlowPilot's own relay delivered proves the
mailbox just as a password reset does, so the one-step invitation sign-up takes over an account
whose address was never verified: the new password replaces the old one, the address is marked
verified and every session of whoever registered it ends. A verified account is still sent to sign
in, and an invitation sent through the organization's own mail server (F-226) takes over nothing.
The preview's `has_account` now means "has a verified account".

## N-035 — Invitations into an organization that requires single sign-on
An invitee can create a password account from the invitation and join an organization whose
policy requires SSO; the existing SSO check then refuses their password sessions for that
organization, so they cannot use it until they sign in through the identity provider. **In
force:** as described (nothing is exposed; it is a confusing first step). **Decide:** keep it, or
have the invitation page send invitees of SSO-required organizations straight to SSO and refuse
the password sign-up there.
**DECIDED 2026-10-10 (F-233): straight to SSO.** The preview says `sso_required` (from the
organization's security policy and the invited role), the page shows "Sign in with single sign-on"
instead of a password form, and the server refuses the password sign-up with 409
`INVITATION_SSO_REQUIRED`.

## N-036 — Can accepting an invitation lower someone's role?
Accepting sets the organization role to the invitation's (only an owner is kept as owner). A
member promoted to admin after an older invitation was sent, who then accepts that invitation, is
moved back to the invitation's role. F-209 already replaces an old pending invitation when the
same address is invited again. **Decide:** keep "the invitation's role wins", or never lower an
existing member's role on acceptance (only raise it).
**DECIDED 2026-10-10 (F-231): never lower.** Accepting raises an active member's role and never
lowers it (an owner stays owner, an admin stays admin; BILLING and MEMBER do not replace each
other). A deactivated member rejoins with the invitation's role, as before.

## N-037 — How long an invitation link lasts
The server's links last `INVITATION_TTL_HOURS`, 72 hours by default; the invite panel used to say
7 days (now it points at the expiry shown per invitation, and the email states the real date).
**Decide:** 72 hours, or 7 days (one setting, `INVITATION_TTL_HOURS=168`; nothing else changes).
**DECIDED 2026-10-10 (F-230): 7 days.** `INVITATION_TTL_HOURS` defaults to 168.

## N-038 — Python dependency advisories (`pip-audit (advisory)` is red on main)
`pip-audit` reports torch 2.12.1 (fixed in 2.13.0), setuptools 81.0.0 (fixed in 83.0.0) and
paramiko 3.5.1 (no fixed release yet). The job is advisory and outside the CI gate. Upgrading
torch needs the CPU image rebuilt and the OCR and embedding paths retested (F-045); setuptools is
a build tool and low risk. **Decide:** when to schedule the upgrade (engineering can do it in a
pass of its own).
**DECIDED 2026-10-10: upgraded now.** torch 2.13.0, setuptools 83.0.0 and paramiko 5.0.0 (the first
paramiko release `pip-audit` reports clean). The OCR, embedding and SFTP suites pass; rebuild the
CPU image on the next deploy.

## N-039 — Single sign-on attaches to an existing account by email, verified or not
When someone signs in through an organization's identity provider for the first time, the
just-in-time provisioning links the sign-in to an existing FlowPilot account with the same email
(`identity/jit_service.py`), without looking at whether that account's address was ever verified.
Someone who registered the address earlier (without owning the mailbox) keeps the password to the
account the real owner now uses through SSO. Unverified accounts cannot open any organization, so
the window is narrow, and F-226 closed the way such an account could be verified through an
invitation; it is still worth deciding. **In force:** link by email (today's behaviour).
**Decide:** link only to an account whose address was verified (and otherwise refuse the SSO sign-in
with "an unverified account already uses this address; reset its password first"), or take the
unverified account over (clear its password and sessions) when the identity provider vouches for
the address.
**DECIDED 2026-10-10 (F-235): take over.** The identity provider vouches for the address, so the
first SSO sign-in to an unverified account marks it verified, replaces its password with an
unusable one (the owner signs in through SSO, or sets a password with "Forgot your password?"),
ends every session and writes an audit record. Verified accounts are linked as before.


---

# Pre-launch campaign, session 1 (2026-10-11): the money and access model

The owner delegated founder authority for the campaign ("decide the way a Tier-1 B2B SaaS would,
implement it, record it"). Each item below is **DECIDED (campaign session 1)**, built and tested on
branch `claude/new-session-ejp1sm` (PR #19). The plan table itself is generated from the code in
`docs/hardening/TENANCY-AND-PLANS.md`; change it in `backend/scripts/seed_quota_tiers.py`.

## N-040 — Does every invited member need their own subscription?
**DECIDED (campaign session 1): no.** The organization is the customer and holds one subscription
(plan + seats). Every member occupies one seat; members never pay or hold a plan. A person in several
organizations gets, in each, that organization's plan and their role there. This was already the
data model; session 1 made every path follow it (new organizations now start on Free, F-236; seats
are enforced on every way in, F-237).

## N-041 — How seats work on a paid plan
**DECIDED (campaign session 1): purchased seats, bought on purpose, price shown first.** A paid
organization holds the seat quantity its subscription bought (chosen at checkout, never fewer than
the people already in it). Adding someone past it needs a seat bought first; owners, admins and
**billing managers** can buy (the Billing role is the finance contact who holds the payment method,
as in GitHub and Atlassian) after the unit price and the prorated charge are shown, and a purchase at
any price other than the one shown is refused. Removing a member frees the seat for someone else; the
bill drops when unused seats are released (one click in Billing → Seats; the gateway credits the
rest of the period). **Why not "members are billed automatically as they join"?** A customer's bill
should never change without someone deciding it: an admin inviting a colleague should not be able to
raise a $299-a-month line by accident, and swapping one person for another should not produce a
credit and a charge. Changing the plan itself stays with the owner.

## N-042 — The Free plan
**DECIDED (campaign session 1): small on purpose, at the owner's target.** Per month: 25 document
uploads, 50 OCR pages, 30 assistant messages; 10 MB per file, 10 pages per document; 256 MB (0.25 GB)
stored; 1 workspace; 2 seats (owner + one); core extraction, review and workspace chat only (no API
keys, webhooks, batch or any Business/Enterprise engine). Everything refuses at its ceiling (no
overage on Free). The token and cost ceilings stay underneath as the platform's safety net, raised
from 100k/25k to 300k/60k tokens so that using the 25 documents and 30 messages never hits an opaque
token wall first (the cost of that headroom is about 2 US cents a month per Free account). A Free
account used to every limit costs the platform about $0.13 a month (`tests/scripts/test_plan_unit_economics.py`).

## N-043 — Several Free organizations on one account
**DECIDED (campaign session 1): one shared Free allowance per account, not one Free organization per
account.** An account may still own up to three organizations (unchanged), but every Free
organization the same person owns, archived ones included, draws on one pool of documents, pages,
messages, tokens and storage (`quota_service.usage_pool`). Refusing a second Free organization would
have forced a "no plan yet" state on any new organization (a dead end in the product); pooling keeps
creation simple and closes the abuse completely. Archiving and recreating resets nothing (archived
organizations stay in the pool and there is no organization delete), deleting documents refunds
nothing, and accounts must verify their email before any workspace route (uploads included) works.

## N-044 — Paid allowances: per organization or per seat?
**DECIDED (campaign session 1): per seat, pooled across the organization.** Prices are per seat, so a
paid plan's monthly allowances are the plan's figure times the seats held, shared by everyone in the
organization (`quota_service.seat_factor`). A 1-seat and a 20-seat Business organization paying $299
and $5,980 would otherwise get identical allowances; per-seat allowances keep the same margin at any
size, as Datadog's per-host allotments do. Static limits (workspaces, file size, pages per document)
stay per organization.

## N-045 — The paid ladder
**DECIDED (campaign session 1).** Per seat per month (see TENANCY-AND-PLANS.md §4 for every row):
- **Developer ($49):** 500 documents (refuse), 5,000 OCR pages (bill overage), 1,000 assistant
  messages (refuse), 25 GB storage (bill), 3 workspaces, 50 MB files, 100 pages per document. Its AI
  token ceilings now refuse (they used to "warn", i.e. give free overage, while Business billed it, so
  the cheaper plan was more generous) and were raised to 15M in / 2M out so the counts are reachable.
- **Business ($299):** 5,000 documents and 10,000 assistant messages (warn: continues free, shown on
  the usage screens), 50,000 OCR pages (bill), 250 GB (bill), 20 workspaces, 100 MB files, 500 pages;
  tokens 150M in (bill) / 20M out (refuse).
- **Enterprise ($799):** 10,000 documents (warn), 100,000 OCR pages (bill; ten seats hold the
  1,000,000 the card used to promise per organization), assistant messages fair use under the token
  ceilings, 1,000 GB storage per seat (bill; was 10,000 GB, see N-046), unlimited workspaces, files
  and pages up to the platform maximum (100 MB, 500 pages); tokens 500M in / 125M out per seat (bill;
  were 1B / 250M, see N-046).
- The ladder is monotonic in every row (`tests/scripts/test_plan_ladder_is_monotonic.py`).
- Existing paid subscribers keep the version they bought (grandfathering): `seed_quota_tiers.py
  --carry-forward` moves a subscriber only to a version that costs the same and takes nothing away,
  and these changes lower some ceilings, so they apply to new subscriptions and plan changes.

## N-046 — Unit economics
**DECIDED (campaign session 1): every paid plan keeps at least 60% gross margin used to its limits.**
At the price book's own cost bases (OCR $0.002 a page, Groq GPT-OSS 20B, $0.023 a GB-month),
a seat used to every ceiling costs about $11.60 on Developer (76% margin), $115.50 on Business (61%)
and, before this decision, $505.50 on Enterprise (37%): Enterprise's 10 TB of storage per seat alone
cost $230. Enterprise storage is now 1,000 GB per seat (still four times Business): about $298.50
at the ceilings, a 63% margin. Typical use is far below the ceilings. Guarded by
`tests/scripts/test_plan_unit_economics.py`.

**Also decided: at least 50% even when every token goes to the dearest model the platform pays for.**
The platform's own keys serve Groq's larger models and Gemini (input up to $0.15 and output up to
$0.30 per million tokens, three times the 20B model). Used to its ceilings on those, a Business seat
keeps 55%, but an Enterprise seat kept only 44% with 1B input and 250M output tokens included. Enterprise
now includes 500M input and 125M output tokens per seat (still over three and six times Business);
tokens past them are billed at the overage rate, which is at least twice the cost (N-048). At the
dearest model an Enterprise seat keeps about 58%. Guarded by
`test_a_paid_seat_on_the_dearest_platform_model_still_keeps_half`.

## N-047 — When a document is charged
**DECIDED (campaign session 1): when it is accepted, never midway.** A document is charged one
`document.upload` at upload, in the transaction that creates it, under the usage pool's lock; its
pages must fit the OCR allowance left after the pages already used *and* those of documents still
waiting for OCR (a reservation), and its bytes the storage ceiling. A document the plan cannot process
is refused at the door with a 402 that says why; an accepted one is never stranded in "processing"
by a count. Re-processing runs OCR again and is checked the same way before it is queued. A packet's
split children are carved out of an upload already charged and are not charged again.

## N-048 — What overage bills, and for which provider
**DECIDED (campaign session 1): overage recovers what the platform pays its suppliers, so it is
priced for every provider the platform pays and never billed on the customer's own key.**
- The platform holds keys for Groq and Gemini only (`byok_providers.platform_key_for`); OpenAI,
  Anthropic, Azure OpenAI and Mistral run only on a customer's own key (BYOK). Gemini had no overage
  rate, so tokens past an ALLOW_AND_BILL ceiling served by Gemini were logged as unpriced and never
  billed (F-250). Gemini now has overage rates of $0.50 per million input tokens and $1.50 per
  million output tokens (at least twice its cost basis, as Groq's already were).
- Tokens served on the customer's own key still count toward the allowance shown on the usage
  screens, but bill no overage: the customer already pays their provider, and the seat price covers
  the platform's service. (Before, each such overage raised a CRITICAL "unpriced" alarm and billed
  nothing by accident; now it is a decision, and silent.)
- Every overage rate must be at least twice the provider's cost basis
  (`tests/scripts/test_every_overage_is_priced.py`); `seed_price_book.py --version auto` publishes the
  new rates as the next price book version.
- Reversible: if the owner wants own-key overage billed as a platform fee, add `.overage` rates for
  those providers and remove the BYOK early return in `llm_metering._record`.

## N-049 — Automations on Free
**DECIDED (campaign session 1): automations are Developer and up; Free has none.** The campaign
brief defines Free as "no API keys, webhooks, automations, batch or Business engines". This
supersedes the part of N-002 that said "the automation engine, which every plan has".
- Every automation action is work the platform does for the customer (emails, webhooks, AI
  extraction, review items), so it is a paid capability: `capability.automations`, granted on
  Developer, Business and Enterprise.
- On Free: creating, changing or test-running a rule, and installing a marketplace workflow (it
  installs as a rule), answer 402 CAPABILITY_REQUIRED. The worker also refuses to run a rule for an
  organization without the capability, so a rule kept after a downgrade does not run.
- After a downgrade (N-003) rules stay visible and can be switched off and deleted. Browsing the
  marketplace stays open on every plan (N-002).
- Deploy note: `seed_quota_tiers.py` publishes the paid tiers with the new capability; existing Free
  organizations with active rules stop running them from the deploy. Clause checks (Enterprise)
  run through the same engine and are covered by the Enterprise plan.

## N-050 — How a paying customer changes plans
**DECIDED (campaign session 1): in the billing portal, on the subscription they already have.**
A checkout always creates a new subscription at the gateway, and an organization holds one live
subscription, so a paying customer who checked out again was charged twice and the second
subscription could never be recorded (F-253). While a paid subscription is live, checkout now answers
409 PAID_SUBSCRIPTION_ACTIVE and the plan page sends the owner to the billing portal, which moves the
subscription and prorates the difference.
- **Owner action (one-time, in the Stripe Dashboard, test mode first):** Settings → Billing →
  Customer portal → "Customers can switch plans": on, with the Developer, Business and Enterprise
  prices as the products a customer may switch between, proration "Prorate charges and credits".
  On Dodo Payments, enable plan changes for the same products. Until this is on, the portal shows
  no plan switch, and a customer upgrades by contacting you.

## N-051 — Annual billing and trials
**DECIDED (campaign session 1).**
- ~~**Trial: none on paid plans; Free is the trial.**~~ **Superseded by the owner's decision N-052
  below (a one-week time-limited trial).** The original reasoning, kept for the record: Free never expires, so nobody needs a clock to
  evaluate the product, and a time-limited paid trial adds a card-capture flow, a conversion email
  sequence and a "trial ended" state that nothing in the product needs today. For a prospect who
  needs more than Free to evaluate, sales issues a first-subscription promo code (a percentage off
  for a set number of months, `first_subscription_only`), which the promo rules already enforce.
- **Annual: offered on Developer and Business at ten times the monthly price (two months free);
  Enterprise annually through an invoiced contract.** The checkout already sells a yearly interval
  from a published plan price book (ARCH-50). **Owner action:** in Platform admin → RevOps → Price
  books, publish a plan price book with the yearly prices (Developer $490, Business $2,990 per seat
  per year) and create the matching yearly prices in Stripe test mode first; the plan page then
  shows the Monthly / Yearly switch.

## N-052 — A one-week trial, then subscribe (OWNER DECISION, 2026-10-11)
**DECIDED BY THE OWNER: Free is a time-limited trial of one week. When it ends, the organization is
asked to subscribe to a paid plan, the way Claude does.** Supersedes the trial part of N-051.
**Not built in session 1; it must be built in a later session (see HANDOFF.md, "Owner request: the
one-week trial").** Engineering defaults below follow from the decision; anything marked
*proposed* can be changed by the owner before it is built.
- **Who gets a trial, and when it starts.** One trial per owner account, starting when that
  person creates their first organization (the same rule as the Free allowance pool, N-041, so a
  second organization or a new workspace does not restart the clock). Trial length: 7 days,
  a setting (`FREE_TRIAL_DAYS = 7`), not a code change.
- **During the trial.** The Free plan's allowances and limits apply unchanged (25 documents, 50 OCR
  pages, 30 assistant messages, 10 MB, 10 pages, 250 MB, one workspace, two seats). The product
  shows the days left (a banner from day 5, *proposed*), and the owner can subscribe at any time.
- **When the trial ends without a subscription.** The organization becomes read-only, like an
  unpaid one (F-255): people can still sign in, read, search, export and delete their data, but
  uploads, the assistant, automations and engine work are refused with a 402 `TRIAL_ENDED` and
  remedy `SUBSCRIBE`. Owners see a full-page "Your trial has ended; choose a plan" with the plan
  cards; everyone else sees "Ask <owner's name> to subscribe" (`useAskPlanOwners`).
- **Subscribing** at any point (during or after the trial) lifts every restriction at once and
  keeps all data.
- **Reminders** (*proposed*): an in-app banner and an email two days before the end, and an email
  when it ends.
- **Data after a trial that never converts** (*proposed*): kept for 90 days, with a reminder
  email at 60 days, then deleted through the existing erasure path, with a final notice. The owner
  may prefer a different period.
- **Existing Free organizations at launch** (*proposed*): their trial starts on the day this ships.


# Pre-launch campaign, session 2 (2026-10-11): documents, AI and every engine

The owner delegated founder authority for the campaign. Each item below is **DECIDED (campaign
session 2)**, built and tested on branch `claude/trusting-ptolemy-yiz005` (PR #20).

## N-053 — Building the one-week trial (N-052): the parts left open
**N-052 is BUILT (campaign session 2).** The owner decided the trial itself; these are the details
N-052 marked *proposed*, decided the way a Tier-1 SaaS (Claude, Linear, Vercel) does it:
- **Banner from day 5: kept.** The days left show in the last three days of the seven, to every
  member; owners get "Choose a plan", everyone else "Ask <owner> to choose a plan".
- **After the end: read-only, plus managing people.** As N-052 said, plus inviting, removing and
  changing the role of members, and archiving the organization stay open. Why: the person who will
  pay is often not the one who started the trial; an owner must be able to invite them.
- **The full-page notice is dismissible ("Continue read-only").** It shows once per browser session;
  a strip stays on every page. Why: N-052 promises reading, search and export after the end; a
  wall that cannot be closed would break that promise.
- **Data after a trial that never converts: kept, not deleted (changed from the proposed 90
  days).** Why: a Free organization stores at most 250 MB (about half a US cent a month), deleting
  customer data automatically is irreversible and is the most common source of angry support
  tickets, and the people who come back months later are the cheapest customers to win. The owner
  can still delete an organization; retention policies and erasure requests work as before. If
  storage cost ever matters, add a sweep that deletes after N days with a 30-day warning email.
- **Existing Free organizations at launch: seven days from the day this ships (kept).** The
  migration `s2a1_free_trial_clock` sets it. A paid organization that later lapses to Free is asked
  to subscribe (its trial is long over), not given a new one.
- **The two emails: kept** (two days before, and on the day it ends), each sent once per account;
  an email the relay did not accept is retried hourly inside its window. Session 3 owns the wording.
- **Setting:** `FREE_TRIAL_DAYS` (default 7) changes the length without a code change.

## N-054 — Which models the plans include, and what they cost
**DECIDED (campaign session 2).** Found while checking the model list against the providers'
pages (F-261, F-262).
- **The plans include the models FlowPilot serves on its own keys: GPT-OSS 120B (the default)
  and GPT-OSS 20B, on Groq.** A plan's token allowance is counted in tokens, and a token on Gemini
  2.5 Pro costs FlowPilot 33 times one on GPT-OSS 20B; one allowance cannot cover both at the
  same price. Gemini 2.5 Flash, 2.5 Pro, 3.5 Flash and Groq's Qwen 3.8 27B (a Preview model Groq
  may withdraw at short notice) **run on the organization's own provider key** (BYOK, where the
  customer pays the provider and FlowPilot charges no overage, N-048). This is how Tier-1 tools
  offer premium models without pricing every plan for the dearest one. If the platform's key
  would ever be asked for one of them (an old setting, a removed key), the default model answers
  instead; nothing fails.
- **The price book costs every model at its supplier's list price** (checked 2026-10-11), and
  prices it at least twice that. `tests/scripts/test_price_book_matches_supplier_rates.py` holds
  the supplier prices: when a provider changes one, change it there first.
- **Allowances resized to keep every paid plan at 60% or more at its ceilings, on the real
  default model:** Business 40,000 OCR pages and 100M input tokens a seat (were 50,000 and 150M);
  Enterprise 300M input and 75M output tokens a seat (were 500M and 125M). Everything past them
  bills overage at several times cost, as before. Developer and Free are unchanged. Existing
  subscribers keep the version they bought (`--carry-forward` moves only to no-worse versions).
- **Reversible later:** model-weighted credits (counting a premium model's token as several) would
  let the plans include premium models; it needs a credit unit on every usage screen and was not
  worth the risk before launch.
- **Owner action:** none in code. On deploy re-run `seed_price_book.py --version auto` and
  `seed_quota_tiers.py` (RUNBOOK 9.3). Spot-check the Groq and Google prices on their pricing pages
  before launch; this environment could only read them through third-party trackers.

## N-055 — The matching queue and copies of one invoice
**DECIDED (campaign session 2), the known leftover "one case per copy of an invoice".** The same
invoice often arrives twice: emailed and then posted, rescanned, re-sent by a supplier chasing
payment, or split out of a scanned packet. **One case per invoice, the first copy to arrive;
later copies open no case and are listed on it** ("This invoice arrived 3 times", with each
copy's file and date, and a "2 more copies" badge in the queue). An invoice is the same invoice
when it has the same supplier and invoice number in the same workspace, or is the same file.
Why: two cases for one bill invite paying it twice, the exact loss three-way matching exists to
stop; and flagging the copies where the reviewer decides is more useful than a separate list.
A case a copy opened before this change leaves the queue (superseded) the next time any copy of
that invoice is scored; a case someone already approved or disputed is left as decided. The
forensic radar still reports duplicates as findings, as before.

## N-056 — What an API key may call
**DECIDED (campaign session 2), with F-271.** An API key may call only a route that one of its
scopes covers, and nothing else (default deny), which is what the key screen and the code always
promised; until now only the webhook routes checked, so any key acted with the full rights of the
admin who issued it. `work_items:read` covers listing documents, one document and its extracted
content; `work_items:write` covers uploading, reprocessing and deleting documents ("upload and
modify documents", as the key screen says). Organization management (members, invitations,
settings, keys, billing changes) and the assistant stay human-only: no scope covers them. The full
list is `ROUTE_SCOPE_MAP` in `backend/app/core/scopes.py`. Why: a key is a long-lived secret
pasted into other systems; a leaked read key must not be able to delete records or read who works
here. A customer who needs more from the API asks for a scope to be added, deliberately.

## N-057 — How an organization's sign-in policy applies
**DECIDED (campaign session 3), with F-276 to F-278.** "Require single sign-on" and the IP
allowlist hold on every request into the organization: every organization and workspace page and
API route, and the live review channel. Single sign-on applies to people, not to API keys (a key is
not a person and cannot sign in through an identity provider); the session must come from **this**
organization's provider, not any provider. Owners keep password access while "Owners can bypass
SSO" (break-glass) is on, as the console says. The IP allowlist applies to people **and** API keys;
saving a list that leaves out the person saving it is refused, and the list cannot be turned on
until the deployment's proxy setting is confirmed (otherwise the address cannot be trusted). Links
that carry their own token are not bound by the allowlist: a calendar feed is fetched by Google's or
Microsoft's servers, and a document request is answered by an outside supplier. Why: this is what
Okta, GitHub Enterprise and Slack Enterprise do, and a security switch shown as "on" must be on.

## N-058 — The "IdP session sync" flag
**DECIDED (campaign session 3): removed.** It was stored, shown and read by nothing. A signed
single-logout request from the organization's identity provider always ends that person's FlowPilot
sessions (F-205), and that is not something a security policy should let anyone turn off. The
column is dropped (migration `s3a1_drop_idp_session_sync`).

## N-059 — Directory sync and single sign-on after a downgrade or an archive
**DECIDED (campaign session 3), with F-279 to F-282.** When an organization leaves Enterprise, or
is archived or suspended, its identity provider and SCIM token **never add anyone** (no new member
by sign-in, no SCIM create or reactivation), but **offboarding always works** (deactivate, delete,
remove from a group) and people already in the organization still sign in. Why: a leaver must be
removable whatever the plan or billing state, and nobody should be locked out by a plan change;
adding people is what the plan pays for. The same holds while an organization is read-only for
non-payment.

## N-060 — Organization-wide "require two-factor" (N-017 decided again)
**DECIDED (campaign session 3): built, on every plan.** An owner can require two-factor sign-in
for the whole organization (Organization → General). It is a security baseline, not an upsell:
GitHub and Slack offer it on their free plans. Rules: the owner must have two-factor on their own
account first (so the switch cannot lock them out); the card lists who has not turned it on before
confirming; from then on a person who signed in with a password and has no second factor is
refused everywhere in the organization until they turn it on, which they can do from the notice
itself; people who sign in through the organization's identity provider are not asked (the
provider's own two-factor check applies); API keys are not people; and nobody can turn off their
second factor while an organization they belong to requires it. Migration `s3a2_require_two_factor`.

## N-061 — Request limits for people in the browser (the per-tenant question)
**DECIDED (campaign session 3).** Signed-in people are limited **per person** (600 requests a
minute, `RATE_LIMIT_USER_PER_MINUTE`), anonymous traffic per network address, API keys per key (as
before). There is **no organization-wide request ceiling**: cost is already bounded by each plan's
allowances, and a ceiling shared by a whole organization would let one runaway script block every
colleague. Before this, every browser request was counted per address (the per-person limit
existed and nothing used it), so a company behind one office address shared one allowance. This is
how GitHub and Linear count.

## N-062 — Which notifications are emailed, and to whom
**DECIDED (campaign session 3).** Every notification appears in the app. By email:

| Category | Emailed | Who | Can it be turned off? |
|---|---|---|---|
| **Security** (roles, SSO and identity provider changes, SCIM keys, verified domains, the sign-in policy) | always | owners and administrators; a role change to the person | No |
| **Billing** (payment failed and each dunning step, read-only, subscription started/ended/paused, plan changes, add-ons, a plan limit reached) | always | owners and Billing members (administrators see it in the app) | No |
| **Usage warnings** (80% of a monthly plan limit) | yes | owners (they can change the plan) | Yes, per person |
| **Documents and automations** (a document that failed, an automation paused or failing) | warnings and errors only | the people the notice is for | Yes, per person |

Security and billing mail goes through FlowPilot's own mail relay, never an organization's SMTP
settings (those can be misconfigured, and are what is in question when the notice is about that
organization). Each email has a button to the right page and a link to the person's choices
(Settings → Notifications). Limit alerts go once per meter, limit and month. A routine notice is
never emailed. This is how Stripe, Linear and Slack do it: you cannot unsubscribe from "your
payment failed" or "someone changed your role".

## N-063 — Who can be given review work and own obligations
**DECIDED (campaign session 3).** Whoever can work in the workspace: review items go to its
contributors and administrators, and obligations to anyone who can open it, **including the
organization's owners and administrators**, who administer every workspace without a separate
grant (as calendar feeds already did, F-249). When someone loses access (their grant is revoked or
lowered, they are deactivated, they leave, the directory removes them), the review items they held
go back to the queue unassigned and their obligations lose their owner, at once, so nothing waits on
a person who cannot open it.

## N-064 — Whose settings decide how dates, times, numbers and money read
**DECIDED (campaign session 3).**

| What | Decided by | Example |
|---|---|---|
| A moment (uploaded at, signed in at) | The person's time zone; the workspace's if they never set one | "12 Oct 2026, 3:05 pm IST" |
| A calendar date on a document (due date, invoice date, contract end) | The **workspace's** date format, for everyone in it | "15/12/2026" in a DD/MM/YYYY workspace |
| Numbers and amounts | The person's language | "1,234.5" or "1.234,5" |
| Money | Its own currency, in the person's language; no currency means a plain number (never a guessed symbol) | "€1,234.50" |
| Exports, webhooks, calendar feeds | Always ISO 8601, whatever the settings | "2026-12-15" |

Why the workspace decides document dates: a team should read "03/04/2026" the same way the
documents and three-way matching do, whoever is looking. Why the person decides the rest: one
workspace serves people in different countries. Emails the server sends to several people state
moments in UTC, saying so. This is how Xero and QuickBooks split organisation and user settings.

## N-065 — Where an organization's brand colours show
**DECIDED (campaign session 3).**

White-label branding is sold for the organization's own address (a verified custom domain), so
that is where the brand shows. On the platform's own address everyone sees FlowPilot's colours.

| Setting | Where it shows on a verified domain |
|---|---|
| Primary colour | Buttons, links and focus rings, on the sign-in pages and in the app |
| Accent colour | The glow behind the sign-in pages |
| Background and text colours | The sign-in pages' backdrop (the app's own surfaces stay, so text never lands on a background it was not designed for) |
| Colour scheme (light, dark, system) | The sign-in pages. Inside the app, light or dark stays each person's own choice, as accessibility settings should |

The text on a primary-coloured button is worked out from the colour (white or near-black,
whichever contrasts more), so a pale brand colour still gives readable buttons. This is how
Okta and Zendesk treat custom-domain branding.

## N-066 — What "data residency" means on this deployment
**DECIDED (campaign session 3).**

The region an owner picks decides where two things are written: **compliance export archives** and
the organization's **branding files** (logo, favicon). Everything else (documents and their pages,
redacted copies, profile pictures, and the database with extracted text, fields, the search index,
members, settings and the audit log) is stored where this deployment runs.

Moving only document files to a regional bucket would not deliver residency either, because a
document's text and search index live in the database. Real residency for documents means a
**regional deployment** (a separate copy of the stack, database included, in that region), which is
an infrastructure step rather than a code change. Until one exists the screen says exactly what the
setting covers, the EU option no longer claims to satisfy GDPR agreements, and a test keeps the
statement equal to the code. When a regional deployment is stood up, list it on the card and in
the contract wording; nothing else needs to change.

## N-067 — How long an organization's audit log is kept
**DECIDED (campaign session 3).**

The organization decides, on `Compliance > Retention`: any period of at least 400 days, or blank to
keep the log indefinitely, exactly as the screen says. The nightly sweep deletes, per organization,
only entries older than the period it chose. Nothing is deleted for an organization that has not
chosen a period.

Why not a platform default (say 400 days) for everyone who never opens the page: audit entries are
evidence, deleting them cannot be undone, and regulated customers commonly need several years
(seven for financial records is usual). Keeping them costs little (they are small rows). If storage
ever matters, a default period can be introduced deliberately, announced to customers and shown on
the screen, rather than applied silently.


## N-068 — Which time zones and currencies a workspace can use
**DECIDED (campaign session 3), with F-320 and F-321.**

Any real time zone (the full IANA list, the same one the profile offers) and any three-letter ISO
4217 currency. The workspace page offered ten of each, chosen for the first customers, so a
workspace in Zurich, São Paulo or Toronto could not set its own clock or the currency three-way
matching falls back to. The common ten stay at the top of the currency list.

The server now refuses anything it cannot honour (an unknown zone, a currency that is not a code,
a date format the product does not render), where before it stored any text and quietly fell back.
Nothing about prices or billing currency changes: this is the workspace's operating currency for
reading documents, not what FlowPilot charges in.


# Pre-launch campaign, session 4 (2026-10-13): experience, operations, certification

The owner delegated founder authority for the campaign. Each item below is **DECIDED (campaign
session 4)**, built and tested on branch `claude/kind-hamilton-5lpp19` (PR #23).

## N-069 — Refunds
**DECIDED (campaign session 4): record every gateway refund on the invoice it pays back, tell the
organization, and show refunds as their own cash figure.** A refund is made by the owner in the
Stripe Dashboard (there is no refund button in FlowPilot: a refund is a money decision a person
takes, usually after a support conversation; see the refund playbook in RUNBOOK §9.8). When Stripe
reports it (`charge.refunded`, and `charge.refund.updated` if the bank later returns it), FlowPilot
re-reads the charge and records on the invoice how much of the payment was refunded and when; the
invoice keeps "paid" as its status, because the payment did happen, and the list says "Refunded" or
"Partially refunded · $X refunded", as Stripe's own invoice page does. Owners and Billing members get
a billing notice (always emailed, N-062). The RevOps console shows money refunded per currency in the
last 30 days next to receivables. **Why not a credit-note document and negative revenue lines:**
FlowPilot's recurring revenue (MRR) is what subscriptions are worth each month, and a refund does not
change that (a cancellation does, and is already counted as churn); a credit-note PDF is a document
customers in some countries need for their tax records, and Stripe issues one when the owner refunds
through "Credit note" rather than "Refund" in the Dashboard. Recording the refund where the customer
and the operator look is what was missing. Dodo Payments is the merchant of record and owns refunds
there, as before. **Owner action:** add `charge.refunded` and `charge.refund.updated` to the Stripe
webhook's events (RUNBOOK §9.2 step 9).

## N-070 — How the owner hears about errors after launch
**DECIDED (campaign session 4): email, from inside the stack, to an address the deployment must name.**
`OPERATOR_ALERT_EMAILS` (one or more addresses, required in production) is emailed through the same mail
relay as everything else when background jobs of one type fail for good, payment events cannot be
applied, the API returns 20 or more server errors in five minutes, or a customer has two live
subscriptions. Each alert repeats at most once an hour while it keeps happening. The uptime heartbeat
(N-017) still covers "the whole site is down", which an in-stack alert cannot report. **Why email and not
a monitoring service (Sentry, Datadog):** it costs nothing, needs no account or extra container, and the
four conditions are the ones a solo founder must act on; an error tracker is worth adding when there is
a team to triage a stream of individual exceptions (PRODUCT-NEXT.md). **Owner action:** put your address
in `OPERATOR_ALERT_EMAILS` before the first deploy (the app refuses to start without it).


## N-071 — How overage reaches the customer's card, and what "Invoices" shows
**DECIDED (campaign session 4): overage is billed in arrears on the next renewal, through Stripe.**
The plans "bill overage" past their allowances (OCR pages, storage, tokens; N-045, N-048), and the
margins in N-046 assume it is charged, but nothing ever charged it (F-339): no statement was written,
no line reached Stripe. Now, when Stripe opens a subscription's renewal at the end of a billing period,
FlowPilot writes its statement for the month that just ended (the seats, the allowances for that many
seats, the overage priced from the subscription's pinned price book) and adds each overage line to that
renewal before Stripe charges it, as one invoice line per meter ("Overage — ocr.page (1 Sep 2026 –
1 Oct 2026)"). The customer pays it with the next month's seats; the statement shows in Billing,
Invoices and is marked paid when the renewal is. This is how per-seat SaaS with metered extras bill on
Stripe (seats in advance, usage in arrears). Also decided:
- **A renewal that was already charged** (FlowPilot was down for over an hour): the overage goes on the
  following invoice, and the statement says so. A period whose renewal never reached FlowPilot is closed
  by an hourly backstop two hours after it ended.
- **The last month of a subscription that ends** has no renewal, so its overage is not charged. The
  amounts are small and bounded by the plan ceilings; charging it needs a one-off invoice at
  cancellation, listed in PRODUCT-NEXT.md.
- **Allowances on the statement are per seat, pooled** (N-044), as the usage checks already were.
- **Usage recorded after the statement is written** (late events, F-256) is not billed.
- **Dodo** cannot take usage lines yet (`supports_usage_billing` is false pending verification, N-026):
  on Dodo, statements are written and overage is not billed. Stripe is the launch gateway.
- **Owner action:** none in code. Keep `invoice.*` in the Stripe webhook's events (RUNBOOK §9.2 step 9).

## N-072 — What happens when a card is declined (Stripe)
**DECIDED (campaign session 4): Stripe retries, FlowPilot tells, and the account becomes read-only only
when Stripe gives up.** When a renewal is declined, Stripe's Smart Retries try the card again over about
two weeks. Each failed attempt emails the organization's owners and Billing members (FlowPilot's three
notices: "Payment could not be processed", "Payment still outstanding", "Final notice before access
changes"), and the app shows "Payment failed" with an "Update payment method" button from the first
one (F-341). FlowPilot's own dunning stops at those notices (`BILLING_DUNNING_MAX_STEP=NOTIFY_3`, so
two systems never race to restrict). When the retries run out, Stripe marks the subscription **unpaid**
and FlowPilot makes the organization read-only: everything can still be read, searched and exported,
nothing is deleted, and writes are refused with the reason. Paying the open invoice (a new card in the
billing portal) restores everything at once. This is the standard Stripe setup for B2B SaaS: no data
loss, a clear path back. **Owner action:** in the Stripe Dashboard, Billing → Revenue recovery →
Retries, choose Smart Retries and set "If all retries for a payment fail" to **Mark the subscription as
unpaid** (not cancel). Proven by journey 5 (`frontend/e2e/tests/64-journey-payment-fails.spec.ts`).

## N-073 — What the public pricing page says
**DECIDED (campaign session 4).** The public pages (`/pricing`, `/terms`, `/privacy`, CAMPAIGN-MAP
§4.3) read the plans from the same published source as the in-app plan cards (`GET /api/v1/pricing`),
so the page can never show a price or allowance the product does not sell.
- **What it states as fact:** each plan's per-seat monthly price, the counts a customer plans by
  (documents, OCR pages, assistant messages, storage, workspaces, seats), the one-week trial with no
  payment details needed, one trial per account, and read-only after the trial until someone
  subscribes (N-052, TENANCY-AND-PLANS §4.3). All of these are what the code enforces.
- **Enterprise's "priority 99.9% SLO":** kept. It is the capability the plan sells
  (`capability.priority_slo`, TENANCY-AND-PLANS §4.5) and the SLO console measures it. It is an
  objective, not a contractual guarantee with service credits; the terms page must not promise
  credits unless the owner's counsel adds them.
- **Overage prices and add-ons** (for example warehouse sync) are not on the public page: Tier-1
  pricing pages list what a plan includes and keep metered rates for the billing screens and the
  contract. Customers see overage on their Billing page before it is invoiced (N-071).
- **No "contact sales" button:** Enterprise is bought per seat in the app like the other plans. An
  invoiced annual contract (N-051) is arranged by email.
- **Terms and privacy are placeholders**, clearly marked as awaiting the owner's legal review, and
  make no compliance, certification, sub-processor, retention or jurisdiction claims (N-029).
- **Owner action (launch checklist):** have counsel replace the terms and privacy text; add a
  support or sales email address to the pricing page footer once you have one.

## N-074 — Decisions taken in campaign session 4 (recorded, nothing to do)

All **DECIDED (campaign session 4)** under the founder authority given for this session:

- **Tailwind 4 waits.** The seven development-only npm advisories (Tailwind CSS 3's file watcher and
  selector parser; nothing ships to the browser, `npm audit --omit=dev` is 0) are accepted for launch.
  Tailwind 4 rewrites the CSS configuration and needs a page-by-page visual comparison: a later
  session (HANDOFF).
- **One database user at launch.** The app connects as the database owner (the compose file's
  `POSTGRES_USER`). The audit-log trigger therefore guards against mistakes, not against someone with
  the database password; the nightly audit sweep names itself to the trigger for its own deletes only
  (F-355). Separate least-privilege roles (an app role that cannot alter tables, a sweeper role) are a
  later-session security item (HANDOFF), with migrations run by the owner role.
- **F-017's 283 undeclared index and constraint lines stay** (PERFORMANCE.md "Indexes"): they are real
  database objects created by migrations; the drift gate's baseline can only shrink.
- **Accessibility target:** WCAG 2.1 AA, no serious or critical axe finding on any page in either theme,
  enforced by `72-accessibility.spec.ts` in CI (F-362). The brand blue and red were darkened slightly
  for it.
- **First run:** every new workspace shows "Get started with FlowPilot" with a built-in sample invoice
  (§A.4); it hides itself when done or when the person hides it.


## N-075 — What a Document pack does today (DECIDED, campaign session 4)

**DECIDED (campaign session 4):** the Document packs card tells the truth now (F-408): enabling a pack
makes new documents that mention at least two of its phrases be read with its field list. Its
suggested checks and redaction profile are shown as **recommendations**: nothing creates the checks
and redaction never uses the profile. Wiring them in (enable a pack → its checks are proposed in
Clause assertions; a redaction of that document type pre-selects the pack's profile) is a product
item in `docs/PRODUCT-NEXT.md`, not a launch blocker. Nothing for you to do.

## N-076 — Extra files in an export package (DECIDED, campaign session 4; build in a later session)

A package with an extra file outside its folder, or two copies under one name, still verifies.
**DECIDED:** any extra file is "Tampered", except the two things macOS adds when someone re-zips a
folder (`__MACOSX/…` and `.DS_Store`), which are ignored. To build next session (the batch audit
reproduced it; no committed test yet).

## N-077 — Small product rules settled in campaign session 4 (build in a later session)

All **DECIDED (campaign session 4)**; each is listed in `NEXT-SESSION-PROMPT.md`:
- **Conversation retention counts from the last message,** not from when the conversation started
  (today a conversation still in use can be deleted by the nightly purge).
- **Safe Harbor wording:** the notice names what the HIPAA profile actually removes (names, dates
  of birth, SSN, card numbers, email, phone) and says the other Safe Harbor identifiers (for example
  medical record numbers and admission dates) need a manual pass; it still never says "HIPAA
  compliant".
- **Dispatch tags are history:** switching tagging off keeps the tags already written; the batch
  page says so next to the switch.
- **`/no-access` goes away:** it redirects to the workspace picker, which already explains archived
  and suspended organizations more accurately (only support can lift a suspension).
- **The PAN card pack** gets a second recognising phrase ("income tax department") so a real PAN card
  is matched.

## Helm, the flagship engine (flagship session, 2026-10-14 onwards)

The brief for the flagship session took eight decisions with the owner before it started; they are
recorded here as given and are not reopened. Decisions taken during the build follow them, each
marked `DECIDED (flagship session)`.

## N-078 — What Helm is (DECIDED (owner and planning chat, 2026-10-15))

Workflows say "when X happens, do Y". Helm replaces that with **outcome control**: the company
states outcomes and hard limits, called **mandates** ("never pay above the agreed price", "keep
cash above ₹10 lakh", "pay micro and small suppliers within the legal limit"), and Helm keeps the
business inside them all the time, like a thermostat or a fund manager under a written mandate
with pre-trade compliance. Its five parts are always named the **Promise ledger**, **Mandates**,
the **Planner**, the **Guard** and the **Time machine**.

## N-079 — The deterministic core decides; AI only assists (DECIDED (owner and planning chat, 2026-10-15))

Every decision, check, plan and number comes from deterministic code (ledger rules, the optimiser,
the simulation, the Guard). AI, through the platform's existing models and metered as today, only
turns a sentence into a mandate draft, which deterministic code then validates. Explanations are
deterministic templates. Helm works fully with AI switched off or the model stand-in.

## N-080 — Zero external cost (DECIDED (owner and planning chat, 2026-10-15))

Only existing dependencies: `scipy.optimize.milp` (HiGHS, inside SciPy) for the optimiser, NumPy
for the simulation, reportlab for PDFs. No new paid service and no external data feeds. No
currency conversion: each currency is planned on its own.

## N-081 — Money never moves inside Helm (DECIDED (owner and planning chat, 2026-10-15))

Helm decides, prepares, holds, schedules, reminds, requests and proves. Paying happens in the
customer's bank or accounting system, through a payment run list a person executes (or the
existing ERP posting where the customer uses it). No bank payment files in this version: they would
carry full account numbers and are a fraud target.

## N-082 — Which plans get Helm (DECIDED (owner and planning chat, 2026-10-15))

- **Business:** Helm with the library mandates, Propose and Act modes, a 6-month backtest, a
  90-day forecast and up to 15 active mandates.
- **Enterprise:** adds unlimited mandates, custom condition mandates, approval chains, planning
  across all workspaces, a 24-month backtest, API access and webhooks.
- **Trial and Developer:** **Helm preview**: the sample company, one backtest a week on their own
  documents of up to 3 months, and an upgrade path. Read-only apart from that backtest and opening
  the sample company.
- Prices do not change. New capability `capability.helm` and `limit.*` rows through the existing
  seeds, plan cards and `TENANCY-AND-PLANS.md`.

## N-083 — Who Helm is for first (DECIDED (owner and planning chat, 2026-10-15))

Mid-sized companies that buy from many suppliers (distributors and wholesalers, manufacturers,
logistics and transport, construction and contracting, multi-branch retail, restaurants and
clinics; typically 50–1,000 people with a small finance team), and accounting and bookkeeping firms
that run payables for such clients, with one workspace per client. India first (INR, GST invoices,
the MSMED Act payment limits, income-tax section 43B(h)); global-ready (any currency and locale;
India-specific rules sit in an "India" mandate pack). Not the first target: tiny shops with no
purchase orders or contracts, and large enterprises with long security reviews.

## N-084 — Safety defaults (DECIDED (owner and planning chat, 2026-10-15))

New mandates start in Propose mode. Act mode runs actions only up to an amount cap an organization
owner or admin sets per mandate; the default cap is 0, so payments always ask. The Guard re-checks
every action at execution time. Every action and decision is in the audit log. Helm shows how much
of the business it can see (a visibility meter) and never presents a guess as a fact.

## N-085 — Truthful claims (DECIDED (owner and planning chat, 2026-10-15))

"Value held" counts only outcomes backed by evidence; projections are labelled "potential". Helm
gives recommendations, not financial or tax advice; the MSMED / 43B(h) mandate links to the law and
says so.

## N-086 — Books: whose business Helm runs (DECIDED (flagship session))

Helm keeps one "book" per workspace on Business; Enterprise may also keep one book for all its
workspaces together. The sample company has its own book in its own labelled workspace, which never
counts towards the plan's workspace limit and can be deleted (it is archived). A Helm-preview
backtest on a lower plan creates a read-only book that never plans or acts.

## N-087 — Who sees and does what in Helm (DECIDED (flagship session))

Owners and admins see Helm and decide (mandates, act caps, approvals). The billing role sees
everything, approves payment actions, records payments and imports bank statements (it is the
finance contact). Members never see Helm mode; they receive the tasks Helm assigns them in the
review hub. The Documents | Helm switch remembers the last mode per browser.

## N-088 — How a payment leaves Helm (DECIDED (flagship session))

Helm never moves money. It hands over a **payment run list** (date, payee, masked account, amount,
invoice, certificate; downloadable as CSV) for the bank or the accounting system. The platform's
ERP model has no payment-date or hold field on a supplier bill, so the list is the hand-over; Helm
writes no bank payment files.

## N-089 — How holds end (DECIDED (flagship session))

A hold Helm placed ends when its reason is gone (for example the duplicate finding was dismissed):
Helm proposes releasing it, and the release is guarded, certified and undoable like any action. A
person who settles a "missing purchase order" task as done waives that reason for that invoice for
good.

## N-090 — First run and examples (DECIDED (flagship session))

Setup is four steps: who we are, the cash, the kind of business (distributor, manufacturer,
logistics, construction, multi-branch retail/restaurants/clinics, accounting firm, other) with the
mandates recommended for it, and an automatic replay. A gallery value with no default (a cash floor,
a PO threshold) is an **example**: Helm never saves or replays it as the owner's own number; the
owner must set it.

## N-091 — The Monday briefing (DECIDED (flagship session))

09:00 on Monday in the book's workspace time zone (an all-workspaces book uses its organization's
oldest workspace), to the organization's owners and admins who have not turned it off; once a week;
never for the sample company. It links to the cockpit and to the person's own notification
preferences.

## N-092 — Writing a mandate from a sentence (DECIDED (flagship session))

On Business a sentence maps only to library mandates (Enterprise adds custom conditions). The AI's
answer is checked strictly; it never sets how a mandate acts (every new mandate starts in Propose
mode); suppliers it names are matched to the book's own, and an unknown one is named, not guessed.
When the AI is unavailable or unsure, the gallery's closest mandates are offered. The call is
metered like an assistant message and refused cleanly (402) when the AI usage limit is reached.

## N-093 — Replays and their honesty classes (DECIDED (flagship session))

A replay counts value in three classes that are never added together: confirmed (evidence shows it
happened), likely, and potential (facts are missing). Business replays 6 months, Enterprise 24,
Helm preview 3 months once a week; the sample company may be replayed whole (9 months) on any plan.

## N-094 — The sample company (DECIDED (flagship session))

Meridian Wholesale Pvt Ltd, an invented Pune distributor, built on request in each organization
from nine months of generated documents that the platform's own engines read. Its planted problems
(overcharges, a duplicate paid twice, a bank-account change, late MSME payments, missed discounts, a
missed renewal, an expired certificate, late customers, a cash dip) are exactly what its replay
finds. Invented names only; it is also the data for the website's screenshots.

## N-095 — Bank statement import (DECIDED (flagship session); owner addition A)

- Files: CSV and Excel (.xlsx with openpyxl, MIT licence; old .xls with xlrd, BSD licence); both are
  free, local libraries, no service is called. Up to 5 MB and 20,000 lines a file.
- Accounts: a name, the currency and only the **last four digits** of the number. The column
  mapping is confirmed once by a person and saved for the account.
- Duplicates: the same file twice adds nothing; an overlapping period adds only lines not seen
  before (two identical payments on one day stay two).
- Cash: an import's closing balance becomes that account's cash. Two accounts in one currency are
  **added together**; the total is only as current as its oldest part; a figure a person typed on
  the same day wins.
- Matching by itself only when the evidence leaves one answer: the exact amount (or the amount less
  its early-payment discount); the invoice number, or the payee's name within the date window of a
  payment Helm scheduled or a person approved; one invoice only, claimed by one line only. Money in
  settles invoices owed to us the same way. Everything else is suggested to a person, with the
  reasons; a suggestion to an invoice Helm held warns that the hold may not have stopped the
  payment. Part payments are not matched (recorded with "Record paid").
- A match is an ordinary "Record paid" action with the bank line as its evidence: guarded,
  certified, audited and undoable; reopening a line undoes it.

## N-096 — Supplier statement reconciliation (DECIDED (flagship session); owner addition B)

- A statement of account from a supplier is **never** read as a bank statement: it does not mark
  invoices paid and its balance is not cash. When nothing on a statement says whose it is, it is
  treated as a bank's, as before.
- Each month (statements dated in the last 45 days), every supplier statement is compared with the
  ledger of its workspace. Differences up to ₹1 (or 1 unit of the currency) are rounding; the owner
  can change both in the mandate.
- Helm drafts one reconciliation email per statement that differs, written to the supplier ("your
  statement lists…"), never mentioning Helm. It is sent only in Act mode to a known contact, and only
  after a person approves when the statement's facts were not checked. A rejected or sent draft is
  not proposed again for the same statement.
- The mandate claims no "value held": reconciling prevents paying twice or paying what is not owed,
  but no amount is counted until it is proven.

## N-097 — The guided tour (DECIDED (flagship session); owner addition C)

- On the sample company only, six steps, started from Start or the sample's cockpit; it never runs
  on a customer's own book. The step lives in the page address, nothing is stored.

## N-098 — Approval chains (DECIDED (flagship session); Enterprise, box 7)

- A chain is a list of tiers per book: above an amount in one currency, a payment needs 2 or 3
  approvals from different people, optionally only from chosen roles (owner, admin, billing). The
  highest tier that applies decides. Holds, releases, recording a payment and anything that moves no
  money keep one approval. Helm's own approval (Act mode) never counts towards a chain, whatever the
  act cap.
- A chain the organization's active members could never meet is refused when it is typed. A saved
  chain the organization later cannot meet (someone left or changed role) is kept and the settings
  page warns; those payments wait.
- An approval counts with the role the person had when they gave it; an approval from someone who
  later leaves still counts (it was valid when given and is in the audit log).
- A partial approval does not carry over when a re-plan replaces the action with a different one (a
  changed amount or date is a new decision).
- Raising a chain applies at once; lowering it applies after the re-plan the change triggers.
- Below Enterprise a saved chain stays stored and stops applying; it applies again after an upgrade.
- The same person approving twice gets a clear "ask someone else" (409), not a silent no-op.

## N-099 — Custom condition mandates (DECIDED (flagship session); Enterprise, box 7)

- A rule is one to eight rows of fact, comparison and value over a closed list of an invoice's facts,
  with one outcome: hold, ask a person before paying, or notify. Several may be active at once.
- After a move to Business a rule already in force keeps working (holding what it matches) until a
  person pauses or archives it; it cannot be edited or switched back on, and new ones are refused.
  Turning a working safety rule off silently on a downgrade would be worse than keeping it.
- The MSME fact means micro or small (the MSMED Act's 45-day rule), not medium.

## N-100 — What if… (DECIDED (flagship session); box 7)

- On every plan that has Helm (Business and above, and the sample company), for owners, admins and
  billing: a what-if saves nothing and moves nothing, so it needs no Enterprise gate.
- Three levers only for now: a one-off cash change, customers paying later (0 to 90 days), invoices
  held out of the plan. More levers (a lost customer, a price rise) can follow once owners ask.

## N-101 — The replay as a PDF (DECIDED (flagship session); box 7)

- the replay PDF is available wherever the replay can be read, on every plan:
  Helm preview downloads its own preview replay, and every plan can download the sample company's replay.
  The plan's replay rules (3 / 6 / 24 months, preview once a week) apply when the replay is run, not when it
  is downloaded, because the PDF holds nothing the page does not already show.
- a replay run on a bigger plan stays downloadable after a downgrade, as the
  replay itself stays readable. It was within the plan when it ran, and it is the customer's own data.
- a replay that failed gets a 409 (`BACKTEST_FAILED`, "could not finish …, so
  there is no report to download. Replay the period again."), not a 404. The replay exists; its state is
  what blocks the report. A running replay also gets a 409 (`BACKTEST_NOT_FINISHED`).
- a Billing member who cannot read the book's workspace gets the PDF with the
  totals, the mandates and what Helm could not see, but no findings, plus a sentence saying why. This
  matches what the page shows them.
- money in the PDF uses the screen's symbol when Helvetica can draw it ($, €, £)
  and the currency code otherwise ("INR 1,23,456.00"; "₹2.4 lakh" in a summary becomes "INR 2.4 lakh").
  Embedding a font with the rupee glyph would mean shipping a font file in the image. The code is also
  unambiguous for an accountant.
- the file name is `helm-replay-<book name, ASCII>-<start>-to-<end>.pdf`.
  Accents are dropped and other scripts fall back to "book", so no browser or mail client mangles it. The
  page uses the same name, taken from the replay's JSON, because the browser cannot read
  `Content-Disposition` cross-origin (it is not in `CORS_EXPOSED_HEADERS`).
- each download is audited as an export (`HELM_BACKTEST` / `EXPORTED`) with the
  fingerprint, `findings_listed` and the size. This follows the other data exports (corroboration, tables,
  obligations, audit log). The certificate JSON download is not audited today; that was left as it is.

## N-102 — Helm through API keys and webhooks (DECIDED (flagship session); Enterprise, box 7)

- A key may feed Helm facts and ask it to work (cash positions, an invoice marked paid, bank accounts
  and statements, a re-plan, a mandate preview, a what-if, the Time machine and its PDF). It may not
  decide or set Helm's rules: approving, rejecting or undoing an action, books, settings,
  counterparties, mandates and the sample company stay with people, refused in words whatever the
  key holds. Approving a payment is a person's act.
- `helm:read` for every read, `helm:write` for the writes a key may make, previews and what-if
  included (they are POSTs that save nothing).
- A member's key never carries Helm's scopes; a billing member's key only `helm:read`.
- Three events: a new plan, an action that ran, a mandate newly breached. Never for the sample
  company. Payloads carry ids, kinds, states, counts, dates and money in micros with its currency;
  never document text, a person's or supplier's name, an explanation or a mandate's sentence.
- Every refusal of a key is audited (rate-limited summary rows past 50 an hour).


## N-103 — The owner's decisions for the website and payments session (DECIDED (owner, planning chat, 2026-10-15))

- **Name and addresses.** The product is **Servabo** ("I will keep", from *pacta sunt servanda*).
  Positioning: "The AI back office that keeps every promise your documents make." Website
  `https://servabo.app` (`www` redirects), app `https://app.servabo.app`; every place reads one
  setting (`WEBSITE_URL`, `FRONTEND_URL`, the website's `PUBLIC_*` build variables). Fallback if the
  domain is taken: `getservabo.com` / `app.getservabo.com` (`docs/website/DEPLOY.md` has the switch).
- **Operator and contact.** Sabarish N, sole proprietor in India; public contact
  sabarishwarne@gmail.com (one setting, to become support@servabo.app). No phone number is committed
  or published; `PUBLIC_SUPPORT_PHONE`, when set at build time, shows on the contact page only.
- **Payments.** Dodo Payments (merchant of record) is the only gateway customers see;
  `BILLING_GATEWAY=DODO` by default; the Stripe adapter and its tests stay, switched off, and Stripe
  is never shown to customers.
- **Prices unchanged.** Developer $49, Business $299, Enterprise $799 per seat per month; 7-day trial
  without a card; yearly Developer $490 and Business $2,990 per seat; Enterprise yearly by agreement.
- **No usage overage on Dodo at launch**: limits that would bill overage are hard ceilings; the
  customer adds seats or upgrades.
- **Refund and cancellation policy** exactly as the website's `/refunds` page and the terms state it
  (cancel any time to the end of the paid period; a 14-day money-back guarantee on the first payment
  of a new subscription, once per organization; no other refunds; errors always refunded in full;
  refunds paid by Dodo to the original payment method; read-only after cancellation).
- **Website**: a static site in `website/`, hosted on Cloudflare Pages, with a Caddy block as the
  alternative.

## N-104 — The rename: history, compatibility and names (DECIDED (website and payments session))

- History stays history: FINDINGS, NEEDS-OWNER, STATE, past CHANGELOG entries, `archive/` and existing
  migrations keep the old name (a header line says so); a guard test refuses it anywhere else.
- For **one release**, the old setting prefix, API keys with the old `fp_live_`/`fp_test_` prefixes and
  the `--loop stripe` / `worker-stripe` names keep working, with one start-up warning. New API keys
  start `svb_live_` / `svb_test_`. Other protocol identifiers (headers, cookie, TXT record,
  WebSocket subprotocol, storage keys, licence format) changed at once; CHANGELOG lists each.
- The GitHub repository keeps its current name and URL (renaming it breaks clones and links for no
  customer benefit); the owner can rename it any time, GitHub redirects.
- Database tables and columns keep their historical names (docs/billing/DODO.md §7).

## N-105 — Where it runs (DECIDED (website and payments session))

One Hetzner server for the app (4 vCPU, 16 GB, EU region; best price for the size, COSTS.md) and
Cloudflare R2 with EU jurisdiction for customers' documents (no egress fees; EU data residency helps
with EU customers' GDPR questions). The website on Cloudflare Pages (N-103). RUNBOOK §9 and the
HANDOFF launch checklist follow this.

## N-106 — Website copy, emails and consent (DECIDED (website and payments session))

- The website's copy follows N-085 (truthful claims): no customer logos, testimonials or numbers we
  do not have; Helm's "what we never claim" list holds on every page.
- Emails sent under the Servabo brand carry the Servabo mark and links to the website's terms and
  privacy pages; white-labelled emails (an organization's own branding) carry neither.
- The sign-up page says "By creating an account you agree to the Terms and Privacy Policy" with links,
  on white-label domains too: the contract is with Servabo whatever the domain.
- The contact page uses a mailto link, never a form (nothing to store, nothing to spam).

## N-107 — Plan and seat changes on Dodo (DECIDED (website and payments session))

- Plans change on Servabo's Billing page, not in Dodo's portal (Dodo's portal manages the card,
  invoices and cancellation). The owner sees Dodo's own preview first and confirms that exact amount;
  a different amount at confirmation is refused.
- **Upgrade:** at once, charged for the days left at the price difference
  (`prorated_immediately`); if the card fails, nothing changes (`prevent_change`).
- **Downgrade:** at the next renewal, so the paid period keeps what was paid for; it is made at Dodo
  an hour before the renewal (`do_not_bill`) after checking the smaller plan fits; it can be withdrawn
  until then.
- **Seats:** priced by Dodo's preview, charged at once for the days left (`prorated_immediately`).
- Only an organization owner changes the plan.

## N-108 — Enterprise OCR allowance: 95,000 pages per seat (DECIDED (website and payments session))

After Dodo's fees (4% + 40¢, +1.5% international, +0.5% subscription) Enterprise kept 54.7% gross
margin at its ceilings with 100,000 OCR pages a seat, under the 55% rule every paid plan must meet.
The allowance is now 95,000 pages a seat; the price is unchanged. COSTS.md §5 shows every plan's
margin at its ceilings and at typical use.

## N-109 — Notices while a Dodo renewal is on hold (DECIDED (website and payments session))

A declined renewal keeps full access for 13 days (`BILLING_ON_HOLD_GRACE_DAYS`, chosen to match
Dodo's default retry window; that window is unverified, DODO.md §6). The owner and billing members are told at once ("Payment failed"), reminded 7 days
before the end ("Payment still outstanding"), warned 2 days before ("Final notice before access
changes") and told when the organization becomes read-only. Only the latest true notice is sent, once
per hold. Same words as the Stripe path's dunning, so customers hear one voice.

## N-110 — Telling Dodo test keys from live keys (DECIDED (website and payments session))

Dodo does not document how a live key differs from a test key, so Servabo does not guess from the key.
`DODO_LIVEMODE` chooses the host (test or live), the start-up guard refuses a host that disagrees with
it, and `scripts/dodo_setup.py` refuses live mode outright. A live key sent to the test host is refused
by Dodo itself. **Unverified** against a real Dodo account (DODO.md §6).

## N-111 — Helm's look is Night Bridge, replacing the "calm instrument panel" (DECIDED (Helm visual session))

The flagship brief's §10.2 asked for a calm instrument panel: hairlines, one accent, monospace numbers.
The owner's verdict on it was "a regular demo", and their wish was a top-tier flagship look. Helm now
wears **Night Bridge** (`docs/helm/VISUAL.md`): a near-black canvas with a green undertone, frosted glass
panels, one signal green, rings that encode values, a slow aurora behind everything, and motion that
explains change. Light mode ("Day Bridge") is finished to the same standard. The reason in plain words:
Helm is what sells Servabo, so it should look like nothing else in the market, while every number on
screen stays real and every state is still a word and an icon, not only a colour.

## N-112 — Rupees in Helm always use Indian grouping (DECIDED (owner brief, Helm visual session))

INR amounts in Helm print as ₹6,59,440.64 and ₹3,24,45,975.20 in every language, because Helm's own
sentences say "lakh" and "crore"; other currencies keep following the person's language (N-064). Short
figures use lakh and crore (₹6.6L, ₹1.2Cr) and write out anything under a lakh (₹1,453), because
Chromium's Indian English writes thousands as "T", which reads as trillions. Only in Helm, not app-wide.

## N-113 — Documents gets its own look, Lumen, not Helm's (DECIDED (Helm visual session))

The owner asked for Documents, the main product, to be upgraded visually too, in its own style rather
than copying the reference images. Documents wears **Lumen**: paper-clear surfaces, the brand blue with a
violet glow, a still light on the canvas, frosted header, lit active page, light-edged cards. It is calm
on purpose (nothing moves by itself), because people work in it all day; Helm keeps the drama. The brand
colours the website shares did not change, so the website's look outside its Helm page is the same.

## N-114 — The mode switch starts its view transition itself (DECIDED (Helm visual session))

The brief asked to navigate with React Router's `viewTransition` option. In this app it does nothing:
the pages are descendant `<Routes>` under one router route, and there React Router drops the option.
Rather than restructure every route, the switch calls `document.startViewTransition` itself and waits
(at most a second) until the other mode's page is on screen. The effect is the one asked for; reduced
motion and browsers without View Transitions navigate plainly.

## N-115 — The cockpit's KPI strip and Promise calendar (DECIDED (Helm visual session))

Six tiles, each from data the API already had: mandates holding (with a delta against 7 days ago only
when the daily trends hold a state for then), cash floor confidence (or the lowest cautious cash when no
floor is set), money at risk, waiting for approval, payments planned, and value held confirmed. Money in
the tiles is short (₹22.5L) with the exact figure underneath. The Promise calendar needed one small
read-only endpoint (`GET …/books/{id}/calendar`): amounts only from workspaces the viewer can read;
promises elsewhere are counted, never summed. Enter on a day opens the Promise ledger on that day.

## N-116 — The website's Helm page and screenshots (DECIDED (Helm visual session))

The Helm page wears Night Bridge (glass, signal green, big numerals for four facts about how Helm works,
the screenshots in glass frames with a soft glow) and a looping Documents → Helm switch demo in the hero
(CSS and SVG only, no video; it loops only while on screen and stands still, showing Helm, under reduced
motion). The rest of the site keeps the Servabo brand; the home page's Helm shots get the same glass
frame. Screenshots are taken as an Indian reader (`en-IN`, Asia/Kolkata). The shot that showed a Guard
certificate is now called `helm-certificate`; `helm-plan` is a true picture of the payment run.

## N-117 — The Documents Overview in Lumen (DECIDED (Helm visual session, owner's added task))

The owner asked for the Overview to be upgraded in its own style, with the reference images as ideas
only. It now opens with a greeting and a live pill, then a hero card (the total as a big numeral, the
last 14 days as columns, the success rate as a ring), the pipeline as five stages (uploaded, waiting,
being read, read, failed), the KPI cards, Helm's card, Recent Activity and the document types as a
donut with matching bars. Two read-only fields were added to the overview API for it (documents read,
and a count for each of the last 14 days), so nothing on the page is invented. "Today" keeps the
existing UTC day boundary (the workspace time zone is not used yet; noted in the code as before).
