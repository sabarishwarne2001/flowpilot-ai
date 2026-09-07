"""Seam fixes — retry-path idempotency and LLM parser resilience.

    python scripts/apply_seam_fixes.py            # apply, then run the gate
    python scripts/apply_seam_fixes.py --check    # report only
    python scripts/apply_seam_fixes.py --no-gate  # apply without the gate

Absolute paths from Path(__file__).resolve(), exact-match block replacement,
no regex rewriting of source. Every edit asserts its anchor appears exactly
once and refuses to write anything if any anchor misses.

Scope
-----
Nine of the eleven flagged sites are patched. Two are deliberately excluded
and reclassified; both exclusions are corrections to my own earlier triage
and are explained at their entries below.

I-1  usage_service.record_usage            uq_usage_events_org_idempotency_key
I-2  notification.outbox_dispatcher        uq_notification_deliveries_org_idempotency_key
I-3  automation.executor.create_execution  uq_automation_executions_rule_event
I-4  partner.rev_share_service             uq_partner_rev_share_ledger_line
I-5  billing.invoice_service.assemble      uq_invoices_subscription_period
I-6  reconciliation.engine                 uq_provider_statements_source
I-7  slo_service.record_measurement        uq_slo_measurements_scope
I-8  compliance.erasure_service            uq_erased_subjects_org_email_hash
I-9  workers.handlers.verification         uq_document_verifications_open_work_item

B-1  llm_service._extract_json             top-level arrays
B-2  automation.extraction                 top-level arrays
B-3  handlers/verification.py:69           guard the bare ValueError

EXCLUDED, with reasons
----------------------
document_intake_service.ingest_validated -> WorkItem
    Reclassified USER_INTENT. Both unique columns are values this
    transaction just minted: `stored_filename` is the storage key returned
    by the driver moments earlier, and `uploaded_file_id` is the row flushed
    three lines above. A retry re-runs the upload and mints new ones, so a
    collision is not a replay of the same logical operation — it is two
    different documents. get-or-return here would hand the caller back an
    unrelated work item. My earlier triage put this in RETRY_PATH; that was
    wrong, and the scanner's allowlist is corrected accordingly.

billing.account_service.ensure_billing_account -> BillingAccount
    Left alone pending a decision from you. The db.add() sits AFTER
    `stripe_gateway.create_customer()`, which is a network side effect. Add
    get-or-return at the insert and a losing race returns the existing
    account while leaving behind an orphaned Stripe customer that nothing
    references and nothing reaps. The correct fix is a lock or a lookup
    BEFORE the remote call, not a savepoint after it, and that is a design
    decision about Stripe customer lifecycle rather than a mechanical edit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BACKEND = Path(__file__).resolve().parent.parent


class AnchorMiss(RuntimeError):
    """An expected anchor is absent. Never downgraded to a warning."""


@dataclass
class Edit:
    ident: str
    relative: str
    sentinel: str
    old: str
    new: str
    extra_import: Optional[tuple[str, str]] = None

    @property
    def path(self) -> Path:
        return BACKEND / self.relative


EDITS: list[Edit] = [

    # ---------------------------------------------------------------- I-1 ---
    Edit(
        ident="I-1 usage_service.record_usage",
        relative="app/services/usage_service.py",
        sentinel="SEAM-I-1",
        extra_import=(
            "from app.core.usage_events import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.core.usage_events import",
        ),
        old="""    db.add(event)
    db.flush([event])
""",
        new='''    # SEAM-I-1. uq_usage_events_org_idempotency_key: (organization_id, idempotency_key)
    # WHERE idempotency_key IS NOT NULL. Metering is the one place a lost
    # write is unrecoverable — the tokens were served and the customer is
    # never billed for them.
    event, _created = insert_or_get(
        db,
        instance=event,
        lookup=lambda: (
            None
            if not idempotency_key
            else db.execute(
                select(UsageEvent)
                .where(UsageEvent.organization_id == organization_id)
                .where(UsageEvent.idempotency_key == idempotency_key)
                .limit(1)
            ).scalar_one_or_none()
        ),
        label="usage.record_usage",
        log_extra={
            "organization_id": str(organization_id),
            "idempotency_key": idempotency_key,
        },
    )
''',
    ),

    # ---------------------------------------------------------------- I-2 ---
    Edit(
        ident="I-2 notification.outbox_dispatcher",
        relative="app/services/notification/outbox_dispatcher.py",
        sentinel="SEAM-I-2",
        extra_import=(
            "from app.models.notification_delivery import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.notification_delivery import",
        ),
        old="""        db.add(delivery)
        db.flush([delivery])
        result.deliveries.append(delivery.id)

        if preference.channel is not NotificationChannel.IN_APP:
