"""ARCH-27 service tests — rev-share arithmetic, ZERO_BYOK, signatures, scoping.

WHY MOST OF THIS FILE NEEDS NO DATABASE
=======================================

The classification, the payout arithmetic, the canonical digest and the
signature verification are all pure functions over values. Testing them
against a live PostgreSQL would make the suite slower, flakier and no more
convincing — and it would hide the fact that these are the pieces that must be
reproducible on any machine, which is precisely invariant 3.

The tests that DO need a database are the ones whose subject IS the database:
the exclusive-tenancy index, the seal trigger, and the NOT NULL that carries
invariant 5. Those are marked `@pytest.mark.usefixtures("db_session")` and
skip cleanly when Postgres is unreachable, matching the ARCH-25 and ARCH-26
suites.

WHAT THE ARITHMETIC TESTS ARE ACTUALLY DEFENDING
================================================

`test_unknown_cost_basis_pays_nothing` is the one to keep if you keep only
one. A bucket with a partial cost basis gives an upper bound on margin, and
the whole ARCH-18/ARCH-24 lineage exists because somebody eventually prices a
contract off an upper bound treated as a fact. Here the consequence is a
cheque to a reseller for margin the platform never made.
"""

from __future__ import annotations

import base64
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from app.models.partner import (
    BPS_DENOMINATOR,
    PartnerRevShareAgreement,
    RevShareBasisClass,
)
from app.services.partner import marketplace_service, rev_share_service
from app.services.partner.marketplace_service import (
    ManifestValidationError,
    SignatureVerificationError,
)

ZERO_BYOK = RevShareBasisClass.ZERO_BYOK.value
SUPPLIER_COST = RevShareBasisClass.SUPPLIER_COST.value
UNKNOWN = RevShareBasisClass.UNKNOWN_COST_BASIS.value


# ===========================================================================
# Helpers
# ===========================================================================


