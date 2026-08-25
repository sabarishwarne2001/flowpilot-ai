"""ARCH-16 Gate 16.1 - 16.8 invariants."""

from __future__ import annotations

import datetime
from datetime import timezone, timedelta
import pytest

from app.models.identity import (
    DomainStatus, IpPinningMode, JitProvisioningMode,
    VerifiedDomain, EnterpriseIdpConfig
)
from app.models.organization import Organization
from app.services.identity import (
    domain_service, jit_service, scim_service, session_policy_service,
)
from app.services.identity.errors import (
    DomainPolicyRefused, ScimInvalidFilter,
)


def utc(**kw):
    return datetime.datetime.now(timezone.utc) + timedelta(**kw)


class TestGate162Domains:

    @pytest.mark.parametrize("domain", ["com", "co.uk", "github.io"])
    def test_public_suffix_refused_at_claim_time(self, db, domain):
        org = Organization(name="Test Org", slug=f"test-domain-org-{domain.replace('.', '-')}")
        db.add(org)
        db.flush()
        with pytest.raises(DomainPolicyRefused):
            domain_service.claim_domain(db, organization_id=org.id,
                                        raw_domain=domain, principal=None)

    @pytest.mark.parametrize("domain", ["gmail.com", "outlook.com", "proton.me"])
    def test_consumer_mail_domain_refused(self, db, domain):
        org = Organization(name="Test Org", slug=f"test-consumer-org-{domain.replace('.', '-')}")
        db.add(org)
        db.flush()
        with pytest.raises(DomainPolicyRefused):
            domain_service.claim_domain(db, organization_id=org.id,
                                        raw_domain=domain, principal=None)


class TestGate165JitSeatCap:

    def test_no_mapping_can_grant_owner(self, db):
        org = Organization(name="Test Org", slug="test-owner-map-org")
        db.add(org)
        db.flush()
        domain = VerifiedDomain(
            organization_id=org.id, domain="acme.test", status="VERIFIED",
            challenge_token="tok", challenge_expires_at=utc(days=1),
            first_verified_at=utc(days=-1), is_sso_binding=True,
        )
        db.add(domain)
        db.flush()
        config = EnterpriseIdpConfig(
            organization_id=org.id,
            verified_domain_id=domain.id,
            protocol="SAML2",
            display_name="SAML",
            is_active=True,
            idp_entity_id="https://idp.acme.test/saml/metadata",
            idp_sso_url="https://idp.acme.test/saml/sso",
            jit_default_org_role="MEMBER",
            jit_provisioning_mode=JitProvisioningMode.CAPPED,
            jit_seat_cap=25,
        )
        db.add(config)
        db.flush()
        role = jit_service.resolve_org_role(
            db, config=config, attributes={"groups": ["flowpilot-owners"]})
        assert role != "OWNER"


class TestGate166Scim:

    def test_start_index_is_one_based(self):
        query = scim_service.parse_query(filter_expr=None, start_index=1, count=100)
        assert query.start_index == 1
        assert query.offset == 0

    def test_unsupported_filter_errors(self):
        with pytest.raises(ScimInvalidFilter):
            scim_service.parse_query(
                filter_expr='title eq "Engineer"', start_index=1, count=100)

    def test_errors_use_the_scim_schema(self):
        from app.services.identity.errors import ScimNotFound
        body = ScimNotFound().to_body()
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert body["status"] == "404"


class TestGate168SessionPolicy:

    def test_untrusted_hop_count_refuses_to_derive_a_client_ip(self, monkeypatch):
        class S:
            TRUSTED_PROXY_HOPS = 2
        monkeypatch.setattr(session_policy_service, "get_settings", lambda: S())

        assert session_policy_service.resolve_client_ip(
            socket_ip="10.0.0.1", forwarded_for="1.2.3.4") is None

    def test_require_sso_never_locks_out_an_owner(self, db):
        org = Organization(name="Test Org", slug="test-sso-policy-org")
        db.add(org)
        db.flush()
        policy = session_policy_service.get_or_create_policy(db, organization_id=org.id)
        policy.require_sso = True
        policy.sso_bypass_for_owners = True
        assert session_policy_service.sso_required_for(policy, org_role="OWNER") is False
        assert session_policy_service.sso_required_for(policy, org_role="MEMBER") is True