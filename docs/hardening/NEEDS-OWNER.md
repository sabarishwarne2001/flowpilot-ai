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

## N-009 — When should production run the lossy ARCH-40 "contract" migration?
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