def rollup(
    *,
    cost_micros: int = 1_000_000,
    cost_basis_micros: int | None = 400_000,
    unknown_events: int = 0,
    mix: dict[str, int] | None = None,
    event_count: int = 10,
    organization_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """A UsageRollup-shaped value.

    A namespace rather than a real model instance: `classify_rollup` reads
    four attributes and nothing else, and constructing a mapped object would
    imply a database relationship these tests deliberately do not have.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        cost_micros=cost_micros,
        cost_basis_micros=cost_basis_micros,
        unknown_cost_basis_event_count=unknown_events,
        cost_basis_source_mix=mix,
        event_count=event_count,
    )


def agreement(
    *,
    basis: str = "GROSS_MARGIN",
    share_bps: int = 2_000,
    zero_byok_share_bps: int | None = None,
    minimum_payout_micros: int = 0,
    policy: str = "EXCLUDE",
) -> PartnerRevShareAgreement:
    return PartnerRevShareAgreement(
        partner_id=uuid.uuid4(),
        name="Standard reseller",
        basis=basis,
        share_bps=share_bps,
        zero_byok_share_bps=zero_byok_share_bps,
        currency="USD",
        minimum_payout_micros=minimum_payout_micros,
        unknown_cost_basis_policy=policy,
        effective_from=date(2026, 1, 1),
        status="ACTIVE",
    )


def ed25519_keypair() -> tuple[ed25519.Ed25519PrivateKey, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private, pem


def sign_ed25519(private: ed25519.Ed25519PrivateKey, digest: str) -> str:
    return base64.b64encode(private.sign(digest.encode("ascii"))).decode("ascii")


def simple_manifest() -> tuple[list[dict], list[dict]]:
    nodes = [
        {"node_key": "start", "node_type": "trigger", "config": {"event": "document.created"}},
        {"node_key": "notify", "node_type": "action", "config": {"action_type": "notify", "recipient": "ops@example.com"}},
    ]
    edges = [{"from_node_key": "start", "to_node_key": "notify", "branch": "default"}]
    return nodes, edges


# ===========================================================================
# Classification — invariant 4
# ===========================================================================


class TestClassification:
    def test_complete_basis_with_supplier_cost(self) -> None:
        row = rollup(cost_basis_micros=400_000, mix={"SUPPLIER_RATE_CARD": 10})
        assert rev_share_service.classify_rollup(row) == SUPPLIER_COST

    def test_zero_byok_is_its_own_class(self) -> None:
        """A tenant paying the supplier directly costs us nothing.

        `cost_basis_micros == 0` is a KNOWN cost, not a missing one, and the
        classifier must not confuse the two.
        """
        row = rollup(cost_basis_micros=0, mix={"ZERO_BYOK": 10})
        assert rev_share_service.classify_rollup(row) == ZERO_BYOK

    def test_null_basis_is_unknown_not_free(self) -> None:
        row = rollup(cost_basis_micros=None, mix=None)
        assert rev_share_service.classify_rollup(row) == UNKNOWN

    def test_partial_basis_is_unknown(self) -> None:
        """40% of the bucket unpriced means the margin is an upper bound."""
        row = rollup(cost_basis_micros=400_000, unknown_events=4, mix={"MEASURED": 6})
        assert rev_share_service.classify_rollup(row) == UNKNOWN

    def test_partially_unpriced_byok_is_unknown_not_full_margin(self) -> None:
        """The ordering test. This is the inversion that would overpay.

        A bucket whose priced events were all ZERO_BYOK but which also carries
        unpriced events looks, to a careless classifier, like 100% margin. It
        is not: the unpriced events may have cost anything.
        """
        row = rollup(cost_basis_micros=0, unknown_events=3, mix={"ZERO_BYOK": 7})
        assert rev_share_service.classify_rollup(row) == UNKNOWN

    def test_mixed_sources_are_supplier_cost(self) -> None:
        row = rollup(cost_basis_micros=250_000, mix={"ZERO_BYOK": 5, "MEASURED": 5})
        assert rev_share_service.classify_rollup(row) == SUPPLIER_COST

    def test_empty_mix_with_zero_cost_is_supplier_cost(self) -> None:
        """Conservative on the ambiguous case.

        A zero basis with no source attribution could be BYOK or could be a
        bug in the metering path. Classifying it ZERO_BYOK would pay a
        reseller 100%-margin rates on a defect.
        """
        row = rollup(cost_basis_micros=0, mix={})
        assert rev_share_service.classify_rollup(row) == SUPPLIER_COST


# ===========================================================================
# Payout arithmetic
# ===========================================================================


class TestPayoutArithmetic:
    def test_gross_margin_share_is_integer_floor(self) -> None:
        share_bps, payout = rev_share_service._payout_for(
            agreement=agreement(share_bps=2_000),
            basis_class=SUPPLIER_COST,
            revenue_micros=1_000_000,
            margin_micros=600_000,
        )
        assert share_bps == 2_000
        assert payout == (600_000 * 2_000) // BPS_DENOMINATOR == 120_000

    def test_net_revenue_basis_ignores_cost(self) -> None:
        _, payout = rev_share_service._payout_for(
            agreement=agreement(basis="NET_REVENUE", share_bps=1_000),
            basis_class=SUPPLIER_COST,
            revenue_micros=1_000_000,
            margin_micros=600_000,
        )
        assert payout == 100_000

    def test_unknown_cost_basis_pays_nothing(self) -> None:
        """The one to keep if you keep only one.

        No share percentage, no basis, no policy produces a payout on revenue
        whose supplier cost is unknown. Paying on an upper bound is the defect
        the entire ARCH-18/24/27 lineage exists to prevent.
        """
        for basis in ("GROSS_MARGIN", "NET_REVENUE"):
            share_bps, payout = rev_share_service._payout_for(
                agreement=agreement(basis=basis, share_bps=10_000),
                basis_class=UNKNOWN,
                revenue_micros=9_999_999,
                margin_micros=None,
            )
            assert payout == 0, basis
            assert share_bps == 0, basis

    def test_zero_byok_uses_its_own_rate_when_set(self) -> None:
        share_bps, payout = rev_share_service._payout_for(
            agreement=agreement(share_bps=2_000, zero_byok_share_bps=500),
            basis_class=ZERO_BYOK,
            revenue_micros=1_000_000,
            margin_micros=1_000_000,
        )
        assert share_bps == 500
        assert payout == 50_000

    def test_zero_byok_falls_back_to_the_standard_rate(self) -> None:
        share_bps, payout = rev_share_service._payout_for(
            agreement=agreement(share_bps=2_000, zero_byok_share_bps=None),
            basis_class=ZERO_BYOK,
            revenue_micros=1_000_000,
            margin_micros=1_000_000,
        )
        assert share_bps == 2_000
        assert payout == 200_000

    def test_negative_margin_does_not_produce_a_negative_payout(self) -> None:
        """A loss-making tenant is visible in margin, not clawed back here."""
        _, payout = rev_share_service._payout_for(
            agreement=agreement(share_bps=2_000),
            basis_class=SUPPLIER_COST,
            revenue_micros=100_000,
            margin_micros=-50_000,
        )
        assert payout == 0

    def test_rate_for_never_returns_a_rate_for_unknown(self) -> None:
        assert agreement(share_bps=10_000).rate_for(UNKNOWN) == 0


# ===========================================================================
# Bucket accumulation
# ===========================================================================


class TestBucketAccumulation:
    def test_absorbing_zero_basis_keeps_a_known_zero(self) -> None:
        """0 is a value; None is an absence. Accumulation must not merge them."""
        bucket = rev_share_service._Bucket(
            organization_id=uuid.uuid4(), basis_class=ZERO_BYOK
        )
        bucket.absorb(rollup(cost_basis_micros=0, cost_micros=500_000, mix={"ZERO_BYOK": 5}))
        bucket.absorb(rollup(cost_basis_micros=0, cost_micros=500_000, mix={"ZERO_BYOK": 5}))
        assert bucket.supplier_cost_micros == 0
        assert bucket.revenue_micros == 1_000_000
        assert bucket.margin_micros == 1_000_000

    def test_unknown_bucket_reports_no_margin(self) -> None:
        bucket = rev_share_service._Bucket(
            organization_id=uuid.uuid4(), basis_class=UNKNOWN
        )
        bucket.absorb(rollup(cost_basis_micros=None, cost_micros=750_000, unknown_events=9))
        assert bucket.supplier_cost_micros is None
        assert bucket.margin_micros is None

    def test_source_mix_sums_across_rollups(self) -> None:
        bucket = rev_share_service._Bucket(
            organization_id=uuid.uuid4(), basis_class=SUPPLIER_COST
        )
        bucket.absorb(rollup(mix={"MEASURED": 3, "ZERO_BYOK": 1}))
        bucket.absorb(rollup(mix={"MEASURED": 2}))
        assert bucket.source_mix == {"MEASURED": 5, "ZERO_BYOK": 1}


# ===========================================================================
# Digest reproducibility — invariant 3
# ===========================================================================


class TestDigestReproducibility:
    def _period(self) -> SimpleNamespace:
        return SimpleNamespace(
            partner_id=uuid.uuid4(),
            agreement_id=uuid.uuid4(),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            currency="USD",
            gross_revenue_micros=1_000_000,
            supplier_cost_micros=400_000,
            margin_micros=600_000,
            payout_micros=120_000,
            carried_forward_micros=0,
            zero_byok_revenue_micros=0,
            zero_byok_margin_micros=0,
            zero_byok_payout_micros=0,
            excluded_revenue_micros=0,
            excluded_unknown_cost_basis_event_count=0,
            organization_count=1,
            source_rollup_count=1,
        )

    def _line(self, organization_id: uuid.UUID) -> SimpleNamespace:
        return SimpleNamespace(
            organization_id=organization_id,
            basis_class=SUPPLIER_COST,
            revenue_micros=1_000_000,
            supplier_cost_micros=400_000,
            margin_micros=600_000,
            share_bps=2_000,
            payout_micros=120_000,
            event_count=10,
            unknown_cost_basis_event_count=0,
            source_rollup_ids=["b", "a"],
        )

    def test_digest_is_stable_across_runs(self) -> None:
        period = self._period()
        lines = [self._line(uuid.uuid4())]
        assert rev_share_service.compute_digest(
            period, lines
        ) == rev_share_service.compute_digest(period, lines)

    def test_digest_is_independent_of_line_order(self) -> None:
        """Two orderings of the same statement are the same statement."""
        period = self._period()
        a, b = uuid.uuid4(), uuid.uuid4()
        forward = [self._line(a), self._line(b)]
        assert rev_share_service.compute_digest(
            period, forward
        ) == rev_share_service.compute_digest(period, list(reversed(forward)))

    def test_digest_changes_when_a_figure_changes(self) -> None:
        period = self._period()
        lines = [self._line(uuid.uuid4())]
        before = rev_share_service.compute_digest(period, lines)
        lines[0].payout_micros = 120_001
        assert rev_share_service.compute_digest(period, lines) != before

    def test_digest_excludes_timestamps(self) -> None:
        """A clock adjustment must not invalidate a settled statement."""
        payload = rev_share_service.canonical_payload(
            self._period(), [self._line(uuid.uuid4())]
        )
        assert "sealed_at" not in payload
        assert "paid_at" not in payload

    def test_null_supplier_cost_survives_into_the_payload(self) -> None:
        period = self._period()
        period.supplier_cost_micros = None
        period.margin_micros = None
        payload = rev_share_service.canonical_payload(period, [])
        assert payload["supplier_cost_micros"] is None
        assert payload["margin_micros"] is None

    def test_digest_shape_matches_the_column_constraint(self) -> None:
        digest = rev_share_service.compute_digest(self._period(), [])
        assert digest.startswith("sha256:")
        assert len(digest) == 71


# ===========================================================================
# Signature verification — invariant 5
# ===========================================================================


class TestSignatureVerification:
    def test_valid_ed25519_signature_verifies(self) -> None:
        private, pem = ed25519_keypair()
        nodes, edges = simple_manifest()
        digest = marketplace_service.manifest_digest(nodes, edges)
        assert marketplace_service.verify_signature(
            public_key_pem=pem,
            algorithm="ED25519",
            signature_b64=sign_ed25519(private, digest),
            digest=digest,
        )

    def test_tampered_manifest_fails_verification(self) -> None:
        """The signature covers the digest, so editing the DAG breaks it."""
        private, pem = ed25519_keypair()
        nodes, edges = simple_manifest()
        signature = sign_ed25519(
            private, marketplace_service.manifest_digest(nodes, edges)
        )
        nodes[1]["config"]["recipient"] = "attacker@example.com"
        assert not marketplace_service.verify_signature(
            public_key_pem=pem,
            algorithm="ED25519",
            signature_b64=signature,
            digest=marketplace_service.manifest_digest(nodes, edges),
        )

    def test_signature_from_another_key_fails(self) -> None:
        _, pem = ed25519_keypair()
        other_private, _ = ed25519_keypair()
        nodes, edges = simple_manifest()
        digest = marketplace_service.manifest_digest(nodes, edges)
        assert not marketplace_service.verify_signature(
            public_key_pem=pem,
            algorithm="ED25519",
            signature_b64=sign_ed25519(other_private, digest),
            digest=digest,
        )

    def test_rsa_pss_round_trip(self) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = (
            private.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        digest = marketplace_service.manifest_digest(*simple_manifest())
        signature = base64.b64encode(
            private.sign(
                digest.encode("ascii"),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        ).decode("ascii")
        assert marketplace_service.verify_signature(
            public_key_pem=pem,
            algorithm="RSA_PSS_SHA256",
            signature_b64=signature,
            digest=digest,
        )

    def test_declared_algorithm_must_match_the_key(self) -> None:
        _, pem = ed25519_keypair()
        with pytest.raises(SignatureVerificationError, match="claims"):
            marketplace_service.verify_signature(
                public_key_pem=pem,
                algorithm="RSA_PSS_SHA256",
                signature_b64=base64.b64encode(b"x" * 64).decode("ascii"),
                digest=marketplace_service.manifest_digest(*simple_manifest()),
            )

    def test_undersized_rsa_key_is_refused(self) -> None:
        private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pem = (
            private.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        with pytest.raises(SignatureVerificationError, match="2048"):
            marketplace_service.algorithm_for_key(pem)

    def test_a_private_key_pem_is_refused_outright(self) -> None:
        private = ed25519.Ed25519PrivateKey.generate()
        pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        with pytest.raises(SignatureVerificationError, match="compromised"):
            marketplace_service.load_public_key(pem)

    def test_fingerprint_ignores_pem_formatting(self) -> None:
        """The same key re-exported with different wrapping is the same key."""
        _, pem = ed25519_keypair()
        assert marketplace_service.fingerprint_public_key(
            pem
        ) == marketplace_service.fingerprint_public_key(pem.strip() + "\n\n")

    def test_non_base64_signature_raises_rather_than_returning_false(self) -> None:
        _, pem = ed25519_keypair()
        with pytest.raises(SignatureVerificationError, match="base64"):
            marketplace_service.verify_signature(
                public_key_pem=pem,
                algorithm="ED25519",
                signature_b64="not base64 !!",
                digest=marketplace_service.manifest_digest(*simple_manifest()),
            )


# ===========================================================================
# Canonicalisation
# ===========================================================================


class TestCanonicalManifest:
    def test_digest_is_independent_of_node_order(self) -> None:
        nodes, edges = simple_manifest()
        assert marketplace_service.manifest_digest(
            nodes, edges
        ) == marketplace_service.manifest_digest(list(reversed(nodes)), edges)

    def test_digest_changes_when_a_config_value_changes(self) -> None:
        nodes, edges = simple_manifest()
        before = marketplace_service.manifest_digest(nodes, edges)
        nodes[1]["config"]["recipient"] = "someone-else@example.com"
        assert marketplace_service.manifest_digest(nodes, edges) != before

    def test_canonical_form_survives_a_jsonb_round_trip(self) -> None:
        """JSONB preserves neither key order nor whitespace.

        Signing the submitted bytes rather than the canonical form would fail
        on the first read-back — at install time, in a tenant's console, for a
        manifest that verified fine when it was published.
        """
        import json

        nodes, edges = simple_manifest()
        canonical = marketplace_service.canonical_manifest(nodes, edges)
        round_tripped = json.loads(json.dumps(canonical, sort_keys=False))
        assert marketplace_service.manifest_digest(
            round_tripped["nodes"], round_tripped["edges"]
        ) == marketplace_service.manifest_digest(nodes, edges)


# ===========================================================================
# DAG validation — invariant 6
# ===========================================================================


class TestManifestLinting:
    def test_a_valid_graph_compiles(self) -> None:
        compiled = marketplace_service.lint_manifest(*simple_manifest())
        assert compiled.trigger_key == "start"

    def test_a_cycle_is_refused(self) -> None:
        nodes, edges = simple_manifest()
        edges.append(
            {"from_node_key": "notify", "to_node_key": "start", "branch": "default"}
        )
        with pytest.raises(ManifestValidationError):
            marketplace_service.lint_manifest(nodes, edges)

    def test_two_triggers_are_refused(self) -> None:
        nodes, edges = simple_manifest()
        nodes.append({"node_key": "second", "node_type": "trigger", "config": {}})
        with pytest.raises(ManifestValidationError):
            marketplace_service.lint_manifest(nodes, edges)

    def test_an_unreachable_node_is_refused(self) -> None:
        nodes, edges = simple_manifest()
        nodes.append({"node_key": "orphan", "node_type": "action", "config": {}})
        with pytest.raises(ManifestValidationError):
            marketplace_service.lint_manifest(nodes, edges)

    def test_r33_violation_is_refused(self) -> None:
        """A document must not choose who an action reaches."""
        nodes, edges = simple_manifest()
        nodes[1]["config"]["recipient"] = "{{document.extracted_email}}"
        with pytest.raises(ManifestValidationError, match="R33"):
            marketplace_service.lint_manifest(nodes, edges)

    def test_authored_literal_recipient_is_allowed(self) -> None:
        nodes, edges = simple_manifest()
        nodes[1]["config"]["recipient"] = "billing@acme.example"
        assert marketplace_service.lint_manifest(nodes, edges) is not None


# ===========================================================================
# Database-backed invariants
# ===========================================================================


@pytest.mark.usefixtures("db_session")
class TestDatabaseInvariants:
    """The invariants whose subject IS the database.

    These cannot be asserted in Python: a service-level check proves the
    service is careful today, and the point of a constraint is that it holds
    when the service is not.
    """

    def test_exclusive_tenancy_index_refuses_a_second_active_book(
        self, db_session
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from app.models.organization import Organization, OrganizationStatus
        from app.models.partner import Partner, PartnerOrganization

        suffix = uuid.uuid4().hex[:8]
        owner_a = Organization(slug=f"pa-{suffix}", name="A", status=OrganizationStatus.ACTIVE)
        owner_b = Organization(slug=f"pb-{suffix}", name="B", status=OrganizationStatus.ACTIVE)
        client = Organization(slug=f"pc-{suffix}", name="C", status=OrganizationStatus.ACTIVE)
        db_session.add_all([owner_a, owner_b, client])
        db_session.flush()

        first = Partner(slug=f"first-{suffix}", name="First", owner_organization_id=owner_a.id)
        second = Partner(slug=f"second-{suffix}", name="Second", owner_organization_id=owner_b.id)
        db_session.add_all([first, second])
        db_session.flush()

        db_session.add(
            PartnerOrganization(
                partner_id=first.id,
                organization_id=client.id,
                status="ACTIVE",
                effective_from=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        db_session.add(
            PartnerOrganization(
                partner_id=second.id,
                organization_id=client.id,
                status="ACTIVE",
                effective_from=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_unknown_cost_basis_line_cannot_carry_a_payout(
        self, db_session
    ) -> None:
        """The CHECK, not the service, is what makes this impossible."""
        from sqlalchemy.exc import IntegrityError

        from app.models.partner import PartnerRevShareLedger

        db_session.add(
            PartnerRevShareLedger(
                payout_period_id=uuid.uuid4(),
                partner_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                basis_class=UNKNOWN,
                revenue_micros=1_000_000,
                supplier_cost_micros=None,
                margin_micros=None,
                share_bps=2_000,
                payout_micros=200_000,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_zero_byok_line_must_be_full_margin(self, db_session) -> None:
        from sqlalchemy.exc import IntegrityError

        from app.models.partner import PartnerRevShareLedger

        db_session.add(
            PartnerRevShareLedger(
                payout_period_id=uuid.uuid4(),
                partner_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                basis_class=ZERO_BYOK,
                revenue_micros=1_000_000,
                supplier_cost_micros=1,
                margin_micros=999_999,
                share_bps=2_000,
                payout_micros=0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_sealed_period_refuses_a_figure_change(self, db_session) -> None:
        """The seal trigger, and the reason it subtracts an allow-list."""
        from sqlalchemy.exc import DatabaseError

        from app.models.organization import Organization, OrganizationStatus
        from app.models.partner import (
            Partner,
            PartnerPayoutPeriod,
            PartnerRevShareAgreement,
        )

        suffix = uuid.uuid4().hex[:8]
        org = Organization(slug=f"seal-{suffix}", name="Seal", status=OrganizationStatus.ACTIVE)
        db_session.add(org)
        db_session.flush()
        partner = Partner(slug=f"seal-{suffix}", name="Seal", owner_organization_id=org.id)
        db_session.add(partner)
        db_session.flush()
        terms = PartnerRevShareAgreement(
            partner_id=partner.id,
            name="Terms",
            basis="GROSS_MARGIN",
            share_bps=2_000,
            effective_from=date(2026, 1, 1),
            status="ACTIVE",
        )
        db_session.add(terms)
        db_session.flush()

        period = PartnerPayoutPeriod(
            partner_id=partner.id,
            agreement_id=terms.id,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            status="SEALED",
            gross_revenue_micros=1_000_000,
            payout_micros=120_000,
            content_digest="sha256:" + "0" * 64,
            sealed_at=datetime.now(timezone.utc),
        )
        db_session.add(period)
        db_session.flush()

        period.payout_micros = 999_999
        with pytest.raises(DatabaseError):
            db_session.flush()
        db_session.rollback()

    def test_marking_a_sealed_period_paid_is_still_allowed(self, db_session) -> None:
        """The allow-list has to actually allow something, or sealing is a wall."""
        from app.models.organization import Organization, OrganizationStatus
        from app.models.partner import (
            Partner,
            PartnerPayoutPeriod,
            PartnerRevShareAgreement,
        )

        suffix = uuid.uuid4().hex[:8]
        org = Organization(slug=f"paid-{suffix}", name="Paid", status=OrganizationStatus.ACTIVE)
        db_session.add(org)
        db_session.flush()
        partner = Partner(slug=f"paid-{suffix}", name="Paid", owner_organization_id=org.id)
        db_session.add(partner)
        db_session.flush()
        terms = PartnerRevShareAgreement(
            partner_id=partner.id,
            name="Terms",
            basis="GROSS_MARGIN",
            share_bps=2_000,
            effective_from=date(2026, 1, 1),
            status="ACTIVE",
        )
        db_session.add(terms)
        db_session.flush()

        period = PartnerPayoutPeriod(
            partner_id=partner.id,
            agreement_id=terms.id,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            status="SEALED",
            gross_revenue_micros=1_000_000,
            payout_micros=120_000,
            content_digest="sha256:" + "1" * 64,
            sealed_at=datetime.now(timezone.utc),
        )
        db_session.add(period)
        db_session.flush()

        period.status = "PAID"
        period.paid_at = datetime.now(timezone.utc)
        period.payment_reference = "wire-2026-08"
        db_session.flush()
        assert period.status == "PAID"
        db_session.rollback()

    def test_partner_cannot_hold_its_own_organization(self, db_session) -> None:
        from app.models.organization import Organization, OrganizationStatus
        from app.models.partner import Partner
        from app.services.partner import tenancy_service

        suffix = uuid.uuid4().hex[:8]
        org = Organization(slug=f"self-{suffix}", name="Self", status=OrganizationStatus.ACTIVE)
        db_session.add(org)
        db_session.flush()
        partner = Partner(slug=f"self-{suffix}", name="Self", owner_organization_id=org.id)
        db_session.add(partner)
        db_session.flush()

        with pytest.raises(tenancy_service.PartnerConflict, match="own book"):
            tenancy_service.assign_organization(
                db_session, partner=partner, organization_id=org.id
            )
        db_session.rollback()

    def test_book_scoping_excludes_a_foreign_organization(self, db_session) -> None:
        from app.models.organization import Organization, OrganizationStatus
        from app.models.partner import Partner
        from app.services.partner import tenancy_service

        suffix = uuid.uuid4().hex[:8]
        owner = Organization(slug=f"bs-{suffix}", name="Owner", status=OrganizationStatus.ACTIVE)
        mine = Organization(slug=f"bm-{suffix}", name="Mine", status=OrganizationStatus.ACTIVE)
        theirs = Organization(slug=f"bt-{suffix}", name="Theirs", status=OrganizationStatus.ACTIVE)
        db_session.add_all([owner, mine, theirs])
        db_session.flush()
        partner = Partner(slug=f"bs-{suffix}", name="Book", owner_organization_id=owner.id)
        db_session.add(partner)
        db_session.flush()

        tenancy_service.assign_organization(
            db_session, partner=partner, organization_id=mine.id
        )
        db_session.flush()

        book = tenancy_service.book_organization_ids(db_session, partner_id=partner.id)
        assert mine.id in book
        assert theirs.id not in book

        with pytest.raises(tenancy_service.PartnerNotFound):
            tenancy_service.assert_organization_in_book(
                db_session, partner_id=partner.id, organization_id=theirs.id
            )
        db_session.rollback()