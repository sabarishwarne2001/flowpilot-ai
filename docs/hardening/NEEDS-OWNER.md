# Decisions only the owner can make

Each item says what the choice is, why it matters, and what the campaign does
until you decide. Nothing here blocks work: the campaign continues with other
items.

## N-001 — Branch naming for this campaign
CLAUDE.md says to work on `hardening/<topic>` branches. The cloud session that
ran Phase 0 was given the branch `claude/new-session-uaku80` by the session
harness and may push only there. Phase 0 changes only `docs/hardening/`, so
its work sits on that branch.
**Default until you decide:** each phase pushes to the branch the session
allows, and the PR title names the intended `hardening/<topic>`. If you want
real `hardening/*` branches, start the next session with that branch name, or
rename the branch on GitHub.

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

## N-004 — License for the repository
`LICENSE` is empty. A public repository with no license is "all rights
reserved" by default. That protects you, but it can confuse contributors and
enterprise buyers' legal review.
**Decide:** keep the repo private or proprietary, or pick a license. This is
not something the campaign should choose for you.

## N-005 — Rewrite git history to drop `backend/stripe.exe`?
The 38 MB binary is deleted from the current tree but stays in history
(commits `d790047`, `1884891`). Removing it needs a history rewrite and a
force-push to `main`. Every existing clone and fork then has to re-clone.
**Decide:** rewrite (smaller, cleaner repo) or leave it (no disruption). The
campaign will not force-push `main` either way. It will only add ignore rules.

## N-006 — Who may mint API keys and manage webhooks: OWNER only, or ADMIN too?
The sidebar shows API keys, Webhooks, Audit log and Enterprise identity only
to OWNER. The API lets ADMIN create, rotate and delete API keys, manage webhook
endpoints and export the audit log. (F-015)
**Decide:** which is the intended rule? The campaign will then make the UI and
API match, with tests.

## N-007 — Where will production run?
Sweepers, retention and backups run from host cron (`backend/deploy/cron.d`).
The production compose file does not run cron (F-006).
**Decide:** a single VM with Docker Compose plus host cron, or a managed
platform (Render, Fly.io, Railway, Kubernetes)? The answer decides how Phase 2
fixes scheduling and backups.
