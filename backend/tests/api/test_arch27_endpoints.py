"""ARCH-27 endpoint tests — role gating, book scoping, marketplace admission.

WHAT THESE TESTS ARE FOR, AND WHAT THEY ARE NOT FOR
===================================================

The arithmetic lives in tests/services/test_partner_services.py and needs no
HTTP. What needs HTTP is the part that only exists at the boundary:

  * a non-member gets 404 rather than 403, so the endpoint is not a partner
    enumeration oracle;
  * an ANALYST can read the ledger and cannot touch the book;
  * a reseller cannot write their own share percentage or seal their own
    statement — those are `require_superadmin`;
  * marketplace reads are ADMIN and every marketplace write is OWNER;
  * a manifest outside a tenant's visibility is 404, not 403.

WHY THE SIGNATURE TESTS GO THROUGH THE REAL CRYPTO
==================================================

`publish_manifest` is exercised with a genuine Ed25519 keypair generated in
the test, not a monkeypatched verifier. A mocked signature check passes
against a service that never verifies anything, which is the precise failure
these tests exist to catch.
"""

from __future__ import annotations

import base64
import uuid
from datetime import date, datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.models.partner import (
    Partner,
    PartnerMember,
    PartnerRevShareAgreement,
)
from app.services.partner import marketplace_service

PARTNERS = "/api/v1/partners"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def keypair() -> tuple[ed25519.Ed25519PrivateKey, str]:
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


@pytest.fixture()
def partner(db_session: Session, tenant) -> Partner:
    """A partner whose operating org is the fixture's OTHER organization.

    Deliberately not `tenant.organization`: that one is the client tenant in
    these tests, and a partner may not hold its own operating organization in
    its own book. Using the same org for both would make the self-dealing
    guard fire on every assignment and mask everything else.
    """
    suffix = uuid.uuid4().hex[:8]
    owner_org = Organization(
        slug=f"reseller-{suffix}", name="Reseller Ltd", status=OrganizationStatus.ACTIVE
    )
    db_session.add(owner_org)
    db_session.flush()

    row = Partner(
        slug=f"reseller-{suffix}",
        name="Reseller Ltd",
        status="ACTIVE",
        owner_organization_id=owner_org.id,
    )
    db_session.add(row)
    db_session.flush()

    db_session.add_all(
        [
            PartnerMember(
                partner_id=row.id, user_id=tenant.owner.user.id, role="OWNER",
                status="ACTIVE",
            ),
            PartnerMember(
                partner_id=row.id, user_id=tenant.viewer.user.id, role="ANALYST",
                status="ACTIVE",
            ),
        ]
    )
    db_session.add(
        PartnerRevShareAgreement(
            partner_id=row.id,
            name="Standard",
            basis="GROSS_MARGIN",
            share_bps=2_000,
            currency="USD",
            unknown_cost_basis_policy="EXCLUDE",
            effective_from=date(2026, 1, 1),
            status="ACTIVE",
        )
    )
    db_session.commit()
    db_session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Partner access control
# ---------------------------------------------------------------------------


