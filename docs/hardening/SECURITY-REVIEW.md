# FlowPilot AI — security review (pre-launch campaign, session 3)

_Date: 2026-10-12. Scope: the whole product as it stands on branch `claude/determined-planck-e5ftrx`
(pull request #22). Standard: OWASP ASVS 4.0 Level 2, plus the multi-tenant checks a B2B SaaS
buyer's security questionnaire asks for._

## In plain words

FlowPilot was attacked the way a customer's security team, a curious member or a hostile tenant
would: reading another organization's data by swapping IDs, climbing from a low role to a high one,
using API keys and directory tokens where they should not work, pointing integrations at the
platform's own network, uploading files that carry script, injecting markup into emails, forging
sign-in assertions, and checking that every control the consoles show actually does something.

**Twenty defects were found and fixed in this session** (F-276 to F-295 below), each proved by a
test that failed before the fix and passes after it. The most serious: the "Require single sign-on"
switch was shown as in force and enforced nowhere (F-276); the IP allowlist was stored and never
read (F-277); changes to enterprise sign-in never reached the audit log (F-294). Nothing found let
one organization read or change another organization's data: the cross-tenant sweeps found no
leak.

**What is left for you** (none blocks launch): an independent penetration test before the first
enterprise contract; confirm `TRUSTED_PROXY_HOPS` on the production load balancer
(`TRUSTED_PROXY_HOPS_CONFIRMED=true`) so IP allowlists can be switched on; a build-tool advisory
(`braces`, through Tailwind 3) that has no fix yet and never reaches the shipped app.

## How it was tested

* **The real request path.** Every finding was reproduced through the HTTP API (FastAPI's test
  client with real sessions and a real database, or the live stack: API, worker, Postgres, Redis,
  mail sink and the production bundle under the production Content-Security-Policy).
* **Sweeps over every operation.** `docs/hardening/tools/live_idor_sweep.py` harvests every ID one
  organization can see and replays every one of the API's operations against them as someone else.
* **Evidence rule.** A defect counts as fixed only when a test proved it, the fix went in, that test
  passed and the full suite stayed green.

## Defects found and fixed in this session

| ID | Severity | What was wrong | Proof |
|---|---|---|---|
| F-276 | Critical | "Require single sign-on" enforced nowhere; a password session kept full access | `backend/tests/engines/test_require_sso_is_enforced_live.py` |
| F-277 | High | The IP allowlist was stored and never read | `backend/tests/engines/test_ip_allowlist_is_enforced_live.py` |
| F-278 | High | The live review WebSocket skipped the sign-in policy | `backend/tests/engines/test_live_review_obeys_the_sign_in_policy_live.py` |
| F-279 | High | A SCIM token kept provisioning after the organization left Enterprise | `backend/tests/engines/test_scim_directory_live.py` |
| F-280 | Medium | SCIM PATCH reactivation skipped the read-only billing gate | same file |
| F-281 | Medium | An archived organization's SCIM token still added people | same file |
| F-282 | High | SSO just-in-time provisioning into a downgraded or archived organization | `backend/tests/engines/test_saml_journey_live.py` |
| F-283 | Low | Anything base64 was accepted as an IdP signing certificate | `backend/tests/engines/test_identity_console_setup_live.py` |
| F-124 | Medium | SCIM token hashes keyed with a public constant | `backend/tests/services/test_scim_token_key_is_secret.py` |
| F-284 | Medium | A partner's standing was self-declared and read by nothing | `backend/tests/api/test_partner_status_belongs_to_the_platform.py` |
| F-285 | Medium | Markup in an organization or brand name reached emails; subjects had no line-break guard | `backend/tests/security/test_email_templates_escape_every_value.py` |
| F-286 | Medium | Browser traffic was rate limited per office address, not per person | `backend/tests/core/test_signed_in_requests_are_limited_per_person.py` |
| F-287 | High | Billing and security notices were never emailed | `backend/tests/services/test_account_notices_are_emailed.py` |
| F-290 | Low | Recipients' addresses written to the log on every notification email | covered by the delivery tests |
| F-291 | Medium | The "every connection passes the egress gate" rule was no longer enforced in CI | `backend/tests/security/test_every_outbound_connection_passes_the_egress_gate.py` |
| F-292 | High | S3 and Snowflake warehouse connectors never received the access key ID (found by the SSRF probe) | `backend/tests/services/test_warehouse_key_ids_reach_the_connector.py` |
| F-293 | Low | The SSO return path used a looser copy of the redirect check | `backend/tests/security/test_sso_return_path_has_one_strict_check.py` |
| F-294 | High | 23 identity audit writes used names the audit log did not have and were dropped | `backend/tests/security/test_audit_vocabulary_covers_every_writer.py` |
| F-295 | Medium | Webhook endpoints changed without an audit row | `backend/tests/engines/test_sensitive_actions_are_audited_live.py` |

Full write-ups: `docs/hardening/FINDINGS.md`, "Pre-launch campaign, session 3". (F-288 and F-289 in
the same section are billing/usage defects, not security.)

## Cross-tenant access (IDOR) and privilege escalation

Run against the live stack with three organizations: C (Enterprise, the victim), B (Business) and X
(an Enterprise organization created for the attack). Every ID visible to C's owner (121 to 141 IDs
under 36 to 38 names) was replayed in every path, query and body position of the API's operations.

