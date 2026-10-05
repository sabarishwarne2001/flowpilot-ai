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

## N-013 — Send me one real Dodo test webhook so a safety check can be fixed (F-033)
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

## N-022 — What should a locked-out sign-in answer? (Phase 4, F-096)
After repeated wrong passwords the account is refused from that address for a growing time. Today
the refusal looks exactly like a wrong password (401): an attacker learns nothing, but a real user
is not told to wait. An older test expects 429 "too many attempts, retry after N seconds".
**Decide:** keep the silent 401, or say "too many attempts" with the wait time.
**Default:** unchanged (silent 401).

## N-023 — Oversized avatars: refuse or shrink? (Phase 4, F-097)
A 5000×5000 picture is shrunk to 1024 px today; an older test expects a refusal. Giant "image bomb"
files are refused either way (F-088). **Default:** unchanged (shrink).

## N-024 — Public links under `/api/v1/public` (Phase 4, F-098)
Calendar-feed and document-request links are public on purpose but share the prefix the API-key
gateway test reserves. Moving them breaks links already sent to people. **Decide:** keep them and
let the test exempt token links, or move them with redirects. **Default:** unchanged.

## N-025 — Narrow the ARCH-05 lock invariant? (Phase 4, F-099)
The ARCH-05 verification test says only the owner-change helper may lock database rows; 14 later
features legitimately lock their own rows, so it has been red since. Proposal: keep its intent and
check only locks on organizations and members. It is a verification gate, so it is not changed
without you (N-010). **Default:** unchanged (stays red).
