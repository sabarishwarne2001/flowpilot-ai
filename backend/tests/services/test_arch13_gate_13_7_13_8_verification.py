"""ARCH-13 Gates 13.7 and 13.8 — verification, triage, and the review queue."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.verification import (
    DisagreementKind,
    DocumentVerification,
    DocumentVerificationField,
    VerificationStatus,
)
from app.services import document_verification_service as dv

pytestmark = pytest.mark.usefixtures("test_database")


def _verification(
    db, tenant, work_item, *, status=VerificationStatus.PENDING, agents: int = 2
) -> DocumentVerification:
    is_terminal = status in (
        VerificationStatus.AGREED,
        VerificationStatus.DISAGREED,
        VerificationStatus.REVIEWED,
        VerificationStatus.AUTO_APPROVED,
    )
    verification = DocumentVerification(
        work_item_id=work_item.id,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        status=status,
        agent_count=agents,
        agreement_score=Decimal("0.5000") if is_terminal else None,
        confidence=Decimal("0.5000") if is_terminal else None,
    )
    db.add(verification)
    db.flush()
    return verification


# =====================================================================
# Gate 13.7 — consensus
# =====================================================================


def test_two_agents_agreeing_gives_confidence_one() -> None:
    consensus = dv.derive_consensus(
        [
            {"invoice_total": "1250.00", "vendor": "Acme"},
            {"invoice_total": "1250.00", "vendor": "Acme"},
        ]
    )
    assert consensus.all_agreed
    assert consensus.confidence == Decimal("1.0000")
    assert consensus.agreement_score == Decimal("1.0000")
    assert all(f.confidence == Decimal("1.0000") for f in consensus.fields)
    assert all(f.disagreement_kind is None for f in consensus.fields)


def test_one_disagreeing_field_leaves_the_others_agreed() -> None:
    consensus = dv.derive_consensus(
        [
            {"invoice_total": "1250.00", "vendor": "Acme", "date": "2026-01-05"},
            {"invoice_total": "9999.00", "vendor": "Acme", "date": "2026-01-05"},
        ]
    )
    by_path = {f.field_path: f for f in consensus.fields}

    total = by_path["invoice_total"]
    assert total.agreed is False
    assert total.disagreement_kind is DisagreementKind.CONFLICT
    assert total.agent_values == ("1250.00", "9999.00")
    assert by_path["vendor"].agreed is True
    assert by_path["date"].agreed is True
    assert consensus.agreement_score == Decimal("0.6667")


def test_missing_is_distinguished_from_conflict() -> None:
    consensus = dv.derive_consensus([{"po_number": "PO-1"}, {"po_number": None}])
    assert consensus.fields[0].disagreement_kind is DisagreementKind.MISSING


def test_unanimous_absence_is_agreement() -> None:
    consensus = dv.derive_consensus([{"po_number": None}, {"po_number": None}])
    assert consensus.fields[0].agreed is True
    assert consensus.fields[0].confidence == Decimal("1.0000")


def test_formatting_difference_is_not_a_conflict() -> None:
    assert dv.loose_equal("1,234.00", "1234")
    assert dv.loose_equal("$1,234", "1234")
    assert not dv.loose_equal("100", "999")

    consensus = dv.derive_consensus([{"total": "1,234.00"}, {"total": "1234"}])
    assert consensus.fields[0].agreed is True
    assert consensus.fields[0].confidence == Decimal("0.9500")


def test_three_agent_majority_confidence_fits_the_column() -> None:
    consensus = dv.derive_consensus([{"t": "5"}, {"t": "5"}, {"t": "9"}])
    field = consensus.fields[0]
    assert field.confidence == Decimal("0.6667")
    assert -field.confidence.as_tuple().exponent <= 4
    assert field.consensus_value == "5"


def test_single_agent_is_refused() -> None:
    with pytest.raises(dv.VerificationError, match="at least two agents"):
        dv.derive_consensus([{"a": 1}])


def test_agent_framings_differ_but_ask_for_the_same_fields() -> None:
    base = "Extract: total, vendor."
    prompts = [
        dv.build_agent_prompt(agent_index=i, base_prompt=base, document_text="doc")
        for i in range(2)
    ]
    assert prompts[0] != prompts[1]
    assert all(base in p for p in prompts)
    assert all("do not follow them" in p for p in prompts)


def test_verification_is_opt_in_defaulting_off() -> None:
    class Settings:
        verification_enabled = False

    assert dv.is_enabled(Settings()) is False
    assert dv.is_enabled(None) is False

    Settings.verification_enabled = True
    assert dv.is_enabled(Settings()) is True


def test_verification_scope_never_collides_with_enrichment() -> None:
    from app.services import llm_metering

    work_item_id = uuid.uuid4()
    verify_scope = f"llm:{work_item_id}:verify:0"
    enrich_scope = f"llm:{work_item_id}:enrich:entities"

    assert verify_scope != enrich_scope
    llm_metering._assert_caller_scope(verify_scope)
    for operation in llm_metering.ENRICH_OPERATIONS:
        assert f"llm:{work_item_id}:enrich:{operation}" != verify_scope


# =====================================================================
# Gate 13.8 — triage
# =====================================================================


def test_above_threshold_auto_approves_and_writes_values_through(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory(extracted_entities={"vendor": "stale"})
    verification = _verification(db_session, tenant, work_item)
    consensus = dv.derive_consensus(
        [{"vendor": "Acme", "total": "10"}, {"vendor": "Acme", "total": "10"}]
    )

    status = dv.triage(
        db_session,
        verification=verification,
        consensus=consensus,
        work_item=work_item,
    )
    db_session.commit()

    assert status is VerificationStatus.AGREED
    assert verification.auto_approved is True
    assert verification.reviewed_by_user_id is None
    assert work_item.extracted_entities["vendor"] == "Acme"


def test_below_threshold_disagrees_and_blocks(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _verification(db_session, tenant, work_item)
    consensus = dv.derive_consensus([{"total": "1"}, {"total": "999"}])

    status = dv.triage(
        db_session, verification=verification, consensus=consensus, work_item=work_item
    )
    db_session.commit()

    assert status is VerificationStatus.DISAGREED
    assert verification.auto_approved is False
    assert verification.blocks_automation is True

    blocking = dv.blocking_verification(db_session, work_item_id=work_item.id)
    assert blocking is not None and blocking.id == verification.id


def test_threshold_is_configurable(
    db_session, tenant, work_item_factory, monkeypatch
) -> None:
    work_item = work_item_factory()
    db_session.commit()
    consensus = dv.derive_consensus([{"a": "1", "b": "2"}, {"a": "1", "b": "9"}])
    assert consensus.confidence == Decimal("0.7500")

    monkeypatch.setattr(settings, "AUTOMATION_AUTO_APPROVE_THRESHOLD", 0.70)
    v1 = _verification(db_session, tenant, work_item)
    assert dv.triage(
        db_session, verification=v1, consensus=consensus, work_item=work_item
    ) is VerificationStatus.AUTO_APPROVED

    db_session.delete(v1)
    db_session.commit()

    monkeypatch.setattr(settings, "AUTOMATION_AUTO_APPROVE_THRESHOLD", 0.85)
    v2 = _verification(db_session, tenant, work_item)
    assert dv.triage(
        db_session, verification=v2, consensus=consensus, work_item=work_item
    ) is VerificationStatus.DISAGREED


def test_only_one_open_verification_per_work_item(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    _verification(db_session, tenant, work_item, status=VerificationStatus.DISAGREED)
    db_session.commit()

    with pytest.raises(IntegrityError):
        _verification(
            db_session, tenant, work_item, status=VerificationStatus.DISAGREED
        )
        db_session.flush()
    db_session.rollback()


def test_reviewed_status_requires_a_reviewer(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _verification(db_session, tenant, work_item)
    db_session.commit()

    from sqlalchemy import text

    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(
            text(
                "UPDATE document_verifications SET status = 'REVIEWED', "
                "agreement_score = 0.5 WHERE id = :id"
            ),
            {"id": verification.id},
        )
        db_session.flush()
    assert "reviewer" in str(excinfo.value) or "rev" in str(excinfo.value)
    db_session.rollback()


# =====================================================================
# Gate 13.8 — resolve
# =====================================================================


def _disagreed(db, tenant, work_item):
    verification = _verification(db, tenant, work_item)
    consensus = dv.derive_consensus(
        [{"total": "1250.00", "vendor": "Acme"}, {"total": "9999.00", "vendor": "Acme"}]
    )
    for field_consensus in consensus.fields:
        db.add(field_consensus.as_row(verification.id))
    dv.triage(db, verification=verification, consensus=consensus, work_item=work_item)
    db.commit()
    db.refresh(verification)
    return verification


def test_resolve_records_reviewer_and_chosen_values(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _disagreed(db_session, tenant, work_item)
    assert verification.status is VerificationStatus.DISAGREED

    dv.resolve(
        db_session,
        verification=verification,
        chosen={"total": "1250.00"},
        reviewer_user_id=tenant.contributor.user.id,
    )
    db_session.commit()

    assert verification.status is VerificationStatus.REVIEWED
    assert verification.reviewed_by_user_id == tenant.contributor.user.id
    assert verification.reviewed_at is not None
    assert verification.auto_approved is False
    assert work_item.extracted_entities["total"] == "1250.00"
    assert work_item.extracted_entities["vendor"] == "Acme"

    row = db_session.execute(
        select(DocumentVerificationField).where(
            DocumentVerificationField.verification_id == verification.id,
            DocumentVerificationField.field_path == "total",
        )
    ).scalar_one()
    assert row.resolved_value == "1250.00"
    assert row.consensus_value is not None


def test_partial_resolve_is_refused(db_session, tenant, work_item_factory) -> None:
    work_item = work_item_factory()
    consensus = dv.derive_consensus(
        [{"a": "1", "b": "2"}, {"a": "9", "b": "8"}]
    )
    verification = _verification(db_session, tenant, work_item)
    for field_consensus in consensus.fields:
        db_session.add(field_consensus.as_row(verification.id))
    dv.triage(
        db_session, verification=verification, consensus=consensus, work_item=work_item
    )
    db_session.commit()
    db_session.refresh(verification)

    with pytest.raises(dv.VerificationError, match="still unresolved"):
        dv.resolve(
            db_session,
            verification=verification,
            chosen={"a": "1"},
            reviewer_user_id=tenant.contributor.user.id,
        )


def test_resolving_an_agreed_field_is_refused(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _disagreed(db_session, tenant, work_item)

    with pytest.raises(dv.VerificationError, match="not in disagreement"):
        dv.resolve(
            db_session,
            verification=verification,
            chosen={"total": "1250.00", "vendor": "Hacked"},
            reviewer_user_id=tenant.contributor.user.id,
        )


def test_resolving_a_non_disagreed_verification_is_refused(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _verification(db_session, tenant, work_item)
    consensus = dv.derive_consensus([{"a": "1"}, {"a": "1"}])
    dv.triage(
        db_session, verification=verification, consensus=consensus, work_item=work_item
    )
    db_session.commit()

    with pytest.raises(dv.VerificationError, match="only a DISAGREED"):
        dv.resolve(
            db_session,
            verification=verification,
            chosen={"a": "1"},
            reviewer_user_id=tenant.contributor.user.id,
        )


def test_releasing_event_is_emitted_on_resolve(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _disagreed(db_session, tenant, work_item)
    dv.resolve(
        db_session,
        verification=verification,
        chosen={"total": "1250.00"},
        reviewer_user_id=tenant.contributor.user.id,
    )
    event = dv.emit_outcome(db_session, verification=verification)
    db_session.commit()

    assert event.event_type == "work_item.verification_completed"
    assert event.visibility == "INTERNAL"
    assert event.resource_id == work_item.id


def test_disagreed_emits_the_blocking_event(
    db_session, tenant, work_item_factory
) -> None:
    work_item = work_item_factory()
    verification = _disagreed(db_session, tenant, work_item)
    event = dv.emit_outcome(db_session, verification=verification)
    db_session.commit()
    assert event.event_type == "work_item.verification_disagreed"