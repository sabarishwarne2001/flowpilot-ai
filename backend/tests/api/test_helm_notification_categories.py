"""Helm's notice and briefing switches are offered to the people Helm notifies (owners, admins and
the billing role), and never to a member, who never gets a Helm notice."""

from __future__ import annotations

URL = "/api/v1/me/notification-preferences"


def _keys(client, persona) -> list[str]:
    response = client.get(URL, headers=persona.headers)
    assert response.status_code == 200, response.text
    return [row["key"] for row in response.json()["categories"]]


def test_an_owner_can_turn_the_helm_briefing_off(client, tenant):
    keys = _keys(client, tenant.owner)
    assert keys[-2:] == ["helm", "helm_briefing"]
    off = client.put(URL, json={"category": "helm_briefing", "email": False}, headers=tenant.owner.headers)
    assert off.status_code == 200, off.text
    rows = {row["key"]: row for row in off.json()["categories"]}
    assert rows["helm_briefing"]["email"] is False and rows["helm"]["email"] is True


def test_a_member_is_not_offered_helm_switches(client, tenant):
    assert "helm" not in _keys(client, tenant.contributor)
