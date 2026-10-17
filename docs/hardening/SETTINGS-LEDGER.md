# Settings ledger (campaign session 3)

Every setting a person can change, who reads it, what it changes, and the test that proves the
change. A setting that is saved but changes nothing is a broken promise, so each row ends in one of:

* **Proven**: a test shows the effect (not just that the value saves).
* **Reader, unproven**: code reads it to change behaviour, but no test shows the effect yet
  (CLAUDE.md evidence rule: unverified).
* **Fixed (F-nnn)**: it was dead or the page said more than it did; fixed this session, with the
  test named in FINDINGS.md.
* **Shown, not editable**: a fact the page displays.

Paths are relative to the repository root. "BE" is `backend/app`, "FE" is `frontend/src`.

## Who decides what (precedence)

| What | Decided by | Decision |
|---|---|---|
| How a moment reads (signed in at, uploaded at) | The person's time zone, else the workspace's | N-064 |
| How a calendar date on a document reads | The workspace's date format, for everyone in it | N-064 |
| Numbers and amounts | The person's language; money keeps its own currency | N-064 |
| Exports, webhooks, calendar feeds | Always ISO 8601 | N-064 |
| Light or dark | The person (the organization's scheme applies only to its sign-in pages) | N-065 |
| Brand colours | The organization, on its verified domains only | N-065 |
| Which emails a person gets | The person, except security and billing notices (always sent) | N-062 |
| Two-factor sign-in | The person, unless the organization requires it | N-060 |

The display layer is one module on each side: `FE/utils/displayTime.ts` (checked by
`npm run check:self`, which also fails on a new ad-hoc `toLocale*`/`Intl` call) and
`BE/core/display_format.py`.

## The person (Settings > Profile, Notifications, Security)

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Display name | Audit entries, invitation and ownership mail, member lists | `backend/tests/api/test_profile.py`; access screens name people (`e2e/tests/41-notifications.spec.ts`) | Proven |
| Time zone (and "use my computer's") | FE display layer: every moment on screen | `e2e/tests/42-display-formats.spec.ts`; `npm run check:self` "display formatting" | Proven (F-298) |
| Language | FE display layer: numbers and amounts | `npm run check:self` "display formatting" | Proven (F-298) |
| Profile picture | Served to members; re-encoded on upload | `backend/tests/api/test_avatar_upload.py`, `tests/security/test_image_assets_are_reencoded.py` | Proven |
| Email notifications per category | `notification_preferences_service.should_email` before each email | `backend/tests/api/test_notification_preferences_api.py`, `e2e/tests/41-notifications.spec.ts` | Proven (N-062) |
| Two-factor sign-in | Sign-in asks for a code | `e2e/tests/30-auth.spec.ts`, `tests/engines/test_require_two_factor_live.py` | Proven |
| Password | Sign-in | `backend/tests/services/test_password.py` | Proven |
| Signed-in devices (revoke) | Ends that session | `backend/tests/services/test_auth_endpoints.py` | Proven |

## Workspace > General

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Name | Everywhere the workspace is named | saved and shown; no test of a rename reaching other screens | Reader, unproven |
| Time zone | Moments for people with no time zone of their own (display layer); scheduled exports; ERP posting dates | `npm run check:self` "display formatting" (the workspace clock when the person has none) | Proven on screen (F-298); exports and ERP unproven |
| Date format | Calendar dates on screen (obligations, documents), dates in automation messages, three-way matching's reading of document dates | `e2e/tests/42-display-formats.spec.ts`, `backend/tests/services/test_display_format.py` | Proven (F-298) |
| Currency | What three-way matching and assertions assume for an amount with no currency (never printed as a guessed symbol, F-172) | none of the assumption itself | Reader, unproven |
| Language | Not offered: the page says language is personal. The stored value is read only by ERP field mapping | n/a | Honest |
| Logo | The workspace's mark in the app; re-encoded on upload | `tests/security/test_image_assets_are_reencoded.py`, `e2e/tests/21-live-feedback.spec.ts` | Proven |

## Workspace > AI

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Provider, model | Which model answers and reads documents | `backend/tests/services/test_platform_served_models.py` | Proven |
| Temperature | Ask AI answers only (reading documents uses fixed low temperatures); the page says so | `e2e/tests/46-settings-truthfulness.spec.ts` (wording); `backend/tests/services/test_workspace_ai_settings_reach_the_provider.py` (reading calls never take it) | Fixed (F-307); the assistant's use unproven |
| Top P, maximum output length | Every model call for the workspace (`llm_service`) | `backend/tests/services/test_workspace_ai_settings_reach_the_provider.py` (classify, extract and summarise each send them) | Proven |
| Frequency and presence penalty | Groq models only; read-only with the reason when Gemini is chosen | `e2e/tests/46-settings-truthfulness.spec.ts` (wording); `backend/tests/services/test_workspace_ai_settings_reach_the_provider.py` (sent to Groq) | Fixed (F-307); proven |
| Streaming | Ask AI answers arrive whole when off | `tests/engines/test_assistant_stream_provider_errors_live.py` | Proven |

## Workspace > Documents

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Chunk size, chunk overlap | Chunking and re-indexing (`chunk_size_tokens`, `chunk_overlap_pct`) | `e2e/tests/42-display-formats.spec.ts`, `backend/tests/services/test_chunking.py` | Fixed (F-299) |
| Embedding model, OCR language | Platform facts, shown not edited | n/a | Shown, not editable |
| Allowed file types | Upload validation (narrowed to what the platform accepts) | `tests/engines/test_document_controls_take_effect_live.py` | Proven |
| Max upload size | Upload refused with 413 above it | `tests/engines/test_document_controls_take_effect_live.py` | Proven |
| Duplicate detection | Intake flags or skips a repeated file | `tests/engines/test_upload_duplicates_live.py` | Proven |
| Automatic classification | Classification runs or the document stays "Other" | `tests/engines/test_document_controls_take_effect_live.py` | Proven |
| Automatic summaries, automatic field extraction | Enrichment runs them or not | `tests/engines/test_document_settings_live.py` | Proven |
| Verification (agents) | Multi-agent extraction review | `tests/engines/test_document_settings_live.py`, `test_review_evidence_live.py` | Proven |

## Workspace > Email (the workspace's own mail server)

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Server, port, user, password, encryption, on/off | Workspace mail (automation emails, workspace notices) leaves through it | `backend/tests/api/test_organization_email_settings.py::test_workspace_row_wins_over_the_organization_row` | Proven |
| Sender name, from address | From line of that mail | same file | Proven |
| Reply-to | Reply-To of that mail (a message naming its own keeps it) | `backend/tests/services/test_workspace_reply_to_reaches_the_mail.py` | Fixed (F-306) |

Note: the old `email_settings` table has no API, page or reader left (its crud helpers remain);
dropping it is a cleanup for a later session (HANDOFF).

## Workspace > other policies

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Dispatch policy: confidence thresholds, required fields | Batch dispatch lanes (straight through, review, exception) | `tests/engines/test_batch_engine_live.py`, `tests/services/test_batch_healing_and_dispatch.py` | Proven |
| Dispatch policy: tag documents | Lane tags written to documents | on case only | Reader, partly proven |
| Extraction memory mode (auto, shadow, off) | Harvest, prompt hints, sweep | `tests/engines/test_extraction_memory_rules.py` (auto, shadow) | Proven except off |
| Process SLA: target hours | Breach deadline | `tests/engines/test_process_intel_live.py` | Proven |
| Process SLA: at-risk probability, alerts on | At-risk flag, alert | none | Reader, unproven |
| Agent policy: planning, auto-apply, hold minutes | The process agent's proposals and applying them | none | Reader, unproven |
| Procurement tolerance: price (bps) | Three-way matching | `tests/engines/test_three_way_matching_live.py` | Proven |
| Procurement tolerance: price (amount), quantity, pair cost, candidate window | Three-way matching | none | Reader, unproven |

## Organization > General

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Name | Everywhere the organization is named | `tests/engines/test_sensitive_actions_are_audited_live.py` | Proven |
| Identifier (slug) | Every URL; not changeable on the page (the page no longer promises a support path) | `e2e/tests/46-settings-truthfulness.spec.ts` | Fixed (F-308) |
| Legal name | Payment provider customer name when the customer is first created; compliance export. API only | none | Reader, unproven (later edits do not reach the provider: HANDOFF) |
| Seat limit | Invitations and seat capacity (set by the platform, not the organization) | `backend/tests/services/test_seat_capacity_every_path.py` | Proven |

## Organization > Branding and domains

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Brand name, logo, favicon, support address | Sign-in pages, title and icon on verified domains; branded mail | `backend/tests/api/test_arch25_endpoints.py`, `e2e/tests/26-phase3-consoles.spec.ts` | Proven |
| Primary, accent, background, text colours; colour scheme | Sign-in pages and the app on verified domains (N-065) | `e2e/tests/43-branding.spec.ts`, `npm run check:self` "brand colours" | Fixed (F-300) |
| Enable custom branding | Nothing a visitor sees changes until on | `backend/tests/api/test_arch25_endpoints.py` | Proven |
| Sender domain | Mail sent as the organization's domain once verified | `backend/tests/api/test_arch25_endpoints.py` | Proven |

## Organization > Email (the organization's mail server)

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Server and sender settings, on/off | Invitations and the optional notices; never password resets, email changes, security or billing notices (platform relay, N-062). The page says so | `backend/tests/api/test_organization_email_settings.py`, `e2e/tests/46-settings-truthfulness.spec.ts` | Fixed wording (F-305) |

## Organization > Compliance

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Data residency region | Where export archives and branding files are written; the card lists what it does not pin (N-066) | `backend/tests/api/test_residency_says_what_it_pins.py`, `e2e/tests/44-data-residency.spec.ts` | Fixed (F-301) |
| Document retention (days) | Blocks deleting younger documents; the purge removes older ones when auto-purge is on | `backend/tests/api/test_legal_hold_enforcement.py`, `tests/engines/test_retention_purge_releases_file_live.py` | Proven |
| Conversation retention (days) | The purge, when auto-purge is on | none | Reader, unproven |
| Audit log retention (days) | The nightly audit sweep, per organization; blank keeps the log (N-067) | `backend/tests/scripts/test_audit_retention_follows_the_policy.py` | Fixed (F-304) |
| Automatic purging | The compliance sweep acts only on organizations that opt in. Runs from the host cron (`deploy/cron.d/servabo-sweepers`), not the in-app scheduler | `tests/engines/test_retention_purge_releases_file_live.py` | Proven (needs the cron installed: HANDOFF) |
| Legal hold | Blocks deletion and purge | `backend/tests/api/test_legal_hold_enforcement.py` | Proven |

## Organization > Identity and security policy

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Require single sign-on | Password sessions refused for members | `tests/engines/test_require_sso_is_enforced_live.py` | Proven |
| Owner bypass | Owners may still use a password | `backend/tests/services/test_sso_organization_guard.py` | Proven |
| Require two-factor | Members without a second factor are stopped until they add one | `tests/engines/test_require_two_factor_live.py` | Proven (N-060) |
| IP allowlist | Requests from elsewhere refused | `tests/engines/test_ip_allowlist_is_enforced_live.py` | Proven |
| Maximum session age | Older sessions end | `tests/engines/test_session_age_policy_live.py` | Proven |
| IP pinning, IPv4/IPv6 prefixes | Shown unavailable on the page (honest). Through the API it would pin single sign-on sessions only | `tests/engines/test_security_policy_has_no_dead_controls_live.py` | Honest; HANDOFF |
| Role mappings, just-in-time provisioning | Role on single sign-on | `tests/engines/test_identity_console_setup_live.py`, `test_saml_journey_live.py` | Proven |

## Organization > Egress lockdown

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Lockdown on/off, allow rules | Every tenant channel (webhooks, warehouses, ERP, own SMTP, identity metadata, AI providers) is refused unless a rule allows it; platform channels unaffected | `tests/engines/test_egress_lockdown_is_enforced_live.py` | Proven |

## Organization > Webhooks

| Setting | Read by, and what it changes | Proof | Status |
|---|---|---|---|
| Endpoint URL, events, on/off | Deliveries | `tests/engines/test_webhooks_live.py` | Proven |
| Rotate secret | Both secrets sign during the overlap | `tests/engines/test_webhooks_live.py` | Proven |
| Redeliver | One more attempt, now | `tests/engines/test_webhooks_live.py`, `e2e/tests/20-organization.spec.ts` | Fixed (F-302) |

## Proven in campaign session 4 (formerly "still unproven")

Each setting below now has a test of its effect (backend/tests/…):

| Setting | Proven by | Result |
|---|---|---|
| Assistant temperature | `engines/test_assistant_temperature_reaches_the_model_live.py` | Reaches the model, streamed and plain |
| Dispatch tagging off | `engines/test_batch_engine_live.py` | Lanes recorded, nothing tagged; tags return when on (old tags stay: N-077) |
| Extraction memory off | `engines/test_extraction_memory_rules.py` | Nothing applied, hinted, learned or swept |
| Procurement amount, quantity, pair-cost and window tolerances | `engines/test_procurement_tolerances_take_effect_live.py` | Each moves the match exactly at its boundary |
| Conversation retention | `engines/test_conversation_retention_purge_live.py` | Purges only older conversations, only when on (counts from the start: N-077) |
| SLA at-risk probability and alerts | `engines/test_sla_risk_threshold_and_alerts_live.py` | Threshold decides "at risk"; the switch raises exactly one alert |
| Every agent policy field | `engines/test_process_agent_policy_takes_effect_live.py` | Planning, auto-apply, allowed kinds and undo hold each change what the agent does |
| Legal name after the payment customer exists | `api/test_legal_name_reaches_the_payment_customer.py` | **Was broken — fixed (F-365)** |

## Still unproven (for a later session)

None.
