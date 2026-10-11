# Hand-over between the four pre-launch campaign sessions

The final pre-launch campaign is split into four Claude Code sessions that run one after another,
each owning one slice of the product: **session 1** tenancy, roles, plans, seats, billing and usage
limits (the money and access model); **session 2** documents, AI and every engine, the whole
document-to-decision pipeline and every background job; **session 3** the organization, identity
and governance consoles, settings truthfulness and the security red team; **session 4** the
product shell and experience, performance, operations, end-to-end customer journeys and launch
certification. A session that finds a defect in another session's slice fixes it when that takes
under about 30 minutes; otherwise it writes it here, under the session that owns it, with enough
detail to reproduce. Each session appends a section "Session N → later sessions" when it finishes:
what it changed that later sessions must build on (limits, capability keys, metered keys,
migrations, renamed routes, shared components), defects it saw in their slices, and what in its
own slice is unfinished, with why.

## For session 2 (documents, AI, engines, background jobs)

_Nothing yet._

## For session 3 (organization, identity and governance consoles, security)

_Nothing yet._

## For session 4 (shell, experience, performance, operations, journeys, certification)

_Nothing yet._

## Session 1 → later sessions

Session 1 (tenancy, roles, plans, seats, billing, usage limits) fixed F-236 to F-255 and took the
product decisions N-040 to N-051 (NEEDS-OWNER.md, each marked `DECIDED (campaign session 1)`).
The generated source of truth for who a customer is, what each role and plan allows and how every
request resolves its organization and plan is `docs/hardening/TENANCY-AND-PLANS.md`
(regenerate with `python backend/scripts/generate_tenancy_doc.py`; a test fails when it drifts).

**Build on these (all sessions):**
- **Plans.** Free is deliberately small and charged at acceptance: 25 `document.upload`, 50
  `ocr.page` (reserved at upload, including documents still waiting for OCR), 30
  `assistant.message` a month, 10 MB files, 10 pages a document, 250 MB stored, one workspace,
  two seats, no API keys, webhooks, automations, batch or Business engines. One Free allowance per
  owner account (`quota_service.usage_pool`). Paid allowances are **per seat** (multiplied by the
  live subscription's seats). Plan limits are `limit.*` rows (`app/core/plan_limits.py`).
- **One admission point.** Every way a document enters goes through
  `app/services/plan_admission.py` (`admit_document`, `assert_reprocess_admitted`,
  `assert_workspace_available`, `admit_assistant_message`). A new intake path must call it; a
  refusal is a machine-readable 402 (`SPEND_LIMIT_EXCEEDED` / `PLAN_LIMIT_EXCEEDED` with `reason`,
  `limit_key`, `remedy`, `resets_at`).
- **New capability `capability.automations`** (Developer and up). The worker's automation handler
  and marketplace installs check it; a new automation entry point must too.
- **Seats.** Every way a person joins checks `seat_capacity_service` under the per-organization
  seat lock (take it before any invitation row lock). Owner, Admin and Billing buy seats
  (`PUT /organizations/{id}/billing/seats`, price shown first).
- **Worker gate.** `app/workers/tenant_gate.py` skips tenant-activity jobs for archived or
  suspended organizations and, now, for organizations read-only for non-payment
  (`BILLING_GATED_JOB_TYPES`). A new tenant job type belongs in `TENANT_ACTIVITY_JOB_TYPES`.
- **"Ask someone" names people.** `AskPlanOwners` / `useAskPlanOwners` (frontend) read the plan
  owners from the member-readable billing summary; use them instead of "ask an administrator".
- **Migrations.** Head is `s1b1_calendar_feed_org_member` (after `s1a1_upload_session_part_sizes`).
- **Seeds.** `seed_quota_tiers.py` and `seed_price_book.py --version auto` must be re-run on deploy
  (new tiers, the automations capability, Gemini overage rates); existing paid subscribers keep the
  version they bought (`--carry-forward` moves only to equal-or-better).

**For session 2 (documents, AI, engines, jobs):**
- Any new job type that does work for one tenant: add it to `TENANT_ACTIVITY_JOB_TYPES` (and it is
  then billing-gated too unless it is an export).
- Re-processing and packet splits already go through plan admission; a new engine that runs OCR or
  stores bytes must reserve through `plan_admission` rather than meter after the fact.
- Free-plan e2e tenant P is on Free: browser tests there only exercise refusals, never spend its
  allowance.

**For session 3 (consoles, identity, security):**
- `NoAccess` ("Contact an organization owner…") and `AccessRestricted` (`askWho` role names) still
  name roles, not people; `useAskPlanOwners` can supply names where the viewer is a member.
- Review-assignment and obligation ownership still require an explicit workspace grant (org
  owners/admins without one cannot be assigned); deliberate, but worth a product look.
- Rate limits are per IP and per API key; there is no per-tenant request limit for browser
  sessions (cost is bounded by the quotas). Decide whether one is needed.

**For session 4 (journeys, operations, certification):**
- **Owner actions before launch:** enable plan switching in the Stripe customer portal (N-050);
  publish yearly plan prices (N-051); re-run the seeds on deploy.
- Refunds are not modelled: a Stripe `charge.refunded` is recorded as "credit note not modelled",
  so invoices stay "paid" and RevOps revenue does not subtract refunds (Dodo, as merchant of
  record, owns refunds). Unverified how far this skews the revenue dashboard; needs a decision.
- The admin RevOps / COGS endpoints are proven to refuse tenants; their operator behaviour was not
  exercised end to end in session 1.

**Unfinished in session 1, and why:**
- Plan changes for paying customers go through the gateway's own portal (N-050); an in-app
  "change plan" that modifies the live subscription was not built, because it could not be
  verified without live gateway credentials.
- A second gateway subscription that somehow arrives anyway (e.g. completed from an old checkout
  tab opened before the first) still fails to record (one live subscription per account) and
  dead-letters; it is not refunded automatically. Rare after F-253; an alert and refund playbook
  would close it.
