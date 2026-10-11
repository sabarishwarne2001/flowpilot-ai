# Tenancy, roles and plans — the source of truth

_Generated from the code by `backend/scripts/generate_tenancy_doc.py`. Do not edit by hand: change the code, run the script, commit both. `tests/scripts/test_tenancy_doc_is_current.py` fails when the two drift._

## 1. Who is the customer

The **organization** is the customer. It holds **one** subscription: a plan and, on a paid plan, a number of seats. Every person in the organization occupies **one seat**; members never pay and never need a plan of their own. A person can belong to several organizations: inside each, what they may do comes from **that** organization's plan and their role **there**, so switching organization switches plan and role. Workspaces live inside an organization and share its plan and its usage allowance.

## 2. Entities and how they relate

| Entity | Table | What it is |
|---|---|---|
| Account | `users` | A person's login. Owns nothing commercial by itself. |
| Organization | `organizations` | The customer. Points at its plan (`quota_tier_id`). |
| Organization membership | `organization_members` | A person in an organization, with one role (Owner, Admin, Billing, Member). An ACTIVE membership is a seat. |
| Workspace | `workspaces` | A space for documents inside one organization. |
| Workspace grant | `workspace_members` | A member's role in one workspace (Admin, Contributor, Viewer). |
| Invitation | `organization_invitations` | A pending offer of a membership. It holds the seat its acceptance will take. |
| Seat | `billable_seats` (view) | Active memberships. Capacity: the plan's `limit.seats` on Free, `subscriptions.seats_purchased` on a paid plan (`app.services.seat_capacity_service.seat_capacity`). |
| Billing account | `billing_accounts` | The organization's customer record at the gateway. |
| Subscription | `subscriptions` | The live gateway subscription: plan version, price book and seats purchased. |
| Plan version | `quota_tiers` + `quota_tier_entries` | An immutable published version of a plan. A live subscription pins the version it was sold (grandfathering); without one the organization follows its plan's newest version. |
| Price book | `price_books` + `price_book_entries` | Unit prices and cost bases for every meter, seats and overage. Subscriptions pin one. |
| Entitlement / capability | rows in `quota_tier_entries` | `capability.*`, `addon.*`, `llm.platform_key`: granted by the row's presence. |
| Quota entry | rows in `quota_tier_entries` | A meter's monthly ceiling and overage policy, or a static `limit.*`. |
| Add-on | `organization_addons` | A purchased extra (custom domain, warehouse sync). |
| API key | `api_keys` | Acts for the organization with the scopes it was given; its creator must remain a member. |
| Partner tenancy | `partner_organizations` | A reseller's book of organizations. Partner staff are not members and take no seats. |

## 3. Roles

### 3.1 Organization roles

| Organization action | Owner | Admin | Billing | Member |
|---|---|---|---|---|
| See plan, invoices, usage | yes | yes | yes | — |
| Change plan, payment method, cancel | yes | — | — | — |
| Buy or release seats | yes | yes | yes | — |
| Rename the organization, branding | yes | yes | — | — |
| Invite people | yes | yes | — | — |
| Change roles, remove members | yes | yes | — | — |
| Create a workspace | yes | yes | — | — |
| Archive or delete a workspace | yes | yes | — | — |
| Read the audit log | yes | yes | — | — |
| Create API keys | yes | yes | — | — |
| Manage outgoing webhooks | yes | yes | — | — |
| Configure single sign-on | yes | — | — | — |
| Set the security policy | yes | — | — | — |
| Transfer ownership | yes | — | — | — |
| Delete (archive) the organization | yes | — | — | — |

### 3.2 Who may give which role

Nobody can give a role above what they may give, nobody can raise themselves, the last active owner cannot leave or be demoted, and an owner is moved only by the two-party ownership transfer.

| Actor | May invite or assign the role | May change or remove a member who is |
|---|---|---|
| Owner | Owner, Admin, Billing, Member | Admin, Billing, Member |
| Admin | Billing, Member | Billing, Member |
| Billing | nobody | nobody |
| Member | nobody | nobody |

### 3.3 Effective role in a workspace

