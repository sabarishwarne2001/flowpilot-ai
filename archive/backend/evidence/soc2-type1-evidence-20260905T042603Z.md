# FlowPilot AI — SOC 2 Type I evidence pack

- Generated: `2026-09-05T04:26:03.238190+00:00`
- Report type: **TYPE_I**
- GA migration head: `arch27_step3_revenue_share_ledger`
- Content digest: `sha256:2cd51f7b0e0315838957985d975ec7764bb9f674521c0ec85e1e64bd0e23cdec`

## Summary

| Satisfied | Exception | Indeterminate |
|---|---|---|
| 16 | 0 | 0 |

> Type I evidences design and presence at a point in time, not operating effectiveness over a period. INDETERMINATE findings are controls this generator could not observe from where it ran; they are NOT passes and must be evidenced separately before the report is signed.

## Findings

| Control | Criterion | Status | Detail | Source |
|---|---|---|---|---|
| Change control | CC8.1 | **SATISFIED** | deployed revision 06c55056f0ae | `git` |
| Verification gates | CC8.1 | **SATISFIED** | 64 static verification gates present in backend/scripts | `backend/scripts/verify_*.py` |
| Schema change control | CC8.1 | **SATISFIED** | 118 migrations; GA head is arch27_step3_revenue_share_ledger; ARCH-28 adds none (28-G8) | `backend/alembic/versions` |
| Encryption at rest | CC6.6 | **SATISFIED** | MultiFernet round-trip verified against 1 configured key(s); the probe decrypted under key index 0 (0 = current head, higher = a retired key still accepted for reads, which is the expected state mid-rotation) | `app/core/encryption.py` |
| Credential storage | CC6.6 | **SATISFIED** | password hashing schemes, in preference order: argon2, bcrypt | `app/core/security.py` |
| Audit chain continuity | CC7.2 | **SATISFIED** | 16 append-only / immutability triggers present and enabled across 15 tables | `pg_trigger.tgenabled` |
| Schema state | CC8.1 | **SATISFIED** | alembic head in the live database: ['arch27_step3_revenue_share_ledger'] | `alembic_version` |
| Tenant isolation | CC6.7 | **SATISFIED** | route-level isolation matrix generated from the live app object | `scripts/isolation_matrix.py` |
| SSO assertion handling | CC6.1 | **SATISFIED** | SAML XSW structural defence, issuer binding, bearer confirmation and signed request binding are active; encrypted assertions are refused by documented policy | `app/services/auth/saml_security.py` |
| Deprovisioning | CC6.2 | **SATISFIED** | SCIM provisioning and deprovisioning; the last OWNER cannot be deactivated (409) | `app.services.identity.scim_service` |
| Session policy | CC6.1 | **SATISFIED** | per-organization session lifetime and re-authentication policy | `app.services.identity.session_policy_service` |
| Data residency | P4.2 | **SATISFIED** | regional bucket routing; a write to a region with no configured bucket is refused rather than falling back | `app.services.compliance.residency_service` |
| Right to erasure | P4.2 | **SATISFIED** | subject erasure with an append-only record of what was erased | `app.services.compliance.erasure_service` |
| Disaster recovery | A1.2 | **SATISFIED** | ARCH-19 DR drill and the ARCH-28 live invoice canary are both present and runnable | `backend/scripts` |
| Read replica | A1.2 | **SATISFIED** | no distinct replica configured; reads fall back to the writer, which is a documented single-instance posture | `settings.sqlalchemy_replica_uri` |
| API deprecation policy | CC2.2 | **SATISFIED** | RFC 8594 policy active; 1 deprecated path prefix(es) advertised with Deprecation, Sunset and Link headers | `app/middleware/deprecation.py` |
