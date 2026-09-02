from types import SimpleNamespace

from app.services.identity.session_policy_service import sso_required_for


def test_sso_required_for_non_owner():
    policy = SimpleNamespace(
        require_sso=True,
        sso_bypass_for_owners=True,
    )

    assert sso_required_for(policy, org_role="MEMBER") is True


def test_sso_required_for_owner_bypass():
    policy = SimpleNamespace(
        require_sso=True,
        sso_bypass_for_owners=True,
    )

    assert sso_required_for(policy, org_role="OWNER") is False


def test_sso_not_required_when_disabled():
    policy = SimpleNamespace(
        require_sso=False,
        sso_bypass_for_owners=False,
    )

    assert sso_required_for(policy, org_role="MEMBER") is False