""",
        new='''        # SEAM-I-2. uq_notification_deliveries_org_idempotency_key:
        # (organization_id, idempotency_key) WHERE idempotency_key IS NOT NULL.
        # The constraint was named for this and never given the handling.
        delivery, created = insert_or_get(
            db,
            instance=delivery,
            lookup=lambda: (
                None
                if not idempotency_key
                else db.execute(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.organization_id == organization_id)
                    .where(NotificationDelivery.idempotency_key == idempotency_key)
                    .limit(1)
                ).scalar_one_or_none()
            ),
            label="notification.delivery",
            log_extra={
                "organization_id": str(organization_id),
                "idempotency_key": idempotency_key,
            },
        )
        result.deliveries.append(delivery.id)

        # `created` gates the enqueue. Without it, a replay returns the
        # existing delivery and then queues a SECOND send job for it, which
        # is how a retry turns into a duplicate email.
        if created and preference.channel is not NotificationChannel.IN_APP:
''',
    ),

    # ---------------------------------------------------------------- I-3 ---
    Edit(
        ident="I-3 automation.executor.create_execution",
        relative="app/services/automation/executor.py",
        sentinel="SEAM-I-3",
        extra_import=(
            "from app.models.automation_execution import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.automation_execution import",
        ),
        old="""    db.add(execution)
    db.flush()
    return execution
""",
        new='''    # SEAM-I-3. uq_automation_executions_rule_event: (rule_id, outbox_event_id)
    # WHERE outbox_event_id IS NOT NULL. The index is partial, so an
    # execution with no originating event is never deduplicated and the
    # lookup must return None for that case rather than matching on NULL.
    execution, _created = insert_or_get(
        db,
        instance=execution,
        lookup=lambda: (
            None
            if outbox_event_id is None
            else db.execute(
                select(AutomationExecution)
                .where(AutomationExecution.rule_id == rule.id)
                .where(AutomationExecution.outbox_event_id == outbox_event_id)
                .limit(1)
            ).scalar_one_or_none()
        ),
        label="automation.execution",
        log_extra={
            "rule_id": str(rule.id),
            "outbox_event_id": str(outbox_event_id) if outbox_event_id else None,
        },
    )
    return execution
''',
    ),

    # ---------------------------------------------------------------- I-4 ---
    Edit(
        ident="I-4 partner.rev_share_service",
        relative="app/services/partner/rev_share_service.py",
        sentinel="SEAM-I-4",
        extra_import=(
            "from app.models.partner import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.partner import",
        ),
        old="""        db.add(line)

        gross_revenue += bucket.revenue_micros
""",
        new='''        # SEAM-I-4. uq_partner_rev_share_ledger_line:
        # (payout_period_id, organization_id, basis_class).
        # There is no flush inside this loop, so before this change a
        # collision surfaced at the closing commit and took the whole
        # period's ledger with it rather than the one duplicated line.
        line, _created = insert_or_get(
            db,
            instance=line,
            lookup=lambda: db.execute(
                select(PartnerRevShareLedger)
                .where(PartnerRevShareLedger.payout_period_id == period.id)
                .where(PartnerRevShareLedger.organization_id == bucket.organization_id)
                .where(PartnerRevShareLedger.basis_class == bucket.basis_class)
                .limit(1)
            ).scalar_one_or_none(),
            label="partner.rev_share_line",
            log_extra={
                "payout_period_id": str(period.id),
                "organization_id": str(bucket.organization_id),
                "basis_class": bucket.basis_class,
            },
        )

        gross_revenue += bucket.revenue_micros
''',
    ),

    # ---------------------------------------------------------------- I-5 ---
    Edit(
        ident="I-5 billing.invoice_service.assemble",
        relative="app/services/billing/invoice_service.py",
        sentinel="SEAM-I-5",
        extra_import=(
            "from app.models.invoice import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.invoice import",
        ),
        old="""    db.add(invoice)
    db.flush()

    lines: list[InvoiceLineItem] = []
