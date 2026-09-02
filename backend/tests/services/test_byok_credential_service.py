"""ARCH-22 — credential encryption, tenant isolation and cost attribution.

The suite is organised around the three blocking findings the audit raised,
because those are the properties most likely to regress:

  B1  no client is shared between tenants, and no tenant key is cached
  B2  a provider that cannot be executed cannot be saved as a tenant-key route
  B3  ZERO_BYOK is stamped from what ran, not from what was intended

`TestZeroCogsAttribution` is the one that matters commercially. A silent zero
in `cost_basis_micros` reads downstream as a 100% gross margin, and ARCH-18
built four CHECK constraints to prevent it. This phase writes zeros there
deliberately, so every path that does needs a test that says why it is
allowed to.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.byok_providers import (
    BYOK_PROVIDER_VALUES,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_OPENAI,
    ROUTABLE_PROVIDERS,
    is_routable,
    spec_for,
)
from app.core.encryption import decrypt_password
from app.models.byok import TenantModelRoute, TenantProviderCredential
from app.models.supplier_cogs import HARD_COST_BASIS_SOURCES, SOURCE_ZERO_BYOK
from app.services.byok import credential_service, model_routing_service
from app.services.byok.credential_service import (
    CredentialError,
    CredentialNotFoundError,
    ValidationOutcome,
)
from app.services.byok.provider_clients import (
    SOURCE_PLATFORM,
    SOURCE_TENANT,
    CredentialUse,
    FallbackForbiddenError,
    ProviderClientFactory,
)
from tests.conftest import Fixture

GROQ_KEY = "gsk_" + "a" * 48
GROQ_KEY_2 = "gsk_" + "b" * 48
GEMINI_KEY = "AIza" + "c" * 35


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------


class TestEncryptionAtRest:
    def test_key_is_not_stored_in_plaintext(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()

        assert GROQ_KEY not in credential.encrypted_api_key
        assert credential.encrypted_api_key.startswith("gAAAAA")
        assert decrypt_password(credential.encrypted_api_key) == GROQ_KEY

    def test_round_trip_through_the_service(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()
        assert credential_service.decrypt_for_use(credential) == GROQ_KEY

    def test_fingerprint_is_stable_and_not_reversible(self) -> None:
        first = credential_service.fingerprint(GROQ_KEY)
        second = credential_service.fingerprint(GROQ_KEY)
        assert first == second
        assert len(first) == 12
        assert first not in GROQ_KEY
        assert credential_service.fingerprint(GROQ_KEY_2) != first

    def test_ciphertext_fits_the_column(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        """A 300-char key is the documented ceiling; it must still fit in 512."""
        longest = "gsk_" + "x" * 296
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=longest,
        )
        db_session.commit()
        assert len(credential.encrypted_api_key) <= 512

    def test_over_length_key_is_refused_with_an_explanation(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        with pytest.raises(CredentialError, match="maximum"):
            credential_service.upsert_credential(
                db_session,
                organization_id=tenant.organization.id,
                provider=PROVIDER_GROQ,
                plaintext_key="gsk_" + "x" * 400,
            )

    def test_wrong_prefix_is_caught_before_the_provider_sees_it(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        with pytest.raises(CredentialError, match="begins with"):
            credential_service.upsert_credential(
                db_session,
                organization_id=tenant.organization.id,
                provider=PROVIDER_GROQ,
                plaintext_key="sk-ant-not-a-groq-key",
            )

    def test_provider_error_text_is_scrubbed_of_the_key(self) -> None:
        message = credential_service._scrub(
            f"401 Unauthorized: key {GROQ_KEY} is revoked", GROQ_KEY
        )
        assert GROQ_KEY not in message
        assert "redacted" in message


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_a_credential_is_never_visible_to_another_tenant(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()

        foreign_org_id = tenant.foreign_workspace.organization_id
        assert (
            credential_service.resolve_active(
                db_session,
                organization_id=foreign_org_id,
                provider=PROVIDER_GROQ,
            )
            is None
        )
        assert (
            credential_service.list_for_organization(
                db_session, organization_id=foreign_org_id
            )
            == []
        )

    def test_two_tenants_hold_independent_keys_for_one_provider(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        foreign_org_id = tenant.foreign_workspace.organization_id

        mine = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        theirs = credential_service.upsert_credential(
            db_session,
            organization_id=foreign_org_id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY_2,
        )
        db_session.commit()

        assert mine.id != theirs.id
        assert credential_service.decrypt_for_use(mine) == GROQ_KEY
        assert credential_service.decrypt_for_use(theirs) == GROQ_KEY_2

    def test_only_one_active_credential_per_provider(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()

        db_session.add(
            TenantProviderCredential(
                id=uuid.uuid4(),
                organization_id=tenant.organization.id,
                provider=PROVIDER_GROQ,
                encrypted_api_key="gAAAAAduplicate",
                key_version=1,
                key_fingerprint="deadbeefcafe",
                key_last_four="aaaa",
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_deactivated_credentials_do_not_block_a_new_one(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        """The partial index exists so rotation keeps an audit trail."""
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()
        credential_service.deactivate(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
        )
        db_session.commit()

        replacement = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY_2,
        )
        db_session.commit()
        assert replacement.is_active is True

        history = (
            db_session.query(TenantProviderCredential)
            .filter_by(organization_id=tenant.organization.id)
            .count()
        )
        assert history == 2, "the retired credential must survive as a trail"


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


class TestRotation:
    def test_rotation_bumps_the_version_and_clears_validation(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        credential_service.record_validation(
            db_session,
            credential=credential,
            outcome=ValidationOutcome(
                ok=True,
                latency_ms=42,
                error=None,
                checked_at=datetime.now(timezone.utc),
            ),
        )
        db_session.commit()
        assert credential.last_validated_at is not None

        rotated = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY_2,
        )
        db_session.commit()

        assert rotated.id == credential.id
        assert rotated.key_version == 2
        assert credential_service.decrypt_for_use(rotated) == GROQ_KEY_2
        assert rotated.last_validated_at is None, (
            "a rotated key inherits no validation state; a green badge for a "
            "key nobody has proved works is worse than no badge"
        )

    def test_rotation_does_not_reopen_a_closed_fallback(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
            allow_platform_fallback=False,
        )
        db_session.commit()

        rotated = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY_2,
        )
        db_session.commit()
        assert rotated.allow_platform_fallback is False

    def test_fallback_defaults_to_off(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()
        assert credential.allow_platform_fallback is False


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_six_providers_stored_one_routable(self) -> None:
        assert len(BYOK_PROVIDER_VALUES) == 6
        assert ROUTABLE_PROVIDERS == {PROVIDER_GROQ}

    def test_gemini_is_stored_but_not_routable(self) -> None:
        spec = spec_for(PROVIDER_GEMINI)
        assert spec.is_routable is False
        assert spec.unroutable_reason
        assert "genai.configure" in spec.unroutable_reason, (
            "the reason must name the process-global hazard, or the next "
            "reader will assume it is an arbitrary restriction and lift it"
        )

    def test_unroutable_providers_have_no_adapter(self) -> None:
        from app.services.byok.provider_clients import has_adapter

        for provider in BYOK_PROVIDER_VALUES:
            assert has_adapter(provider) == is_routable(provider)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestModelRouting:
    def test_a_tenant_key_rule_on_an_unroutable_provider_is_refused(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        with pytest.raises(model_routing_service.UnroutableProviderError):
            model_routing_service.upsert_route(
                db_session,
                organization_id=tenant.organization.id,
                task_type="ASSISTANT",
                provider=PROVIDER_OPENAI,
                model_name="gpt-4o",
                use_tenant_key=True,
            )

    def test_the_same_provider_is_allowed_on_the_platform_key(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        route = model_routing_service.upsert_route(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
            provider=PROVIDER_OPENAI,
            model_name="gpt-4o",
            use_tenant_key=False,
        )
        db_session.commit()
        assert route.use_tenant_key is False

    def test_one_rule_per_task(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        model_routing_service.upsert_route(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
            provider=PROVIDER_GROQ,
            model_name="llama-3.3-70b-versatile",
            use_tenant_key=False,
        )
        db_session.commit()

        db_session.add(
            TenantModelRoute(
                id=uuid.uuid4(),
                organization_id=tenant.organization.id,
                task_type="ASSISTANT",
                provider=PROVIDER_GROQ,
                model_name="llama-3.1-8b-instant",
                use_tenant_key=False,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_no_rule_falls_through_to_ai_settings(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        class Settings:
            class provider:  # noqa: N801
                value = "GROQ"

            model = "llama-3.1-8b-instant"

        decision = model_routing_service.resolve(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
            ai_settings=Settings(),
        )
        assert decision.origin == "ai_settings_default"
        assert decision.use_tenant_key is False
        assert decision.model_name == "llama-3.1-8b-instant"

    def test_a_rule_without_a_credential_downgrades_rather_than_fails(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        model_routing_service.upsert_route(
            db_session,
            organization_id=tenant.organization.id,
            task_type="EXTRACTION",
            provider=PROVIDER_GROQ,
            model_name="llama-3.3-70b-versatile",
            use_tenant_key=True,
        )
        db_session.commit()

        decision = model_routing_service.resolve(
            db_session,
            organization_id=tenant.organization.id,
            task_type="EXTRACTION",
        )
        assert decision.use_tenant_key is False
        assert decision.downgrade_reason == "no_tenant_credential_configured"

    def test_a_credential_with_a_failed_validation_is_not_used(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        credential_service.record_validation(
            db_session,
            credential=credential,
            outcome=ValidationOutcome(
                ok=False,
                latency_ms=15,
                error="401 Unauthorized",
                checked_at=datetime.now(timezone.utc),
            ),
        )
        model_routing_service.upsert_route(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
            provider=PROVIDER_GROQ,
            model_name="llama-3.3-70b-versatile",
            use_tenant_key=True,
        )
        db_session.commit()

        decision = model_routing_service.resolve(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
        )
        assert decision.use_tenant_key is False
        assert (
            decision.downgrade_reason
            == "tenant_credential_last_validation_failed"
        )

    def test_a_valid_credential_routes_on_the_tenant_key(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        model_routing_service.upsert_route(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
            provider=PROVIDER_GROQ,
            model_name="llama-3.3-70b-versatile",
            use_tenant_key=True,
        )
        db_session.commit()

        decision = model_routing_service.resolve(
            db_session,
            organization_id=tenant.organization.id,
            task_type="ASSISTANT",
        )
        assert decision.use_tenant_key is True
        assert decision.downgrade_reason is None


# ---------------------------------------------------------------------------
# The client factory (B1)
# ---------------------------------------------------------------------------


class TestProviderClientFactory:
    def test_each_call_gets_its_own_client(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()

        first, use_a = ProviderClientFactory.build(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
        )
        second, use_b = ProviderClientFactory.build(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
        )

        assert first is not second, (
            "a shared instance is the singleton defect returning"
        )
        assert use_a.source == use_b.source == SOURCE_TENANT

    def test_the_singleton_is_never_mutated(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        from app.services.llm_service import llm_service

        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()
        before = llm_service._groq_client

        client, _ = ProviderClientFactory.build(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
        )

        assert llm_service._groq_client is before
        assert llm_service._groq_client is not client

    def test_no_credential_falls_through_to_the_platform(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        _, use = ProviderClientFactory.build(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
        )
        assert use.source == SOURCE_PLATFORM
        assert use.reason == "no_tenant_credential_configured"
        assert use.is_zero_cogs is False

    def test_fallback_is_refused_without_consent(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
            allow_platform_fallback=False,
        )
        db_session.commit()

        with pytest.raises(FallbackForbiddenError, match="not enabled"):
            ProviderClientFactory.fallback_to_platform(
                db_session,
                organization_id=tenant.organization.id,
                provider=PROVIDER_GROQ,
                cause="rate_limited",
            )

    def test_permitted_fallback_produces_a_platform_receipt(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
            allow_platform_fallback=True,
        )
        db_session.commit()

        _, use = ProviderClientFactory.fallback_to_platform(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            cause="rate_limited",
        )
        assert use.source == SOURCE_PLATFORM
        assert use.fell_back is True
        assert use.is_zero_cogs is False, (
            "a fallback is billed to FlowPilot's supplier account; stamping it "
            "ZERO_BYOK would report real COGS as free"
        )


# ---------------------------------------------------------------------------
# Zero-COGS attribution (B3)
# ---------------------------------------------------------------------------


class TestZeroCogsAttribution:
    """The financial control. ARCH-18 §COGS meets ARCH-22 §3.3."""

    @staticmethod
    def _reservation(credential_use) -> object:
        from app.services.llm_metering import LLMReservation

        reservation = LLMReservation(
            organization_id=uuid.uuid4(),
            workspace_id=None,
            scope="llm:test",
            resource_type="CONVERSATION",
            resource_id=uuid.uuid4(),
            estimated_input_tokens=10,
            max_output_tokens=100,
        )
        if credential_use is not None:
            reservation.attach_credential_use(credential_use)
        return reservation

    def test_zero_byok_is_a_hard_cost_basis_source(self) -> None:
        """ARCH-18 already classifies it, so BYOK margin is truthfully 100%."""
        assert SOURCE_ZERO_BYOK in HARD_COST_BASIS_SOURCES

    def test_a_tenant_key_call_is_stamped_zero(self) -> None:
        from app.services.llm_metering import _byok_applies

        use = CredentialUse(
            source=SOURCE_TENANT,
            provider=PROVIDER_GROQ,
            organization_id=uuid.uuid4(),
            key_fingerprint="abc123",
        )
        applies, reason = _byok_applies(
            self._reservation(use), settled_provider="groq"
        )
        assert applies is True
        assert reason is None

    def test_no_receipt_means_the_platform_paid(self) -> None:
        from app.services.llm_metering import _byok_applies

        applies, _ = _byok_applies(
            self._reservation(None), settled_provider="groq"
        )
        assert applies is False

    def test_a_platform_receipt_is_never_zeroed(self) -> None:
        from app.services.llm_metering import _byok_applies

        use = CredentialUse(
            source=SOURCE_PLATFORM,
            provider=PROVIDER_GROQ,
            organization_id=uuid.uuid4(),
            reason="no_tenant_credential_configured",
        )
        applies, _ = _byok_applies(
            self._reservation(use), settled_provider="groq"
        )
        assert applies is False

    def test_failover_to_another_provider_is_reattributed(self) -> None:
        """B3, exactly. The reserved key did not serve the call.

        Without this branch a tenant reserved on Groq whose request failed
        over to the platform's Gemini account would record
        cost_basis_micros=0 / ZERO_BYOK on tokens FlowPilot actually paid a
        supplier for — a 100% gross margin on real spend, passing every CHECK
        constraint because the pair is internally consistent.
        """
        from app.services.llm_metering import _byok_applies

        use = CredentialUse(
            source=SOURCE_TENANT,
            provider=PROVIDER_GROQ,
            organization_id=uuid.uuid4(),
            key_fingerprint="abc123",
        )
        applies, reason = _byok_applies(
            self._reservation(use), settled_provider="gemini"
        )
        assert applies is False
        assert reason is not None
        assert "mismatch" in reason

    def test_a_fallback_receipt_is_never_zeroed(self) -> None:
        from app.services.llm_metering import _byok_applies

        use = CredentialUse(
            source=SOURCE_PLATFORM,
            provider=PROVIDER_GROQ,
            organization_id=uuid.uuid4(),
            fell_back=True,
            reason="tenant_key_failed_fallback_permitted: rate_limited",
        )
        applies, _ = _byok_applies(
            self._reservation(use), settled_provider="groq"
        )
        assert applies is False

    def test_the_database_rejects_an_undeclared_zero(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        """ARCH-18's constraint still bites. Belt to the code's braces."""
        from app.services import usage_service

        with pytest.raises(Exception):
            usage_service.record_usage(
                db_session,
                organization_id=tenant.organization.id,
                event_type="llm.input_token",
                quantity=Decimal(10),
                cost_basis_micros=Decimal(0),
                cost_basis_source="SUPPLIER_RATE_CARD",
            )
        db_session.rollback()


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------


class TestCredentialStatus:
    def test_unroutable_outranks_active(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        """A valid Gemini key is still not serving the tenant's traffic."""
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GEMINI,
            plaintext_key=GEMINI_KEY,
        )
        credential_service.record_validation(
            db_session,
            credential=credential,
            outcome=ValidationOutcome(
                ok=True,
                latency_ms=30,
                error=None,
                checked_at=datetime.now(timezone.utc),
            ),
        )
        db_session.commit()
        assert credential.status == "UNROUTABLE"

    def test_a_validated_groq_key_is_active(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        credential_service.record_validation(
            db_session,
            credential=credential,
            outcome=ValidationOutcome(
                ok=True,
                latency_ms=30,
                error=None,
                checked_at=datetime.now(timezone.utc),
            ),
        )
        db_session.commit()
        assert credential.status == "ACTIVE"

    def test_a_never_validated_key_is_not_active(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()
        assert credential.status == "UNVALIDATED"

    def test_repr_leaks_nothing(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        credential = credential_service.upsert_credential(
            db_session,
            organization_id=tenant.organization.id,
            provider=PROVIDER_GROQ,
            plaintext_key=GROQ_KEY,
        )
        db_session.commit()
        rendered = repr(credential)
        assert GROQ_KEY not in rendered
        assert credential.encrypted_api_key not in rendered
        assert credential.key_fingerprint not in rendered


# ---------------------------------------------------------------------------
# Not-found handling
# ---------------------------------------------------------------------------


class TestMissingCredential:
    def test_fallback_policy_on_a_missing_credential(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        with pytest.raises(CredentialNotFoundError):
            credential_service.set_fallback_policy(
                db_session,
                organization_id=tenant.organization.id,
                provider=PROVIDER_GROQ,
                allow_platform_fallback=True,
            )

    def test_deactivating_a_missing_credential(
        self, db_session: Session, tenant: Fixture
    ) -> None:
        with pytest.raises(CredentialNotFoundError):
            credential_service.deactivate(
                db_session,
                organization_id=tenant.organization.id,
                provider=PROVIDER_GROQ,
            )