class TestPartnerAccessControl:
    def test_non_member_gets_404_not_403(
        self, client: TestClient, tenant, partner
    ) -> None:
        """404 so the endpoint is not a partner enumeration oracle.

        The set of resellers on a platform is commercially sensitive. A 403
        confirms the partner id exists, which is the disclosure the status
        code choice exists to prevent.
        """
        response = client.get(
            f"{PARTNERS}/{partner.id}", headers=tenant.non_member.headers
        )
        assert response.status_code == 404

    def test_member_can_read_the_partner(
        self, client: TestClient, tenant, partner
    ) -> None:
        response = client.get(
            f"{PARTNERS}/{partner.id}", headers=tenant.owner.headers
        )
        assert response.status_code == 200
        assert response.json()["slug"] == partner.slug

    def test_list_returns_only_my_partners(
        self, client: TestClient, tenant, partner
    ) -> None:
        mine = client.get(PARTNERS, headers=tenant.owner.headers)
        assert mine.status_code == 200
        assert [row["id"] for row in mine.json()] == [str(partner.id)]

        theirs = client.get(PARTNERS, headers=tenant.non_member.headers)
        assert theirs.status_code == 200
        assert theirs.json() == []

    def test_analyst_cannot_manage_the_book(
        self, client: TestClient, tenant, partner
    ) -> None:
        """ANALYST reads the ledger and changes nothing."""
        response = client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.viewer.headers,
            json={"organization_id": str(tenant.organization.id)},
        )
        assert response.status_code == 403

    def test_analyst_can_read_the_book(
        self, client: TestClient, tenant, partner
    ) -> None:
        response = client.get(
            f"{PARTNERS}/{partner.id}/book", headers=tenant.viewer.headers
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Book of business — invariants 1 and 2
# ---------------------------------------------------------------------------


class TestBookOfBusiness:
    def test_assign_then_read_back(
        self, client: TestClient, tenant, partner
    ) -> None:
        created = client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(tenant.organization.id)},
        )
        assert created.status_code == 201
        assert created.json()["organization_id"] == str(tenant.organization.id)

        book = client.get(
            f"{PARTNERS}/{partner.id}/book", headers=tenant.owner.headers
        )
        assert [row["organization_id"] for row in book.json()] == [
            str(tenant.organization.id)
        ]

    def test_partner_cannot_assign_its_own_operating_org(
        self, client: TestClient, tenant, partner
    ) -> None:
        response = client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(partner.owner_organization_id)},
        )
        assert response.status_code == 409
        assert "own book" in response.json()["detail"]

    def test_release_frees_the_organization_for_reassignment(
        self, client: TestClient, tenant, partner
    ) -> None:
        """The partial unique index is on ACTIVE rows only, so this works."""
        client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(tenant.organization.id)},
        )
        released = client.delete(
            f"{PARTNERS}/{partner.id}/book/{tenant.organization.id}",
            headers=tenant.owner.headers,
        )
        assert released.status_code == 204

        reassigned = client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(tenant.organization.id)},
        )
        assert reassigned.status_code == 201

    def test_second_partner_cannot_claim_an_assigned_organization(
        self, client: TestClient, db_session: Session, tenant, partner
    ) -> None:
        """Invariant 2 at the boundary."""
        client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(tenant.organization.id)},
        )

        suffix = uuid.uuid4().hex[:8]
        rival_org = Organization(
            slug=f"rival-{suffix}", name="Rival", status=OrganizationStatus.ACTIVE
        )
        db_session.add(rival_org)
        db_session.flush()
        rival = Partner(
            slug=f"rival-{suffix}",
            name="Rival",
            status="ACTIVE",
            owner_organization_id=rival_org.id,
        )
        db_session.add(rival)
        db_session.flush()
        db_session.add(
            PartnerMember(
                partner_id=rival.id,
                user_id=tenant.owner.user.id,
                role="OWNER",
                status="ACTIVE",
            )
        )
        db_session.commit()

        response = client.post(
            f"{PARTNERS}/{rival.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(tenant.organization.id)},
        )
        assert response.status_code == 409
        assert "at most one active partner" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Commercial operations are platform-gated
# ---------------------------------------------------------------------------