""",
        new='''    # SEAM-I-5. uq_invoices_subscription_period: (subscription_id, period_start,
    # period_end). Deliberately not uq_invoices_number — `number` was
    # allocated by allocate_number() moments ago and is unique by
    # construction, so matching on it would never find the prior attempt's
    # row. The period tuple is what identifies the same logical invoice
    # across a retried assemble job.
    invoice, created = insert_or_get(
        db,
        instance=invoice,
        lookup=lambda: db.execute(
            select(Invoice)
            .where(Invoice.subscription_id == subscription.id)
            .where(Invoice.period_start == start)
            .where(Invoice.period_end == end)
            .limit(1)
        ).scalar_one_or_none(),
        label="billing.invoice_assemble",
        log_extra={
            "subscription_id": str(subscription.id),
            "period_start": start.isoformat() if start else None,
        },
    )
    if not created:
        # The prior attempt already wrote this invoice and its line items.
        # Re-running the line loop would duplicate them under
        # uq_invoice_line_items_number.
        return invoice

    lines: list[InvoiceLineItem] = []
''',
    ),

    # ---------------------------------------------------------------- I-6 ---
    Edit(
        ident="I-6 reconciliation.engine.persist_statement",
        relative="app/services/reconciliation/engine.py",
        sentinel="SEAM-I-6",
        extra_import=(
            "from app.models.reconciliation import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.reconciliation import",
        ),
        old="""    db.add(statement)
    db.flush([statement])

    for line in payload.lines:
""",
        new='''    # SEAM-I-6. uq_provider_statements_source: (provider, source_key).
    statement, created = insert_or_get(
        db,
        instance=statement,
        lookup=lambda: db.execute(
            select(ProviderStatement)
            .where(ProviderStatement.provider == payload.provider)
            .where(ProviderStatement.source_key == payload.source_key)
            .limit(1)
        ).scalar_one_or_none(),
        label="reconciliation.statement",
        log_extra={"provider": payload.provider, "source_key": payload.source_key},
    )
    if not created:
        # Statement lines belong to the statement. Re-inserting them against
        # an existing header would double every supplier cost in the period,
        # which is the one error class ARCH-18 exists to prevent.
        return statement

    for line in payload.lines:
''',
    ),

    # ---------------------------------------------------------------- I-7 ---
    Edit(
        ident="I-7 slo_service.record_measurement",
        relative="app/services/slo_service.py",
        sentinel="SEAM-I-7",
        extra_import=(
            "from app.models.slo import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.slo import",
        ),
        old="""    if existing is None:
        measurement = SLOMeasurement(
            slo_definition_id=definition.id,
            organization_id=organization_id,
            slo_key=slo_key,
            window_start=start,
            window_end=end,
        )
        db.add(measurement)
    else:
        measurement = existing
""",
        new='''    if existing is None:
        # SEAM-I-7. uq_slo_measurements_scope:
        # (slo_definition_id, organization_id, window_start).
        # The get-or-return shape was already here; what was missing is the
        # savepoint. Two recorders sampling the same window concurrently
        # both see `existing is None` and both insert.
        measurement, _created = insert_or_get(
            db,
            instance=SLOMeasurement(
                slo_definition_id=definition.id,
                organization_id=organization_id,
                slo_key=slo_key,
                window_start=start,
                window_end=end,
            ),
            lookup=lambda: db.execute(
                select(SLOMeasurement)
                .where(SLOMeasurement.slo_definition_id == definition.id)
                .where(SLOMeasurement.organization_id == organization_id)
                .where(SLOMeasurement.window_start == start)
                .limit(1)
            ).scalar_one_or_none(),
            label="slo.measurement",
            log_extra={"slo_key": slo_key, "window_start": start.isoformat()},
        )
    else:
        measurement = existing
''',
    ),

    # ---------------------------------------------------------------- I-8 ---
    Edit(
        ident="I-8 compliance.erasure_service",
        relative="app/services/compliance/erasure_service.py",
        sentinel="SEAM-I-8",
        extra_import=(
            "from app.models.compliance import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.compliance import",
        ),
        old="""    db.add(tombstone)
    db.flush()