| Sweep | Requests | Result |
|---|---|---|
| Reads as B's owner, C's IDs | 353 | 0 leaks (326 × 404, others 401/402/422; 8 × 200 were the attacker's own data) |
| Reads as X's owner (Enterprise, every feature on) | 353 | 0 leaks |
| Writes as X's owner | 832 | 0 changes to C (777 × 404; the 2 "suspects" were X creating its own organization and acting on its own review item) |
| Every operation with X's API key | 832 | 0 leaks (789 × 403: keys reach only the public API) |
| A member of C without a grant to C's restricted workspace | 832 | 0 reads of that workspace (the "suspects" were the member's own profile, the organization directory every member may read, and their own workspaces) |
| Privilege: a viewer, a Billing member | 832 each | No write above the role; reads were of workspaces the person is granted |

Non-human principals (SCIM tokens, the public gateway's key, partner members) reach only their own
routes: `backend/tests/security/test_non_human_principals_default_deny.py`. The pre-existing
`test_cross_tenant_sweep.py`, `test_role_matrix.py`, `test_idor_child_entities.py` and the isolation
suite (`backend/tests/isolation/`) remain green.

## ASVS 4.0 Level 2, chapter by chapter

| Chapter | Verdict | Evidence and notes |
|---|---|---|
| V1 Architecture | Pass | One enforcement point for tenancy (the context dependencies in `app/api/deps.py`), one for outbound connections (`app/core/egress.py`, now enforced by F-291's test), one for redirects (`app/core/redirects.py`, F-293). |
| V2 Authentication | Pass, after fixes | Argon2 passwords and a policy (`test_password_policy.py`); lockout and backoff (`test_login_backoff.py`, `test_sec1_gate_3_lockout.py`); TOTP two-factor and recovery codes; organizations can require two-factor (N-060) and SSO (F-276); SAML signature, XSW, replay and audience checks (`test_saml_xsw_rig.py`, `test_saml_journey_live.py`); OIDC PKCE, nonce and issuer checks (`test_oidc_journey_live.py`). |
| V3 Session management | Pass | Short access tokens, rotating refresh cookie with reuse detection that ends the family (`test_auth_endpoints.py`, `test_concurrent_refresh_f126.py`); logout and logout-everywhere end tokens in flight; password reset ends every session; organization maximum session age enforced (F-204). Refresh cookie: `HttpOnly`, `SameSite=Lax`, `Secure` outside development, path-scoped to `/api/v1/auth`. |
| V4 Access control | Pass | The sweeps above; plan gates server-side (`test_plan_gating_server_side.py`); IP allowlist (F-277); partner standing (F-284); SCIM and SSO after a downgrade (F-279 to F-282). |
| V5 Validation, sanitisation, encoding | Pass, after fixes | Email templates escape every value (F-285); CSV exports neutralise formulas; uploads: SVG and HTML-polyglot images refused or re-encoded (`test_image_assets_are_reencoded.py`, `test_upload_attacks.py`); NUL bytes refused (`test_nul_byte_guard.py`); injection sweep (`test_injection_sweep.py`); validation errors never echo input. |
| V6 Stored cryptography | Pass | Secrets encrypted with rotating keys (`EMAIL_ENCRYPTION_KEYS`, MultiFernet); API keys and SCIM tokens stored as keyed hashes (F-124); git history scanned: every match is a test fixture or placeholder (`docs/hardening/tools/scan_history_for_secrets.py`, 56 groups reviewed). |
| V7 Errors and logging | Pass, after fixes | Append-only audit log enforced by a database trigger (`test_team_and_audit_live.py`); every sensitive change now audited (F-294, F-295; 24 actions in `test_sensitive_actions_are_audited_live.py`); no email addresses in delivery logs (F-290); refusals give the same body whatever the reason (`test_auth_token_attacks.py`). |
| V8 Data protection | Pass | Erasure and export (DSAR) audited; residency pinning; the assistant's output filter applies to notification emails too (`test_arch12_notifications.py`); canary isolation of retrieval (session 2). |
| V9 Communications | Pass | HTTPS only in production (`production_guard.py` refuses non-https origins and wildcard CORS); HSTS with preload, `nosniff`, a strict CSP (`script-src 'self'`, `frame-ancestors 'none'`) in `backend/deploy/Caddyfile`; the browser suite runs under that exact policy. |
| V10 Malicious code | Pass | Dependencies pinned; `pip-audit`: no known vulnerabilities; `npm audit`: none in shipped code (see below). |
| V11 Business logic | Pass | Seats, plans, trial and spend limits enforced server-side (session 1); per-person rate limits (F-286); usage metered exactly once (session 2). |
| V12 Files and resources | Pass, after fixes | SSRF: every customer-chosen destination refused for loopback, metadata, private and IPv6 forms (`test_ssrf_hostile_destinations.py`, `test_tenant_supplied_endpoints.py`, and ERP/warehouse in `test_tenant_destinations_cannot_reach_the_platform_live.py`); no socket opened. |
| V13 API | Pass | API keys are scoped and reach only the public API (789 × 403 above); webhooks are signed with rotating secrets; OpenAPI docs are not public in production (`test_api_docs_not_public_in_production.py`). |
| V14 Configuration | Pass | `production_guard.py` refuses to start with development secrets, wildcard CORS, http origins or an unconfirmed proxy hop count for IP features. |

## Account enumeration

Login, forgot-password and sign-up answer identically for an unknown address and a taken one
(`test_regression_matrix.py::test_login_does_not_distinguish_unknown_account_from_wrong_password`,
`test_password.py::test_forgot_password_is_identical_for_unknown_addresses`,
`test_regression_matrix.py::test_registration_is_indistinguishable_for_a_taken_address`; a taken
address receives an "account exists" email instead). Login has a minimum duration so timing does not
tell either. Single sign-on discovery says whether an email **domain** uses SSO, as Okta and Slack
do: it must, to send the person to their identity provider.

## Tokens

Every emailed link (verification, password reset, email change, invitation) is single-use, expires,
is invalidated by the next one of its kind, carries its token in the URL fragment (never sent to a
server or written to a log) and cannot be used as another kind (`test_password.py`,
`test_verification.py`, `test_email_change_service.py`, `test_auth_token_service.py`). JWTs:
`alg=none`, other secrets, tampering, wrong type, missing expiry and revoked users are refused
(`test_auth_token_attacks.py`).

## Dependencies

* **Python** (`pip-audit -r backend/requirements.txt`, 2026-10-12): no known vulnerabilities.
* **JavaScript** (`npm audit`): **0 in the shipped application** (`--omit=dev`). Build-time tooling
  has 7 (5 high, 2 moderate): `braces` (every version; no fix published) and
  `postcss-selector-parser`, both reached only through Tailwind CSS 3's file watcher and glob
  matcher while building. They cannot run in a browser or on the server. The fix is upgrading to
  Tailwind 4 (a breaking change to the styling build); handed to session 4. CI's `npm audit` step
  is advisory (`continue-on-error`), so it reports these without failing.

## Residual risks and recommendations

1. **Independent penetration test** before the first enterprise contract (customers will ask for
   the report). This review is thorough but it is not independent.
2. **Proxy hop count.** IP allowlists and IP pinning stay unavailable until
   `TRUSTED_PROXY_HOPS_CONFIRMED=true` is set for the production load balancer; the console says so.
3. **Tailwind 4 upgrade** to clear the build-tool advisories (session 4).
4. **Login rate limits per account** exist (lockout and backoff); per-organization request
   ceilings were decided against (N-061).
