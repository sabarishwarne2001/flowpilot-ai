"""ARCH-20 — service-layer tests for erasure, residency and export."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models.assistant import Conversation, ConversationMessage
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.billing_account import BillingAccount
from app.models.compliance import (
    AUDIT_RETENTION_FLOOR_DAYS,
    DATA_RESIDENCY_REGION_VALUES,
    EXPORT_COMPLETE,
    REGION_EU,
    REGION_GLOBAL,
    ComplianceExport,
    ErasedSubject,
    RetentionPolicy,
    erased_email_for,
)
from app.models.organization import MembershipStatus, OrganizationMember
from app.models.uploaded_file import UploadedFile
from app.models.user import User
from app.models.user_session import AuthMethod, UserSession
from app.models.work_item import WorkItem
from app.services.compliance import erasure_service, export_service, residency_service


def _subject(tenant):
    return tenant.contributor.user


def _seed_content(db, tenant, subject: User) -> dict[str, uuid.UUID]:
    work_item = WorkItem(
        workspace_id=tenant.workspace.id,
        created_by_user_id=subject.id,
        original_filename="Jane-Doe-passport.pdf",
        stored_filename=f"stored-{uuid.uuid4().hex}",
        file_type="application/pdf",
        file_size=2048,
        extracted_text="Passport number 123456789, Jane Doe, born 1990.",
        summary="Identity document for Jane Doe.",
    )
    db.add(work_item)
    db.flush()

    conversation = Conversation(
        workspace_id=tenant.workspace.id,
        user_id=subject.id,
        title="About my passport",
    )
    db.add(conversation)
    db.flush()

    db.add(
        ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content="What is my passport number?",
        )
    )
    db.add(
        AuthToken(
            user_id=subject.id,
            purpose=AuthTokenPurpose.PASSWORD_RESET,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.add(
        UserSession(
            user_id=subject.id,
            family_id=uuid.uuid4(),
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            authenticated_at=datetime.now(timezone.utc),
            auth_method=AuthMethod.PASSWORD.value,
        )
    )
    uploaded = UploadedFile(
        owner_id=subject.id,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        file_path=f"{tenant.organization.id}/documents/{uuid.uuid4()}.pdf",
        original_filename="Jane-Doe-passport.pdf",
        mime_type="application/pdf",
        file_size=2048,
        checksum_sha256="0" * 64,
    )
    db.add(uploaded)

    db.execute(
        text(
            "INSERT INTO document_chunks "
            "(id, workspace_id, organization_id, work_item_id, chunk_index, "
            " content, token_count, embedding, embedding_model) "
            "VALUES (gen_random_uuid(), :ws, :org, :wi, 0, :content, 12, "
            "        :embedding, 'test-model')"
        ),
        {
            "ws": tenant.workspace.id,
            "org": tenant.organization.id,
            "wi": work_item.id,
            "content": "Passport number 123456789, Jane Doe.",
            "embedding": "[" + ",".join(["0.0"] * 384) + "]",
        },
    )
    db.commit()

    return {"work_item_id": work_item.id, "conversation_id": conversation.id}


def test_residency_vocabulary_is_closed():
    assert DATA_RESIDENCY_REGION_VALUES == ("US", "EU", "APAC", "GLOBAL")


def test_audit_retention_floor_is_the_trigger_value():
    assert AUDIT_RETENTION_FLOOR_DAYS == 400


def test_erased_email_is_deterministic_and_unique_per_user():
    left, right = uuid.uuid4(), uuid.uuid4()
    assert erased_email_for(left) == erased_email_for(left)
    assert erased_email_for(left) != erased_email_for(right)
    assert erased_email_for(left).endswith("@erased.invalid")


def test_email_hash_is_case_and_whitespace_insensitive():
    assert erasure_service.email_hash("  Jane@Example.COM ") == (
        erasure_service.email_hash("jane@example.com")
    )
    assert len(erasure_service.email_hash("a@b.com")) == 64


def test_global_region_resolves_to_the_process_default_driver(monkeypatch):
    residency_service.reset_regional_drivers()
    from app.core import storage

    driver = residency_service.driver_for_region(REGION_GLOBAL)
    assert driver is storage.get_storage_driver()


def test_unknown_region_is_refused():
    with pytest.raises(residency_service.UnknownRegionError):
        residency_service.driver_for_region("ANTARCTICA")


def test_pinned_region_without_a_bucket_refuses_rather_than_falling_back(monkeypatch):
    residency_service.reset_regional_drivers()
    monkeypatch.setattr(
        residency_service.settings, "S3_REGIONAL_BUCKETS", {}, raising=False
    )
    with pytest.raises(residency_service.ResidencyNotConfiguredError):
        residency_service.driver_for_region(REGION_EU)


def test_unknown_region_in_config_is_dropped_not_honoured(monkeypatch):
    monkeypatch.setattr(
        residency_service.settings,
        "S3_REGIONAL_BUCKETS",
        {"EU": "fp-eu", "MARS": "fp-mars", "APAC": "   "},
        raising=False,
    )
    mapping = residency_service.regional_bucket_map()
    assert mapping == {"EU": "fp-eu"}


def test_set_region_returns_the_previous_value(db_session, tenant):
    previous = residency_service.set_organization_region(
        db_session,
        organization=tenant.organization,
        region=REGION_GLOBAL,
        verify_backend=False,
    )
    db_session.commit()
    assert previous == REGION_GLOBAL
    assert tenant.organization.data_residency_region == REGION_GLOBAL


def test_repin_does_not_move_existing_objects(db_session, tenant):
    record = ComplianceExport(
        organization_id=tenant.organization.id,
        status=EXPORT_COMPLETE,
        storage_key="key",
        residency_region=REGION_GLOBAL,
    )
    db_session.add(record)
    db_session.commit()

    residency_service.set_organization_region(
        db_session,
        organization=tenant.organization,
        region=REGION_GLOBAL,
        verify_backend=False,
    )
    db_session.commit()
    db_session.refresh(record)
    assert record.residency_region == REGION_GLOBAL


def test_preview_counts_without_destroying(db_session, tenant):
    subject = _subject(tenant)
    _seed_content(db_session, tenant, subject)

    counts = erasure_service.preview_subject(
        db_session,
        organization_id=tenant.organization.id,
        subject_user_id=subject.id,
    )
    assert counts["work_items"] == 1
    assert counts["conversations"] == 1
    assert counts["conversation_messages"] == 1
    assert counts["document_chunks"] == 1

    db_session.refresh(subject)
    assert subject.is_active is True
    assert "@erased.invalid" not in subject.email


def test_erasure_destroys_content_and_anonymises_the_user(db_session, tenant):
    subject = _subject(tenant)
    seeded = _seed_content(db_session, tenant, subject)
    subject_id = subject.id

    result = erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject_id,
        erasure_ticket="DSAR-2026-001",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    assert result.already_erased is False
    assert result.counts["document_chunks"] == 1
    assert result.counts["conversations"] == 1

    work_item = db_session.get(WorkItem, seeded["work_item_id"])
    assert work_item.extracted_text is None
    assert work_item.summary is None
    assert work_item.original_filename == erasure_service.PLACEHOLDER_FILENAME

    assert (
        db_session.execute(
            text("SELECT count(*) FROM document_chunks WHERE work_item_id = :wi"),
            {"wi": seeded["work_item_id"]},
        ).scalar_one()
        == 0
    )
    assert db_session.get(Conversation, seeded["conversation_id"]) is None

    refreshed = db_session.get(User, subject_id)
    assert refreshed is not None
    assert refreshed.email == erased_email_for(subject_id)
    assert refreshed.display_name is None
    assert refreshed.is_active is False
    assert refreshed.sessions_revoked_at is not None


def test_erasure_revokes_credentials(db_session, tenant):
    subject = _subject(tenant)
    _seed_content(db_session, tenant, subject)

    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-002",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    assert (
        db_session.execute(
            text("SELECT count(*) FROM auth_tokens WHERE user_id = :uid"),
            {"uid": subject.id},
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                "SELECT count(*) FROM sessions "
                "WHERE user_id = :uid AND revoked_at IS NULL"
            ),
            {"uid": subject.id},
        ).scalar_one()
        == 0
    )


def test_uploaded_files_are_tombstoned_and_renamed(db_session, tenant):
    subject = _subject(tenant)
    _seed_content(db_session, tenant, subject)

    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-003",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    row = (
        db_session.query(UploadedFile)
        .filter(UploadedFile.owner_id == subject.id)
        .one()
    )
    assert row.deleted_at is not None
    assert row.original_filename == erasure_service.PLACEHOLDER_FILENAME


def test_membership_is_deactivated_not_removed(db_session, tenant):
    subject = _subject(tenant)
    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-004",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    membership = (
        db_session.query(OrganizationMember)
        .filter(
            OrganizationMember.organization_id == tenant.organization.id,
            OrganizationMember.user_id == subject.id,
        )
        .one()
    )
    assert membership.status == MembershipStatus.DEACTIVATED


def test_invoices_survive_erasure(db_session, tenant):
    subject = _subject(tenant)
    ba = db_session.execute(
        select(BillingAccount).where(BillingAccount.organization_id == tenant.organization.id)
    ).scalar_one_or_none()
    if ba is None:
        ba = BillingAccount(
            organization_id=tenant.organization.id,
            stripe_customer_id=f"cus_{uuid.uuid4().hex[:12]}",
            currency="USD",
            billing_email="billing@example.com",
        )
        db_session.add(ba)
        db_session.flush()

    before = db_session.execute(
        text("SELECT count(*) FROM invoices WHERE billing_account_id = :ba"),
        {"ba": ba.id},
    ).scalar_one()

    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-005",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    after = db_session.execute(
        text("SELECT count(*) FROM invoices WHERE billing_account_id = :ba"),
        {"ba": ba.id},
    ).scalar_one()
    assert after == before


def test_usage_events_survive_erasure(db_session, tenant):
    subject = _subject(tenant)
    before = db_session.execute(
        text("SELECT count(*) FROM usage_events WHERE organization_id = :org"),
        {"org": tenant.organization.id},
    ).scalar_one()

    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-006",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    after = db_session.execute(
        text("SELECT count(*) FROM usage_events WHERE organization_id = :org"),
        {"org": tenant.organization.id},
    ).scalar_one()
    assert after == before


def test_audit_attribution_survives_erasure(db_session, tenant):
    subject = _subject(tenant)
    db_session.execute(
        text(
            "INSERT INTO audit_logs (id, organization_id, actor_id, "
            "resource_type, action, outcome, created_at) "
            "VALUES (gen_random_uuid(), :org, :actor, 'USER', 'UPDATED', "
            "'ALLOWED', now())"
        ),
        {"org": tenant.organization.id, "actor": subject.id},
    )
    db_session.commit()

    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-007",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    attributed = db_session.execute(
        text("SELECT count(*) FROM audit_logs WHERE actor_id = :actor"),
        {"actor": subject.id},
    ).scalar_one()
    assert attributed >= 1


def test_owner_cannot_be_erased(db_session, tenant):
    with pytest.raises(erasure_service.SubjectProtectedError):
        erasure_service.erase_subject(
            db_session,
            organization=tenant.organization,
            subject_user_id=tenant.owner.user.id,
            erasure_ticket="DSAR-2026-008",
            actor_user_id=tenant.owner.user.id,
        )


def test_self_erasure_is_refused(db_session, tenant):
    with pytest.raises(erasure_service.SubjectProtectedError):
        erasure_service.erase_subject(
            db_session,
            organization=tenant.organization,
            subject_user_id=tenant.contributor.user.id,
            erasure_ticket="DSAR-2026-009",
            actor_user_id=tenant.contributor.user.id,
        )


def test_non_member_is_not_found(db_session, tenant):
    with pytest.raises(erasure_service.SubjectNotFoundError):
        erasure_service.erase_subject(
            db_session,
            organization=tenant.organization,
            subject_user_id=tenant.non_member.user.id,
            erasure_ticket="DSAR-2026-010",
            actor_user_id=tenant.owner.user.id,
        )


def test_second_erasure_is_idempotent(db_session, tenant):
    subject = _subject(tenant)
    first = erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-011",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    second = erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-011-repeat",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    assert second.already_erased is True
    assert second.erased_subject.id == first.erased_subject.id
    assert (
        db_session.query(ErasedSubject)
        .filter(ErasedSubject.organization_id == tenant.organization.id)
        .count()
        == 1
    )


def test_tombstone_records_evidence(db_session, tenant):
    subject = _subject(tenant)
    _seed_content(db_session, tenant, subject)

    result = erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=subject.id,
        erasure_ticket="DSAR-2026-012",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    details = result.erased_subject.details
    assert details["method"] == "OVERWRITE"
    assert details["user_row"] == "ANONYMISED_IN_PLACE"
    assert set(details["preserved_tables"]) == set(
        erasure_service.PRESERVED_FINANCIAL_TABLES
    )
    assert "caveat" in details
    assert len(result.erased_subject.subject_email_hash) == 64


def test_erasure_is_scoped_to_one_tenant(db_session, tenant):
    foreign_item = WorkItem(
        workspace_id=tenant.foreign_workspace.id,
        created_by_user_id=tenant.other_org_member.user.id,
        original_filename="beta-contract.pdf",
        stored_filename=f"stored-{uuid.uuid4().hex}",
        file_type="application/pdf",
        file_size=100,
        extracted_text="Beta Ltd confidential.",
    )
    db_session.add(foreign_item)
    db_session.commit()

    erasure_service.erase_subject(
        db_session,
        organization=tenant.organization,
        subject_user_id=tenant.contributor.user.id,
        erasure_ticket="DSAR-2026-013",
        actor_user_id=tenant.owner.user.id,
    )
    db_session.commit()

    db_session.refresh(foreign_item)
    assert foreign_item.extracted_text == "Beta Ltd confidential."


def test_bundle_contains_every_expected_section(db_session, tenant):
    bundle = export_service.build_bundle(
        db_session, organization=tenant.organization
    )
    assert bundle["bundle_version"] == export_service.BUNDLE_VERSION
    assert bundle["organization"]["slug"] == tenant.organization.slug
    for section in (
        "members",
        "workspaces",
        "work_items",
        "conversations",
        "conversation_messages",
        "audit_logs",
        "invoices",
        "erased_subjects",
    ):
        assert section in bundle["sections"]


def test_bundle_is_scoped_to_the_requesting_tenant(db_session, tenant):
    bundle = export_service.build_bundle(
        db_session, organization=tenant.organization
    )
    slugs = {row["slug"] for row in bundle["sections"]["workspaces"]}
    assert slugs == {tenant.workspace.slug}


def test_bundle_zips_without_touching_the_filesystem(db_session, tenant):
    payload = export_service._zip_bytes(
        export_service.build_bundle(db_session, organization=tenant.organization)
    )
    assert isinstance(payload, bytes) and payload
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "data/members.json" in names


def test_request_export_starts_pending(db_session, tenant):
    record = export_service.request_export(
        db_session,
        organization=tenant.organization,
        requested_by_user_id=tenant.owner.user.id,
    )
    db_session.commit()
    assert record.status == "PENDING"
    assert record.storage_key is None
    assert record.is_downloadable is False


def test_download_url_is_refused_for_an_incomplete_export(db_session, tenant):
    record = export_service.request_export(
        db_session,
        organization=tenant.organization,
        requested_by_user_id=tenant.owner.user.id,
    )
    db_session.commit()
    with pytest.raises(export_service.ExportNotReadyError):
        export_service.download_url_for(tenant.organization, record)


def test_download_url_is_refused_after_expiry(db_session, tenant):
    record = ComplianceExport(
        organization_id=tenant.organization.id,
        status=EXPORT_COMPLETE,
        storage_key="some/key.zip",
        residency_region=REGION_GLOBAL,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(record)
    db_session.commit()
    with pytest.raises(export_service.ExportNotReadyError):
        export_service.download_url_for(tenant.organization, record)


def test_expire_stale_exports_is_dry_run_by_default(db_session, tenant):
    record = ComplianceExport(
        organization_id=tenant.organization.id,
        status=EXPORT_COMPLETE,
        storage_key="some/key.zip",
        residency_region=REGION_GLOBAL,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(record)
    db_session.commit()

    counted = export_service.expire_stale_exports(
        db_session, organization_id=tenant.organization.id, apply=False
    )
    db_session.refresh(record)
    assert counted == 1
    assert record.status == EXPORT_COMPLETE


def test_audit_retention_below_the_floor_is_rejected_by_the_database(
    db_session, tenant
):
    policy = RetentionPolicy(
        organization_id=tenant.organization.id,
        audit_retention_days=90,
    )
    db_session.add(policy)
    with pytest.raises(Exception):
        db_session.commit()
    db_session.rollback()


def test_audit_retention_at_the_floor_is_accepted(db_session, tenant):
    policy = RetentionPolicy(
        organization_id=tenant.organization.id,
        audit_retention_days=AUDIT_RETENTION_FLOOR_DAYS,
        work_item_retention_days=90,
        auto_purge_enabled=False,
    )
    db_session.add(policy)
    db_session.commit()
    assert policy.auto_purge_enabled is False


def test_one_retention_policy_per_organization(db_session, tenant):
    db_session.add(RetentionPolicy(organization_id=tenant.organization.id))
    db_session.commit()
    db_session.add(RetentionPolicy(organization_id=tenant.organization.id))
    with pytest.raises(Exception):
        db_session.commit()
    db_session.rollback()