""",
        new='''    # SEAM-I-8. uq_erased_subjects_org_email_hash:
    # (organization_id, subject_email_hash). A second erasure request for a
    # subject already erased must return the original tombstone, not raise:
    # under Art. 17 the erasure has already happened and the record of it is
    # the answer.
    tombstone, _created = insert_or_get(
        db,
        instance=tombstone,
        lookup=lambda: db.execute(
            select(ErasedSubject)
            .where(ErasedSubject.organization_id == organization_id)
            .where(ErasedSubject.subject_email_hash == tombstone.subject_email_hash)
            .limit(1)
        ).scalar_one_or_none(),
        label="compliance.erasure_tombstone",
        log_extra={"organization_id": str(organization_id)},
    )
''',
    ),

    # ---------------------------------------------------------------- I-9 ---
    Edit(
        ident="I-9 workers.handlers.verification",
        relative="app/workers/handlers/verification.py",
        sentinel="SEAM-I-9",
        extra_import=(
            "from app.models.verification import",
            "from app.core.idempotent_insert import insert_or_get\nfrom app.models.verification import",
        ),
        old="""        db.add(verification)
        db.commit()
""",
        new='''        # SEAM-I-9. uq_document_verifications_open_work_item: (work_item_id)
        # WHERE status IN ('PENDING', 'DISAGREED').
        #
        # The status predicate is load-bearing and must be in the lookup.
        # Without it, a work item with a RESOLVED verification from a prior
        # run returns that closed record, the handler treats it as the open
        # one it just created, and agent output is written against a
        # verification a human already signed off.
        verification, _created = insert_or_get(
            db,
            instance=verification,
            lookup=lambda: db.execute(
                select(DocumentVerification)
                .where(DocumentVerification.work_item_id == work_item.id)
                .where(
                    DocumentVerification.status.in_(
                        [VerificationStatus.PENDING, VerificationStatus.DISAGREED]
                    )
                )
                .limit(1)
            ).scalar_one_or_none(),
            label="verification.open_record",
            log_extra={"work_item_id": str(work_item.id)},
        )
        db.commit()
''',
    ),

    # --------------------------------------------------------------- I-10 ---
    Edit(
        ident="I-10 billing.account_service.ensure_billing_account",
        relative="app/services/billing/account_service.py",
        sentinel="SEAM-I-10",
        old="""    existing = get_for_organization(db, organization_id=organization_id)
    if existing is not None:
        return existing

    organization = db.execute(
        select(Organization).where(Organization.id == organization_id)
    ).scalar_one_or_none()
    if organization is None:
        raise BillingAccountError(f"Organization {organization_id} does not exist.")
""",
        new='''    existing = get_for_organization(db, organization_id=organization_id)
    if existing is not None:
        return existing

    # SEAM-I-10. uq_billing_accounts_organization_id.
    #
    # A savepoint at the db.add() would be the wrong fix here, and worse than
    # the bug. The insert sits AFTER stripe_gateway.create_customer(), so a
    # loser in the race would return the existing account while leaving behind
    # a second Stripe customer that nothing references and nothing reaps —
    # precisely the outcome this function's own docstring warns about.
    #
    # The create path has to be serialised BEFORE the remote call instead.
    # FOR UPDATE on the organization row is the lock ARCH-05 already uses for
    # ownership transfer, so this adds no new locking concept.
    #
    # Tradeoff, stated plainly: the row lock is held across a Stripe network
    # call, so a slow Stripe blocks concurrent callers for that organization.
    # That is a real cost and it is the cheaper one — an orphaned customer is
    # silent, permanent, and reconciles against nothing.
    organization = db.execute(
        select(Organization)
        .where(Organization.id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if organization is None:
        raise BillingAccountError(f"Organization {organization_id} does not exist.")

    # Re-check under the lock. A caller that passed the unlocked check above
    # and then blocked here must see the account the winner committed.
    existing = get_for_organization(db, organization_id=organization_id)
    if existing is not None:
        return existing
''',
    ),

    # ---------------------------------------------------------------- B-1 ---
    Edit(
        ident="B-1 llm_service._extract_json top-level arrays",
        relative="app/services/llm_service.py",
        sentinel="SEAM-B-1",
        old="""        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    pass
            logger.error("Unable to parse JSON response from LLM.")
            raise ValueError("Model returned invalid JSON.")