Owners and admins of the organization are admins of every workspace. Billing managers and members get exactly their workspace grant, and without one they see nothing in that workspace (it answers 404, so its existence is not revealed). Computed by `app.core.workspace_permissions.resolve_effective_workspace_role`.

| Organization role | No workspace grant | Viewer grant | Contributor grant | Admin grant |
|---|---|---|---|---|
| Owner | Admin | Admin | Admin | Admin |
| Admin | Admin | Admin | Admin | Admin |
| Billing | no access (404) | Viewer | Contributor | Admin |
| Member | no access (404) | Viewer | Contributor | Admin |

### 3.4 Workspace roles

| Workspace action (effective role) | Admin | Contributor | Viewer |
|---|---|---|---|
| Read documents, results, conversations | yes | yes | yes |
| Upload, create, edit own work | yes | yes | — |
| Edit anyone's work, delete documents | yes | — | — |
| Use the assistant | yes | yes | — |
| Manage automations | yes | — | — |
| Export data | yes | — | — |
| Change workspace settings | yes | — | — |
| Manage the workspace team | yes | — | — |
| Invite to the workspace | yes | — | — |

## 4. Plans

### 4.1 Prices

| Plan | Price |
|---|---|
| Free | $0 |
| Developer | $49 per seat per month |
| Business | $299 per seat per month |
| Enterprise | $799 per seat per month |

### 4.2 Monthly allowances

Free is per organization, and **one Free allowance is shared by every Free organization an account owns** (archived ones included): `app.services.quota_service.usage_pool`. A paid plan's allowances are **per seat, pooled across the organization**: the figure times the seats the subscription holds (`app.services.quota_service.seat_factor`). Documents, OCR pages, assistant messages and storage are what a customer plans by; the token and cost ceilings are the platform's safety net above them. Overage: *refuse* stops at the ceiling, *bill overage* continues and bills each unit above it at the price book's overage price, *warn* continues free of charge and the usage screens show the overrun.

| Per month | Free | Developer | Business | Enterprise |
|---|---|---|---|---|
| Document uploads | 25 (refuse) | 500 / seat (refuse) | 5,000 / seat (warn) | 10,000 / seat (warn) |
| OCR pages | 50 (refuse) | 5,000 / seat (bill overage) | 50,000 / seat (bill overage) | 100,000 / seat (bill overage) |
| Assistant messages | 30 (refuse) | 1,000 / seat (refuse) | 10,000 / seat (warn) | no ceiling |
| Storage (GB, a hard ceiling only where REFUSE) | 0.25 (refuse) | 25 / seat (bill overage) | 250 / seat (bill overage) | 1,000 / seat (bill overage) |
| AI input tokens (safety net) | 300,000 (refuse) | 15,000,000 / seat (refuse) | 150,000,000 / seat (bill overage) | 1,000,000,000 / seat (bill overage) |
| AI output tokens (safety net) | 60,000 (refuse) | 2,000,000 / seat (refuse) | 20,000,000 / seat (refuse) | 250,000,000 / seat (bill overage) |
| Usage cost ceiling, USD (safety net) | $1 (refuse) | $25 / seat (refuse) | $500 / seat (refuse) | $10,000 / seat (warn) |

### 4.3 Per-organization limits

| Per organization | Free | Developer | Business | Enterprise |
|---|---|---|---|---|
| Seats | 2 | purchased seats | purchased seats | purchased seats |
| Workspaces | 1 | 3 | 20 | unlimited |
| File size, MB | 10 | 50 | 100 | 100 (platform maximum) |
| Pages per document | 10 | 100 | 500 | 500 (platform maximum) |

### 4.4 Capabilities

