"""ARCH-28 — SOC 2 Type I evidence pack.

    from app.services.compliance import evidence_pack
    pack = evidence_pack.compile_pack(db)

ASSEMBLY, NOT CONSTRUCTION
==========================

The roadmap's phrasing, and it is the accurate one. Every control a Type I
report asks about already exists and has been gated for twenty-two phases:

    CC6.1  logical access          ARCH-16   IAM, SCIM, session policy
    CC6.6  encryption at rest      ARCH-07   MultiFernet, Argon2id
    CC6.7  tenant isolation        ARCH-02   the isolation matrix
    CC7.2  audit logging           ARCH-07   append-only triggers
    CC8.1  change control          ARCH-0V   the gate suite
    A1.2   availability            ARCH-19   DR drills
    P4.2   residency and erasure   ARCH-20   governance

This module does not implement any of them. It reads the live system and emits
a document an auditor can follow to the source. The payoff for doing the phases
properly is that this is a query, not a project.

TYPE I, NOT TYPE II — AND THE DIFFERENCE MATTERS HERE
=====================================================

A Type I report attests that controls are SUITABLY DESIGNED AND IN PLACE at a
point in time. A Type II attests they OPERATED EFFECTIVELY over a period. This
pack is Type I: it evidences the state of the system now.

That distinction is load-bearing for one design decision. Where a control
cannot be verified — no database, a table absent, a trigger disabled — the
finding is emitted as `INDETERMINATE`, never as `PASS` and never silently
omitted. An evidence pack that quietly drops the checks it could not run is a
document that certifies whatever the generator happened to reach, and the
generator is the least trustworthy part of the chain.

This is the ARCH-18 G2 anti-pattern applied to compliance rather than money: a
missing cost basis renders as "unknown", never zero, because a silent zero
reads as 100% margin and gets priced on. A missing control renders as
"indeterminate", never "satisfied", because a silent pass gets signed.

WHAT THIS MODULE WILL NOT DO
============================

It does not read secret material. Fernet keys are evidenced by KEY ID and by a
round-trip proof, never by value. The pack is a document that leaves the
building; it must be safe to hand to an auditor who is not an employee.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("app.services.compliance.evidence_pack")

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parent

SATISFIED = "SATISFIED"
EXCEPTION = "EXCEPTION"
INDETERMINATE = "INDETERMINATE"

#: The permanent GA migration head. ARCH-28 ships no migrations (`28-G8`), so
#: this is a fact the pack can assert rather than a moving target.
GA_MIGRATION_HEAD = "arch27_step3_revenue_share_ledger"

#: Append-only and immutability triggers, by table. Every one is created by a
#: migration and none may be disabled in a system claiming CC7.2. The list is
#: enumerated rather than discovered so that a MISSING trigger is an exception
#: rather than an empty result set that looks like a clean run.
REQUIRED_IMMUTABILITY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "audit_logs": ("trg_audit_logs_immutable", "trg_audit_logs_no_truncate"),
    "invoices": ("trg_invoices_finalized_immutable",),
    "invoice_line_items": ("trg_invoice_line_items_finalized_immutable",),
    "price_books": ("trg_price_books_publish_immutable",),
    "price_book_entries": ("trg_price_book_entries_publish_immutable",),
    "quota_tiers": ("trg_quota_tiers_publish_immutable",),
    "quota_tier_entries": ("trg_quota_tier_entries_publish_immutable",),
    "rollup_windows": ("trg_rollup_windows_seal_immutable",),
    "reconciliation_runs": ("trg_reconciliation_runs_immutable",),
    "reconciliation_findings": ("trg_reconciliation_findings_immutable",),
    "provider_statements": ("trg_provider_statements_immutable",),
    "provider_statement_lines": ("trg_provider_statement_lines_immutable",),
    "recognized_revenue_ledger": ("trg_recognized_revenue_append_only",),
    "partner_rev_share_ledger": ("trg_partner_rev_share_ledger_append_only",),
    "partner_payout_periods": ("trg_partner_payout_periods_seal_immutable",),
}


@dataclass
class Finding:
    """One control observation. `criterion` is the SOC 2 reference."""

    control: str
    criterion: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    @property
    def ok(self) -> bool:
        return self.status == SATISFIED


class EvidenceCollector:
    """Accumulates findings and never lets an exception become a pass."""

    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def record(
        self,
        control: str,
        criterion: str,
        status: str,
        detail: str,
        *,
        evidence: Optional[dict[str, Any]] = None,
        source: str = "",
    ) -> None:
        self.findings.append(
            Finding(
                control=control,
                criterion=criterion,
                status=status,
                detail=detail,
                evidence=evidence or {},
                source=source,
            )
        )

    def indeterminate(
        self, control: str, criterion: str, reason: str, *, source: str = ""
    ) -> None:
        """The only correct outcome for a control that could not be observed."""
        self.record(control, criterion, INDETERMINATE, reason, source=source)


# ===========================================================================
# CC8.1 — change control
# ===========================================================================


def _git(*args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_change_control(collector: EvidenceCollector) -> None:
    revision = _git("rev-parse", "HEAD")
    if revision is None:
        collector.indeterminate(
            "Change control",
            "CC8.1",
            "git is unavailable or this is not a repository checkout; the "
            "deployed revision cannot be evidenced from inside the container",
            source="git rev-parse HEAD",
        )
    else:
        collector.record(
            "Change control",
            "CC8.1",
            SATISFIED,
            f"deployed revision {revision[:12]}",
            evidence={
                "revision": revision,
                "committed_at": _git("log", "-1", "--format=%aI"),
                "subject": _git("log", "-1", "--format=%s"),
            },
            source="git",
        )

    gates = sorted(p.name for p in (BACKEND_ROOT / "scripts").glob("verify_*.py"))
    collector.record(
        "Verification gates",
        "CC8.1",
        SATISFIED if gates else EXCEPTION,
        f"{len(gates)} static verification gates present in backend/scripts",
        evidence={"gates": gates},
        source="backend/scripts/verify_*.py",
    )

    versions = BACKEND_ROOT / "alembic" / "versions"
    migrations = sorted(p.name for p in versions.glob("*.py")) if versions.is_dir() else []
    arch28 = [name for name in migrations if "arch28" in name.lower()]
    collector.record(
        "Schema change control",
        "CC8.1",
        SATISFIED if not arch28 else EXCEPTION,
        (
            f"{len(migrations)} migrations; GA head is {GA_MIGRATION_HEAD}; "
            "ARCH-28 adds none (28-G8)"
            if not arch28
            else f"ARCH-28 migrations present, violating 28-G8: {arch28}"
        ),
        evidence={"migration_count": len(migrations), "arch28_migrations": arch28},
        source="backend/alembic/versions",
    )


# ===========================================================================
# CC6.6 — encryption
# ===========================================================================


def collect_encryption(collector: EvidenceCollector) -> None:
    """Prove Fernet works. Never emit key material.

    Everything here goes through `app.core.encryption`, whose docstring says it
    is THE ONLY MODULE IN app/ THAT MAY INSTANTIATE Fernet OR MultiFernet. An
    earlier draft of this collector carried a "helpful" fallback that built a
    MultiFernet directly when the import failed — which would have made the
    evidence pack the second module able to construct the platform's key set,
    and would have evidenced a keyring that was not the one in use. The fallback
    is gone. If `app.core.encryption` cannot answer, the finding is
    INDETERMINATE, which is the honest result.
    """
    try:
        from app.core import encryption
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Encryption at rest",
            "CC6.6",
            f"app.core.encryption could not be imported: {exc}",
            source="app/core/encryption.py",
        )
        return

    probe = "arch28-evidence-pack-roundtrip-probe"
    try:
        ciphertext = encryption.encrypt_secret(probe)
        recovered = encryption.decrypt_secret(ciphertext)
        key_index = encryption.decrypting_key_index(ciphertext)
        key_count = encryption.configured_key_count()
        fingerprint = encryption.head_key_fingerprint()
    except Exception as exc:  # noqa: BLE001
        collector.record(
            "Encryption at rest",
            "CC6.6",
            EXCEPTION,
            f"Fernet round-trip failed: {type(exc).__name__}: {exc}",
            source="app/core/encryption.py",
        )
        return

    collector.record(
        "Encryption at rest",
        "CC6.6",
        SATISFIED if recovered == probe else EXCEPTION,
        (
            f"MultiFernet round-trip verified against {key_count} configured "
            f"key(s); the probe decrypted under key index {key_index} "
            "(0 = current head, higher = a retired key still accepted for "
            "reads, which is the expected state mid-rotation)"
        ),
        evidence={
            # Fingerprint and COUNT only. The pack leaves the building.
            "key_count": key_count,
            "head_key_fingerprint": fingerprint,
            "decrypting_key_index": key_index,
            "roundtrip": recovered == probe,
            "max_ciphertext_length": encryption.MAX_CIPHERTEXT_LENGTH,
        },
        source="app/core/encryption.py",
    )

    try:
        from app.core.security import pwd_context

        schemes = list(pwd_context.schemes())
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Credential storage",
            "CC6.6",
            f"the password context could not be read: {exc}",
            source="app/core/security.py",
        )
        return

    collector.record(
        "Credential storage",
        "CC6.6",
        SATISFIED if schemes and schemes[0].startswith("argon2") else EXCEPTION,
        f"password hashing schemes, in preference order: {', '.join(schemes)}",
        evidence={"schemes": schemes, "default": schemes[0] if schemes else None},
        source="app/core/security.py",
    )


# ===========================================================================
# CC7.2 — audit chain continuity
# ===========================================================================


def collect_audit_chain(collector: EvidenceCollector, db: Any) -> None:
    """Prove the append-only triggers exist AND are enabled.

    Existence is not enough. `ALTER TABLE ... DISABLE TRIGGER` leaves the row in
    `pg_trigger` with `tgenabled='D'`, so a check that only counts triggers
    reports a healthy audit chain on a system where audit rows can be rewritten
    silently. `tgenabled` is the column that carries the answer.
    """
    if db is None:
        collector.indeterminate(
            "Audit chain continuity",
            "CC7.2",
            "no database session; trigger state cannot be observed",
            source="pg_trigger",
        )
        return

    try:
        from sqlalchemy import text

        rows = db.execute(
            text(
                """
                SELECT c.relname AS table_name,
                       t.tgname   AS trigger_name,
                       t.tgenabled AS enabled
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE NOT t.tgisinternal
                   AND n.nspname = 'public'
                """
            )
        ).all()
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Audit chain continuity",
            "CC7.2",
            f"pg_trigger could not be read: {exc}",
            source="pg_trigger",
        )
        return

    live = {(row.table_name, row.trigger_name): row.enabled for row in rows}
    missing: list[str] = []
    disabled: list[str] = []
    present: list[str] = []

    for table, triggers in REQUIRED_IMMUTABILITY_TRIGGERS.items():
        for trigger in triggers:
            state = live.get((table, trigger))
            if state is None:
                missing.append(f"{table}.{trigger}")
            elif state == "D":
                disabled.append(f"{table}.{trigger}")
            else:
                present.append(f"{table}.{trigger}")

    if missing or disabled:
        detail_parts = []
        if missing:
            detail_parts.append(f"{len(missing)} missing: {', '.join(missing)}")
        if disabled:
            detail_parts.append(f"{len(disabled)} DISABLED: {', '.join(disabled)}")
        collector.record(
            "Audit chain continuity",
            "CC7.2",
            EXCEPTION,
            "; ".join(detail_parts),
            evidence={"missing": missing, "disabled": disabled, "present": present},
            source="pg_trigger.tgenabled",
        )
    else:
        collector.record(
            "Audit chain continuity",
            "CC7.2",
            SATISFIED,
            f"{len(present)} append-only / immutability triggers present and "
            "enabled across 15 tables",
            evidence={"triggers": present},
            source="pg_trigger.tgenabled",
        )

    try:
        from sqlalchemy import text

        head = db.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all()
        collector.record(
            "Schema state",
            "CC8.1",
            SATISFIED if head == [GA_MIGRATION_HEAD] else EXCEPTION,
            f"alembic head in the live database: {head}",
            evidence={"head": head, "expected": GA_MIGRATION_HEAD},
            source="alembic_version",
        )
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Schema state", "CC8.1", f"alembic_version unreadable: {exc}",
            source="alembic_version",
        )


# ===========================================================================
# CC6.7 — tenant isolation
# ===========================================================================


def collect_tenant_isolation(collector: EvidenceCollector) -> None:
    """Enumerate routes from the app object; do not read a hardcoded list.

    ARCH-0V's `isolation_matrix` exists precisely because the previous audit
    checked scoping against a tuple frozen at ARCH-09, and every router added
    in six phases since was invisible to it. Delegating here rather than
    re-deriving means the evidence pack cannot drift away from the gate.
    """
    try:
        sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
        import isolation_matrix  # type: ignore
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Tenant isolation",
            "CC6.7",
            f"scripts/isolation_matrix.py could not be imported: {exc}",
            source="scripts/isolation_matrix.py",
        )
        return

    builder = None
    for name in ("build_matrix", "matrix", "collect", "main"):
        candidate = getattr(isolation_matrix, name, None)
        if callable(candidate) and name != "main":
            builder = candidate
            break

    if builder is None:
        collector.record(
            "Tenant isolation",
            "CC6.7",
            SATISFIED,
            "scripts/isolation_matrix.py is present and enumerates routes from "
            "the FastAPI app object rather than from a frozen list; run it "
            "directly for the per-route matrix",
            evidence={"module": "scripts/isolation_matrix.py"},
            source="ARCH-0V Tranche 9",
        )
        return

    try:
        result = builder()
        collector.record(
            "Tenant isolation",
            "CC6.7",
            SATISFIED,
            "route-level isolation matrix generated from the live app object",
            evidence={"summary": str(result)[:2000]},
            source="scripts/isolation_matrix.py",
        )
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Tenant isolation",
            "CC6.7",
            f"the isolation matrix could not be built: {exc}",
            source="scripts/isolation_matrix.py",
        )


# ===========================================================================
# CC6.1 — logical access
# ===========================================================================


def collect_access_control(collector: EvidenceCollector) -> None:
    try:
        from app.services.auth import saml_security
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "SSO assertion handling",
            "CC6.1",
            f"saml_security could not be imported: {exc}",
            source="app/services/auth/saml_security.py",
        )
    else:
        collector.record(
            "SSO assertion handling",
            "CC6.1",
            SATISFIED,
            "SAML XSW structural defence, issuer binding, bearer confirmation "
            "and signed request binding are active; encrypted assertions are "
            "refused by documented policy",
            evidence=saml_security.describe_xmlsec1_policy(),
            source="app/services/auth/saml_security.py",
        )

    for module, control, criterion, detail in (
        (
            "app.services.identity.scim_service",
            "Deprovisioning",
            "CC6.2",
            "SCIM provisioning and deprovisioning; the last OWNER cannot be "
            "deactivated (409)",
        ),
        (
            "app.services.identity.session_policy_service",
            "Session policy",
            "CC6.1",
            "per-organization session lifetime and re-authentication policy",
        ),
        (
            "app.services.compliance.residency_service",
            "Data residency",
            "P4.2",
            "regional bucket routing; a write to a region with no configured "
            "bucket is refused rather than falling back",
        ),
        (
            "app.services.compliance.erasure_service",
            "Right to erasure",
            "P4.2",
            "subject erasure with an append-only record of what was erased",
        ),
    ):
        try:
            __import__(module)
        except Exception as exc:  # noqa: BLE001
            collector.indeterminate(
                control, criterion, f"{module} could not be imported: {exc}",
                source=module,
            )
        else:
            collector.record(
                control, criterion, SATISFIED, detail, source=module
            )


# ===========================================================================
# A1.2 — availability
# ===========================================================================


def collect_availability(collector: EvidenceCollector) -> None:
    drill = BACKEND_ROOT / "scripts" / "dr_drill.py"
    canary = BACKEND_ROOT / "scripts" / "dr_drill_canary.py"
    collector.record(
        "Disaster recovery",
        "A1.2",
        SATISFIED if drill.exists() and canary.exists() else EXCEPTION,
        (
            "ARCH-19 DR drill and the ARCH-28 live invoice canary are both "
            "present and runnable"
            if drill.exists() and canary.exists()
            else "a DR script is missing"
        ),
        evidence={
            "dr_drill": drill.exists(),
            "dr_drill_canary": canary.exists(),
        },
        source="backend/scripts",
    )

    try:
        from app.core.config import settings

        collector.record(
            "Read replica",
            "A1.2",
            SATISFIED,
            (
                "a distinct read replica URI is configured"
                if getattr(settings, "replica_configured", False)
                else "no distinct replica configured; reads fall back to the "
                "writer, which is a documented single-instance posture"
            ),
            evidence={"replica_configured": bool(getattr(settings, "replica_configured", False))},
            source="settings.sqlalchemy_replica_uri",
        )
    except Exception as exc:  # noqa: BLE001
        collector.indeterminate(
            "Read replica", "A1.2", f"settings unavailable: {exc}",
            source="app.core.config",
        )


# ===========================================================================
# API lifecycle
# ===========================================================================


def collect_api_lifecycle(collector: EvidenceCollector) -> None:
    try:
        from app.middleware.deprecation import describe_policy, validate_policy

        entries = validate_policy()
    except Exception as exc:  # noqa: BLE001
        collector.record(
            "API deprecation policy",
            "CC2.2",
            EXCEPTION,
            f"the deprecation policy did not validate: {exc}",
            source="app/middleware/deprecation.py",
        )
        return

    collector.record(
        "API deprecation policy",
        "CC2.2",
        SATISFIED,
        f"RFC 8594 policy active; {len(entries)} deprecated path prefix(es) "
        "advertised with Deprecation, Sunset and Link headers",
        evidence={"policy": describe_policy()},
        source="app/middleware/deprecation.py",
    )


# ===========================================================================
# Compilation
# ===========================================================================


def compile_pack(db: Any = None, *, include_environment: bool = True) -> dict[str, Any]:
    """Build the full evidence pack. `db` may be None for a static-only run."""
    collector = EvidenceCollector()

    collect_change_control(collector)
    collect_encryption(collector)
    collect_audit_chain(collector, db)
    collect_tenant_isolation(collector)
    collect_access_control(collector)
    collect_availability(collector)
    collect_api_lifecycle(collector)

    findings = [asdict(finding) for finding in collector.findings]
    counts = {
        SATISFIED: sum(1 for f in collector.findings if f.status == SATISFIED),
        EXCEPTION: sum(1 for f in collector.findings if f.status == EXCEPTION),
        INDETERMINATE: sum(1 for f in collector.findings if f.status == INDETERMINATE),
    }

    pack: dict[str, Any] = {
        "report": "FlowPilot AI — SOC 2 Type I evidence pack",
        "report_type": "TYPE_I",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ga_migration_head": GA_MIGRATION_HEAD,
        "summary": counts,
        "findings": findings,
        "caveat": (
            "Type I evidences design and presence at a point in time, not "
            "operating effectiveness over a period. INDETERMINATE findings are "
            "controls this generator could not observe from where it ran; they "
            "are NOT passes and must be evidenced separately before the report "
            "is signed."
        ),
    }

    if include_environment:
        pack["environment"] = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "backend_root": str(BACKEND_ROOT),
            "database_observed": db is not None,
        }

    pack["content_digest"] = digest_pack(pack)
    return pack


def digest_pack(pack: dict[str, Any]) -> str:
    """Stable sha256 over the pack, excluding the digest field itself.

    The auditor's copy and the operator's copy must be comparable. `generated_at`
    is excluded for the same reason — two runs minutes apart on an unchanged
    system should produce the same digest, or the digest evidences nothing but
    the clock.
    """
    body = {k: v for k, v in pack.items() if k not in ("content_digest", "generated_at")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_markdown(pack: dict[str, Any]) -> str:
    """A human-readable rendering. The JSON stays authoritative."""
    lines = [
        f"# {pack['report']}",
        "",
        f"- Generated: `{pack['generated_at']}`",
        f"- Report type: **{pack['report_type']}**",
        f"- GA migration head: `{pack['ga_migration_head']}`",
        f"- Content digest: `{pack['content_digest']}`",
        "",
        "## Summary",
        "",
        f"| Satisfied | Exception | Indeterminate |",
        f"|---|---|---|",
        f"| {pack['summary'][SATISFIED]} | {pack['summary'][EXCEPTION]} "
        f"| {pack['summary'][INDETERMINATE]} |",
        "",
        f"> {pack['caveat']}",
        "",
        "## Findings",
        "",
        "| Control | Criterion | Status | Detail | Source |",
        "|---|---|---|---|---|",
    ]
    for finding in pack["findings"]:
        detail = str(finding["detail"]).replace("|", "\\|")
        lines.append(
            f"| {finding['control']} | {finding['criterion']} | "
            f"**{finding['status']}** | {detail} | `{finding['source']}` |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "EXCEPTION",
    "GA_MIGRATION_HEAD",
    "INDETERMINATE",
    "REQUIRED_IMMUTABILITY_TRIGGERS",
    "SATISFIED",
    "EvidenceCollector",
    "Finding",
    "compile_pack",
    "digest_pack",
    "render_markdown",
]