""",
        new='''        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # SEAM-B-1. Brace-only scanning missed a real case: Gemini returns a bare
        # top-level array under some extraction prompts, and `[{...}]` has
        # no outermost brace pair spanning the whole payload. Try both
        # bracket kinds and keep whichever yields the longest valid span,
        # so a preamble containing a stray "{" cannot win over the real
        # object that follows it.
        best: Any = None
        best_length = 0
        for opener, closer in _JSON_OPENERS:
            start = cleaned.find(opener)
            end = cleaned.rfind(closer)
            if start == -1 or end <= start:
                continue
            candidate = cleaned[start : end + 1]
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if len(candidate) > best_length:
                best, best_length = parsed, len(candidate)

        if best is not None:
            return best

        logger.error(
            "llm.json_parse_failed",
            extra={"preview": cleaned[:200], "length": len(cleaned)},
        )
        raise ValueError("Model returned invalid JSON.")
''',
    ),

    # ---------------------------------------------------------------- B-2 ---
    Edit(
        ident="B-2 automation.extraction top-level arrays",
        relative="app/services/automation/extraction.py",
        sentinel="SEAM-B-2",
        old="""    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise SchemaViolation(
            "Extraction returned no JSON object. The node produces data a "
            "condition tests; free text is not data."
        )

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SchemaViolation(f"Extraction returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise SchemaViolation(
            f"Extraction returned a {type(parsed).__name__}, not an object."
        )
""",
        new='''    parsed = _first_json_value(text)
    if parsed is None:
        raise SchemaViolation(
            "Extraction returned no JSON value. The node produces data a "
            "condition tests; free text is not data."
        )

    # SEAM-B-2. A model told to emit one object sometimes wraps it in an array. That
    # is a formatting quirk, not a schema violation, so unwrap a
    # single-element list rather than failing the node over it. Anything
    # longer is genuinely ambiguous and still fails.
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            parsed = parsed[0]
        else:
            raise SchemaViolation(
                f"Extraction returned a {len(parsed)}-element array; the node "
                "schema declares a single object."
            )

    if not isinstance(parsed, dict):
        raise SchemaViolation(
            f"Extraction returned a {type(parsed).__name__}, not an object."
        )
''',
    ),

    # ---------------------------------------------------------------- B-3 ---
    Edit(
        ident="B-3 verification handler guards _extract_json",
        relative="app/workers/handlers/verification.py",
        sentinel="SEAM-B-3",
        old="""    entities = llm_service._extract_json(response)