class TestPlatformGating:
    def test_partner_owner_cannot_write_their_own_agreement(
        self, client: TestClient, tenant, partner
    ) -> None:
        """A reseller who can set their own share has the cheque book."""
        response = client.post(
            f"{PARTNERS}/{partner.id}/agreements",
            headers=tenant.owner.headers,
            json={
                "name": "Generous",
                "basis": "GROSS_MARGIN",
                "share_bps": 9_500,
                "effective_from": "2026-01-01",
            },
        )
        assert response.status_code == 404  # require_superadmin returns 404

    def test_partner_owner_cannot_seal_their_own_statement(
        self, client: TestClient, tenant, partner
    ) -> None:
        response = client.post(
            f"{PARTNERS}/{partner.id}/payouts/{uuid.uuid4()}/seal",
            headers=tenant.owner.headers,
            json={},
        )
        assert response.status_code == 404

    def test_partner_admin_may_recompute_a_draft(
        self, client: TestClient, tenant, partner
    ) -> None:
        """A DRAFT is not a promise, so refreshing it is partner-gated."""
        client.post(
            f"{PARTNERS}/{partner.id}/book",
            headers=tenant.owner.headers,
            json={"organization_id": str(tenant.organization.id)},
        )
        response = client.post(
            f"{PARTNERS}/{partner.id}/payouts",
            headers=tenant.owner.headers,
            json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "DRAFT"
        # No usage in the window, so nothing is owed and nothing is unknown.
        assert body["gross_revenue_micros"] == 0
        assert body["payout_micros"] == 0
        # Invariant 4 is on the wire even for an empty period.
        assert body["zero_byok_revenue_micros"] == 0
        # Unknown stays unknown: no priced bucket means no margin figure.
        assert body["supplier_cost_micros"] is None
        assert body["margin_micros"] is None


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


class TestPayoutStatements:
    def test_draft_statement_reports_digest_mismatch_without_claiming_tampering(
        self, client: TestClient, tenant, partner
    ) -> None:
        created = client.post(
            f"{PARTNERS}/{partner.id}/payouts",
            headers=tenant.owner.headers,
            json={"period_start": "2026-06-01", "period_end": "2026-06-30"},
        )
        period_id = created.json()["id"]

        statement = client.get(
            f"{PARTNERS}/{partner.id}/payouts/{period_id}",
            headers=tenant.owner.headers,
        )
        assert statement.status_code == 200
        body = statement.json()
        assert body["period"]["status"] == "DRAFT"
        assert body["period"]["content_digest"] == ""
        # A draft has no digest, so it never "matches". The console labels it
        # unsealed rather than tampered.
        assert body["digest_matches"] is False
        assert body["recomputed_digest"].startswith("sha256:")

    def test_statement_of_another_partner_is_404(
        self, client: TestClient, tenant, partner
    ) -> None:
        response = client.get(
            f"{PARTNERS}/{partner.id}/payouts/{uuid.uuid4()}",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 404

    def test_economics_summary_is_readable_by_an_analyst(
        self, client: TestClient, tenant, partner
    ) -> None:
        response = client.get(
            f"{PARTNERS}/{partner.id}/economics", headers=tenant.viewer.headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["sealed_period_count"] == 0
        # None, not 0. A lifetime margin nobody computed is not a margin of nil.
        assert body["lifetime_margin_micros"] is None


# ---------------------------------------------------------------------------
# Signing keys
# ---------------------------------------------------------------------------


class TestSigningKeys:
    def test_register_and_list(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        _private, pem = keypair
        created = client.post(
            f"{PARTNERS}/{partner.id}/signing-keys",
            headers=tenant.owner.headers,
            json={"key_id": "primary", "algorithm": "ED25519", "public_key_pem": pem},
        )
        assert created.status_code == 201
        assert created.json()["fingerprint"].startswith("sha256:")
        # No response field can carry key material beyond the public half.
        assert "private_key" not in created.json()

        listed = client.get(
            f"{PARTNERS}/{partner.id}/signing-keys", headers=tenant.owner.headers
        )
        assert [row["key_id"] for row in listed.json()] == ["primary"]

    def test_a_private_key_pem_is_refused_at_the_boundary(
        self, client: TestClient, tenant, partner
    ) -> None:
        private = ed25519.Ed25519PrivateKey.generate()
        pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        response = client.post(
            f"{PARTNERS}/{partner.id}/signing-keys",
            headers=tenant.owner.headers,
            json={"key_id": "oops", "algorithm": "ED25519", "public_key_pem": pem},
        )
        assert response.status_code == 422

    def test_declared_algorithm_must_match_the_key(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        _private, pem = keypair
        response = client.post(
            f"{PARTNERS}/{partner.id}/signing-keys",
            headers=tenant.owner.headers,
            json={
                "key_id": "mismatched",
                "algorithm": "RSA_PSS_SHA256",
                "public_key_pem": pem,
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Marketplace — invariants 5 and 6
# ---------------------------------------------------------------------------


def _publish(client: TestClient, tenant, partner, keypair, *, version="1.0.0"):
    private, pem = keypair
    client.post(
        f"{PARTNERS}/{partner.id}/signing-keys",
        headers=tenant.owner.headers,
        json={"key_id": "primary", "algorithm": "ED25519", "public_key_pem": pem},
    )
    item = client.post(
        f"{PARTNERS}/{partner.id}/catalog",
        headers=tenant.owner.headers,
        json={
            "slug": "invoice-triage",
            "name": "Invoice triage",
            "visibility": "PUBLIC",
        },
    ).json()

    nodes = [
        {"node_key": "start", "node_type": "trigger", "config": {"event": "document.created"}},
        {
            "node_key": "notify",
            "node_type": "action",
            "config": {"action_type": "notify", "recipient": "ap@example.com"},
        },
    ]
    edges = [{"from_node_key": "start", "to_node_key": "notify", "branch": "default"}]
    digest = marketplace_service.manifest_digest(nodes, edges)
    signature = base64.b64encode(private.sign(digest.encode("ascii"))).decode("ascii")

    published = client.post(
        f"{PARTNERS}/{partner.id}/catalog/{item['id']}/manifests",
        headers=tenant.owner.headers,
        json={
            "version": version,
            "nodes": nodes,
            "edges": edges,
            "signing_key_id": "primary",
            "signature": signature,
        },
    )
    return item, published, nodes, edges


class TestMarketplacePublishing:
    def test_a_correctly_signed_manifest_publishes(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        _item, published, _nodes, _edges = _publish(
            client, tenant, partner, keypair
        )
        assert published.status_code == 201
        body = published.json()
        assert body["content_digest"].startswith("sha256:")
        assert body["node_count"] == 2
        assert len(body["signatures"]) == 1

    def test_a_forged_signature_is_refused(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        _private, pem = keypair
        client.post(
            f"{PARTNERS}/{partner.id}/signing-keys",
            headers=tenant.owner.headers,
            json={"key_id": "primary", "algorithm": "ED25519", "public_key_pem": pem},
        )
        item = client.post(
            f"{PARTNERS}/{partner.id}/catalog",
            headers=tenant.owner.headers,
            json={"slug": "forged", "name": "Forged", "visibility": "PUBLIC"},
        ).json()

        other = ed25519.Ed25519PrivateKey.generate()
        nodes = [
            {"node_key": "start", "node_type": "trigger", "config": {}},
        ]
        digest = marketplace_service.manifest_digest(nodes, [])
        signature = base64.b64encode(other.sign(digest.encode("ascii"))).decode("ascii")

        response = client.post(
            f"{PARTNERS}/{partner.id}/catalog/{item['id']}/manifests",
            headers=tenant.owner.headers,
            json={
                "version": "1.0.0",
                "nodes": nodes,
                "edges": [],
                "signing_key_id": "primary",
                "signature": signature,
            },
        )
        assert response.status_code == 400
        assert "does not verify" in response.json()["detail"]

    def test_a_cyclic_manifest_is_refused_before_any_signature_check(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        """Invariant 6. Malformed is the more actionable message, so it wins."""
        _private, pem = keypair
        client.post(
            f"{PARTNERS}/{partner.id}/signing-keys",
            headers=tenant.owner.headers,
            json={"key_id": "primary", "algorithm": "ED25519", "public_key_pem": pem},
        )
        item = client.post(
            f"{PARTNERS}/{partner.id}/catalog",
            headers=tenant.owner.headers,
            json={"slug": "cyclic", "name": "Cyclic", "visibility": "PUBLIC"},
        ).json()

        response = client.post(
            f"{PARTNERS}/{partner.id}/catalog/{item['id']}/manifests",
            headers=tenant.owner.headers,
            json={
                "version": "1.0.0",
                "nodes": [
                    {"node_key": "a", "node_type": "trigger", "config": {}},
                    {"node_key": "b", "node_type": "action", "config": {}},
                ],
                "edges": [
                    {"from_node_key": "a", "to_node_key": "b", "branch": "default"},
                    {"from_node_key": "b", "to_node_key": "a", "branch": "default"},
                ],
                "signing_key_id": "primary",
                "signature": base64.b64encode(b"x" * 64).decode("ascii"),
            },
        )
        assert response.status_code == 422


class TestMarketplaceInstallation:
    def _catalog_url(self, tenant) -> str:
        return f"/api/v1/organizations/{tenant.organization.id}/marketplace"

    def test_admin_can_browse_and_owner_can_install(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        _item, published, _nodes, _edges = _publish(
            client, tenant, partner, keypair
        )
        manifest_id = published.json()["id"]
        base = self._catalog_url(tenant)

        catalog = client.get(f"{base}/catalog", headers=tenant.org_admin.headers)
        assert catalog.status_code == 200
        assert any(row["id"] for row in catalog.json())

        inspected = client.get(
            f"{base}/manifests/{manifest_id}", headers=tenant.org_admin.headers
        )
        assert inspected.status_code == 200
        assert inspected.json()["signature_verified"] is True
        assert inspected.json()["verified_key_fingerprint"].startswith("sha256:")

        installed = client.post(
            f"{base}/installations",
            headers=tenant.owner.headers,
            json={
                "manifest_id": manifest_id,
                "workspace_id": str(tenant.workspace.id),
            },
        )
        assert installed.status_code == 201
        body = installed.json()
        assert body["verified_signature_id"]
        assert body["automation_rule_id"]

    def test_admin_cannot_install(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        """Admitting third-party executable code is an ownership decision."""
        _item, published, _n, _e = _publish(client, tenant, partner, keypair)
        response = client.post(
            f"{self._catalog_url(tenant)}/installations",
            headers=tenant.org_admin.headers,
            json={
                "manifest_id": published.json()["id"],
                "workspace_id": str(tenant.workspace.id),
            },
        )
        assert response.status_code == 403

    def test_install_defaults_to_disabled(
        self, client: TestClient, db_session: Session, tenant, partner, keypair
    ) -> None:
        """Third-party code does not start firing on live documents unasked."""
        from app.models.automation import AutomationRule

        _item, published, _n, _e = _publish(client, tenant, partner, keypair)
        installed = client.post(
            f"{self._catalog_url(tenant)}/installations",
            headers=tenant.owner.headers,
            json={
                "manifest_id": published.json()["id"],
                "workspace_id": str(tenant.workspace.id),
            },
        ).json()

        rule = db_session.get(
            AutomationRule, uuid.UUID(installed["automation_rule_id"])
        )
        assert rule is not None
        assert rule.is_active is False

    def test_installing_into_a_foreign_workspace_is_404(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        _item, published, _n, _e = _publish(client, tenant, partner, keypair)
        response = client.post(
            f"{self._catalog_url(tenant)}/installations",
            headers=tenant.owner.headers,
            json={
                "manifest_id": published.json()["id"],
                "workspace_id": str(tenant.foreign_workspace.id),
            },
        )
        assert response.status_code == 404

    def test_unknown_manifest_is_404_not_403(
        self, client: TestClient, tenant
    ) -> None:
        response = client.get(
            f"{self._catalog_url(tenant)}/manifests/{uuid.uuid4()}",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 404

    def test_partner_only_item_is_invisible_to_an_unaffiliated_tenant(
        self, client: TestClient, tenant, partner, keypair
    ) -> None:
        """PARTNER_ONLY catalog entries encode a reseller's client logic."""
        private, pem = keypair
        client.post(
            f"{PARTNERS}/{partner.id}/signing-keys",
            headers=tenant.owner.headers,
            json={"key_id": "primary", "algorithm": "ED25519", "public_key_pem": pem},
        )
        item = client.post(
            f"{PARTNERS}/{partner.id}/catalog",
            headers=tenant.owner.headers,
            json={
                "slug": "bespoke",
                "name": "Bespoke",
                "visibility": "PARTNER_ONLY",
            },
        ).json()

        nodes = [{"node_key": "start", "node_type": "trigger", "config": {}}]
        digest = marketplace_service.manifest_digest(nodes, [])
        client.post(
            f"{PARTNERS}/{partner.id}/catalog/{item['id']}/manifests",
            headers=tenant.owner.headers,
            json={
                "version": "1.0.0",
                "nodes": nodes,
                "edges": [],
                "signing_key_id": "primary",
                "signature": base64.b64encode(
                    private.sign(digest.encode("ascii"))
                ).decode("ascii"),
            },
        )

        # tenant.organization is not in this partner's book.
        catalog = client.get(
            f"{self._catalog_url(tenant)}/catalog", headers=tenant.org_admin.headers
        )
        assert catalog.status_code == 200
        assert item["id"] not in [row["id"] for row in catalog.json()]

    def test_uninstall_deactivates_the_rule_without_deleting_it(
        self, client: TestClient, db_session: Session, tenant, partner, keypair
    ) -> None:
        from app.models.automation import AutomationRule

        _item, published, _n, _e = _publish(client, tenant, partner, keypair)
        installed = client.post(
            f"{self._catalog_url(tenant)}/installations",
            headers=tenant.owner.headers,
            json={
                "manifest_id": published.json()["id"],
                "workspace_id": str(tenant.workspace.id),
            },
        ).json()

        removed = client.delete(
            f"{self._catalog_url(tenant)}/installations/{installed['id']}",
            headers=tenant.owner.headers,
        )
        assert removed.status_code == 204

        rule = db_session.get(
            AutomationRule, uuid.UUID(installed["automation_rule_id"])
        )
        # Deactivated, not deleted: the execution history outlives the decision
        # to uninstall.
        assert rule is not None
        assert rule.is_active is False