| Capability | Free | Developer | Business | Enterprise |
|---|---|---|---|---|
| Forensic audit radar (`capability.anomaly_radar`) | — | — | yes | yes |
| Batch operations and integrity-verified export packages (`capability.batch_dispatch`) | — | — | yes | yes |
| Bring your own AI key (BYOK) (`capability.byok`) | — | — | yes | yes |
| Calibrated autonomy (`capability.calibrated_autonomy`) | — | — | — | yes |
| Case intelligence and the packet dicer (`capability.case_intelligence`) | — | — | yes | yes |
| Real-time collaborative review (`capability.collaborative_review`) | — | — | — | yes |
| Custom branding (`capability.custom_branding`) | — | yes | yes | yes |
| Custom email (`capability.custom_email`) | — | — | yes | yes |
| The developer API (`capability.developer_api`) | — | yes | yes | yes |
| Egress lockdown (`capability.egress_lockdown`) | — | — | — | yes |
| Enterprise single sign-on and SCIM (`capability.enterprise_identity`) | — | — | — | yes |
| The entity graph (`capability.entity_graph`) | — | — | yes | yes |
| ERP posting (`capability.erp_posting`) | — | — | yes | yes |
| Extraction memory (`capability.extraction_memory`) | — | — | yes | yes |
| Obligations and calendar feeds (`capability.obligations`) | — | — | yes | yes |
| Outgoing webhooks (`capability.outgoing_webhooks`) | — | yes | yes | yes |
| The priority 99.9% SLO (`capability.priority_slo`) | — | — | — | yes |
| Process intelligence and the exception agent (`capability.process_intelligence`) | — | — | — | yes |
| Procurement matching (`capability.reconciliation`) | — | — | yes | yes |
| Document redaction (`capability.redaction`) | — | — | — | yes |
| Clause assertions (`capability.semantic_assertions`) | — | — | — | yes |
| Table intelligence (`capability.table_intelligence`) | — | — | yes | yes |
| TruthMesh: the cross-document digital twin (`capability.truthmesh`) | — | — | yes | yes |
| The document corroborator (`capability.universal_corroborator`) | — | — | — | yes |
| AI on the platform's provider account (`llm.platform_key`) | yes | yes | yes | yes |

### 4.5 Add-ons

| Add-on | Free | Developer | Business | Enterprise |
|---|---|---|---|---|
| Custom domains (`addon.custom_domain`) | — | included | included | included |
| Warehouse sync (`addon.warehouse_sync`) | — | $300/month | included | included |

## 5. How a request is resolved: tenant, then role, then plan, then quota

| Caller | Tenant | Role | Plan and quota |
|---|---|---|---|
| Browser | the organization and workspace in the URL; a verified email is required (`app.api.deps.get_verified_user`) | the membership and grant, resolved on every request (no cached role) | the live subscription's pinned plan, else the organization's plan (`app.services.quota_service.resolve_tier`); capabilities by `app.api.capability_gate.require_capability`; meters by `app.services.spend_control_service.ensure_within_limits`; uploads, workspaces and assistant messages by `app.services.plan_admission.admit_document`, `app.services.plan_admission.assert_workspace_available` and `app.services.plan_admission.admit_assistant_message` |
| API key | the key's organization | the key's scopes, while its creator is a member | the same plan; the Developer API capability is required to use a key at all |
| Worker | the job's organization | a system principal | the same meters checked again before the work is charged (OCR pages, tokens); a document is charged when accepted, so a job never starts work the plan cannot pay for |
| Public document-request link | the request's organization | none (the link is the permission) | the same intake and the same document charge; a refusal tells the uploader only to contact the sender |
| SCIM | the token's organization | the directory's mapped role | the organization's seats (`app.services.seat_capacity_service.assert_seat_available`); past them, a SCIM 403 |
| Single sign-on (JIT) | the identity provider's organization | the mapped role | the organization's seats, then the provider's own cap if CAPPED |

## 6. Seats

One check bounds every way in (invitation issue, resend and accept; SSO and SCIM provisioning; SCIM reactivation): `app.services.seat_capacity_service.assert_seat_available`. Used seats are active members plus pending invitations. Free holds the seats its plan declares. A paid plan holds the seats its subscription bought; owners, admins and billing managers buy more (or release unused ones) with `app.services.billing.seat_service.set_purchased_seats` after the price is shown (`GET .../billing/price-book/seat`), and a purchase at any other price is refused. Removing a member frees the seat for someone else; the subscription keeps it until it is released (the gateway credits the rest of the period). Checkout cannot sell fewer seats than are in use.