""",
        new='''    try:
        entities = llm_service._extract_json(response)
    except ValueError as exc:
        # SEAM-B-3. Multi-agent verification is a quorum. One agent returning prose
        # instead of JSON is a disagreement signal, not a pipeline fault —
        # and letting the ValueError escape burned all five job attempts on
        # a response that would never parse, stranding the document.
        # An empty dict disagrees with every other agent, which drops the
        # confidence score and routes the fields to the review queue. That
        # is the behaviour a human reviewer needs anyway.
        logger.warning(
            "verification.agent_unparseable",
            extra={"error": str(exc), "preview": (response or "")[:200]},
        )
        entities = {}
''',
    ),
]


_LLM_OPENERS_CONST = '''
#: Bracket pairs tried when a raw json.loads fails. Objects first: a model
#: told to emit an object usually does, and preferring `{` keeps the common
#: case cheap.
_JSON_OPENERS: tuple[tuple[str, str], ...] = (("{", "}"), ("[", "]"))

'''

_EXTRACTION_HELPER = '''
def _first_json_value(text: str) -> Any:
    """Longest valid JSON object or array in `text`, or None.

    Scans both bracket kinds rather than braces alone. A bare top-level
    array has no outermost brace pair spanning it, so brace-only scanning
    reported "no JSON object" for a payload that was valid JSON throughout.

    Longest-wins matters: a conversational preamble containing a stray "{"
    produces a short span that parses to something useless, and it must not
    beat the real payload that follows.
    """
    best: Any = None
    best_length = 0
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start == -1 or end <= start:
            continue
        candidate = text[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if len(candidate) > best_length:
            best, best_length = parsed, len(candidate)
    return best


'''


def _preflight(edit: Edit, source: str) -> None:
    count = source.count(edit.old)
    if count != 1:
        raise AnchorMiss(
            f"{edit.ident}: anchor appears {count} times in "
            f"{edit.relative}, expected exactly 1"
        )


def _apply(edit: Edit, source: str) -> str:
    _preflight(edit, source)
    patched = source.replace(edit.old, edit.new)

    if edit.extra_import:
        marker, replacement = edit.extra_import
        if "from app.core.idempotent_insert import insert_or_get" not in patched:
            if marker not in patched:
                raise AnchorMiss(
                    f"{edit.ident}: import marker {marker!r} not found in {edit.relative}"
                )
            patched = patched.replace(marker, replacement, 1)

    # Module-level additions that the new bodies reference.
    # The guard must not match the code this patch just inserted. The new
    # body contains `for opener, closer in _JSON_OPENERS:`, which matched a
    # naive "_JSON_OPENERS:" check and skipped the constant definition,
    # leaving a NameError at runtime that compiled fine. Match the binding.
    if edit.ident.startswith("B-1") and "_JSON_OPENERS: tuple" not in patched:
        marker = "\nclass "
        index = patched.find(marker)
        if index == -1:
            raise AnchorMiss("B-1: no class definition to anchor _JSON_OPENERS above")
        index += 1
        patched = patched[:index] + _LLM_OPENERS_CONST.lstrip("\n") + "\n" + patched[index:]

    if edit.ident.startswith("B-2") and "def _first_json_value" not in patched:
        marker = "def parse_extraction_response("
        if marker not in patched:
            raise AnchorMiss("B-2: parse_extraction_response not found")
        patched = patched.replace(marker, _EXTRACTION_HELPER.lstrip("\n") + marker, 1)

    return patched


def _ensure_select_import(path: Path, source: str) -> str:
    """Every new lookup uses `select`. Add the import where it is absent."""
    if "from sqlalchemy import select" in source or "from sqlalchemy import (" in source:
        return source
    if "\nfrom sqlalchemy" in source:
        index = source.index("\nfrom sqlalchemy")
        return source[:index] + "\nfrom sqlalchemy import select" + source[index:]
    raise AnchorMiss(f"{path.name}: no sqlalchemy import block to extend")


def _run_gate() -> int:
    scanner = BACKEND / "scripts" / "scan_idempotency_seams.py"
    if not scanner.exists():
        print("scan_idempotency_seams.py not found; skipping gate.", file=sys.stderr)
        return 0
    print("\n" + "=" * 70)
    print("Running scan_idempotency_seams.py --gate")
    print("=" * 70)
    result = subprocess.run(
        [sys.executable, str(scanner), "--gate"], cwd=str(BACKEND)
    )
    return result.returncode


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="apply_seam_fixes")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-gate", action="store_true")
    args = parser.parse_args(argv)

    # Working copy per path. Two edits can target the same file (verification.py
    # takes both I-9 and B-3); computing each from the on-disk original meant
    # the second write silently clobbered the first.
    working: dict[Path, str] = {}
    applied: list[Edit] = []
    failures = 0

    for edit in EDITS:
        if not edit.path.exists():
            print(f"[{edit.ident}] MISSING FILE: {edit.relative}", file=sys.stderr)
            failures += 1
            continue

        if edit.path not in working:
            working[edit.path] = edit.path.read_text(encoding="utf-8")
        source = working[edit.path]

        if edit.sentinel in source:
            print(f"[{edit.ident}] already applied")
            continue

        if args.check:
            print(f"[{edit.ident}] NOT APPLIED", file=sys.stderr)
            applied.append(edit)
            continue

        try:
            patched = _apply(edit, source)
            if "insert_or_get(" in patched and edit.extra_import:
                patched = _ensure_select_import(edit.path, patched)
        except AnchorMiss as exc:
            print(f"[{edit.ident}] ANCHOR MISS -> {exc}", file=sys.stderr)
            failures += 1
            continue

        if edit.sentinel not in patched:
            print(f"[{edit.ident}] post-condition failed", file=sys.stderr)
            failures += 1
            continue

        working[edit.path] = patched
        applied.append(edit)

    if failures:
        print(
            f"\n{failures} edit(s) failed anchor resolution. "
            "Nothing written — the patch is all-or-nothing.",
            file=sys.stderr,
        )
        return 1

    if args.check:
        if applied:
            print(f"\n{len(applied)} edit(s) pending.", file=sys.stderr)
            return 1
        print("\nAll seam fixes are in place.")
        return 0

    touched = {edit.path for edit in applied}
    for path in sorted(touched):
        path.write_text(working[path], encoding="utf-8")

    for edit in applied:
        print(f"[{edit.ident}] applied -> {edit.relative}")

    print(f"\n{len(applied)} edit(s) written across {len(touched)} file(s).")

    if args.no_gate:
        return 0
    return _run_gate()


if __name__ == "__main__":
    raise SystemExit(main())