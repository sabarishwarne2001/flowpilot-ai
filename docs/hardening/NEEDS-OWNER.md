# Decisions only the owner can make

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
