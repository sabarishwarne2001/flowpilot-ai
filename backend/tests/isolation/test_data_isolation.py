"""
Multi-tenant data scope isolation integration tests.
"""

import pytest
from app.models.work_item import WorkItem
from tests.isolation.helpers import assert_workspace_scoped

SCOPED_COLLECTIONS = [
    ("work-items", "original_filename", "alpha_doc", "beta_doc"),
    ("automation", "name", "alpha_rule", "beta_rule"),
    ("notifications", "title", "alpha_note", "beta_note"),
    ("assistant/conversations", "title", "alpha_convo", "beta_convo"),
]


@pytest.mark.parametrize(
    "path,field,alpha_key,beta_key", SCOPED_COLLECTIONS
)
def test_collections_are_workspace_scoped(
    client, token_for, multi_user, alpha_ws, beta_ws, seeded,
    path, field, alpha_key, beta_key,
):
    token = token_for(multi_user)

    assert_workspace_scoped(
        client, token=token, owner_ws=alpha_ws.id, foreign_ws=beta_ws.id,
        path=path, field=field, marker=getattr(seeded[alpha_key], field),
    )
    assert_workspace_scoped(
        client, token=token, owner_ws=beta_ws.id, foreign_ws=alpha_ws.id,
        path=path, field=field, marker=getattr(seeded[beta_key], field),
    )


def test_authorship_does_not_filter(
    client, token_for, contributor_alpha, alpha_ws, seeded
):
    response = client.get(
        f"/api/v1/workspaces/{alpha_ws.id}/work-items",
        headers={"Authorization": f"Bearer {token_for(contributor_alpha)}"},
    )
    assert response.status_code == 200
    names = {row["original_filename"] for row in response.json()["items"]}
    assert names == {"ALPHA-MARKER-1.pdf", "ALPHA-MARKER-2.pdf"}


def test_counters_are_workspace_scoped(
    client, token_for, multi_user, alpha_ws, beta_ws, seeded
):
    headers = {"Authorization": f"Bearer {token_for(multi_user)}"}

    alpha = client.get(f"/api/v1/workspaces/{alpha_ws.id}/work-items", headers=headers)
    beta = client.get(f"/api/v1/workspaces/{beta_ws.id}/work-items", headers=headers)

    assert alpha.json()["totalItems"] == 2
    assert beta.json()["totalItems"] == 1

    overview = client.get(
        f"/api/v1/workspaces/{beta_ws.id}/dashboard/overview", headers=headers
    )
    assert overview.json()["total_work_items"] == 1


def test_cross_workspace_object_addressing_returns_404(
    client, token_for, multi_user, beta_ws, seeded
):
    response = client.get(
        f"/api/v1/workspaces/{beta_ws.id}/work-items/{seeded['alpha_doc'].id}",
        headers={"Authorization": f"Bearer {token_for(multi_user)}"},
    )
    assert response.status_code == 404


def test_cross_workspace_delete_does_not_delete(
    client, db_session, token_for, multi_user, beta_ws, seeded
):
    alpha_doc_id = seeded["alpha_doc"].id
    response = client.delete(
        f"/api/v1/workspaces/{beta_ws.id}/work-items/{alpha_doc_id}",
        headers={"Authorization": f"Bearer {token_for(multi_user)}"},
    )
    assert response.status_code == 404

    db_session.expire_all()
    assert db_session.get(WorkItem, alpha_doc_id) is not None


def test_settings_are_independent_per_workspace(
    client, token_for, multi_user, alpha_ws, beta_ws
):
    headers = {"Authorization": f"Bearer {token_for(multi_user)}"}

    client.put(
        f"/api/v1/workspaces/{alpha_ws.id}/ai-settings",
        headers=headers, json={
            "provider": "GROQ",
            "model": "alpha-only-model",
            "temperature": 0.7,
            "max_output_tokens": 2048,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "input_cost_per_1k_tokens": 0.0,
            "output_cost_per_1k_tokens": 0.0,
            "system_prompt_version": "v1",
            "prompt_version": "v1",
            "enable_token_tracking": True,
            "enable_streaming": True
        },
    )
    beta = client.get(
        f"/api/v1/workspaces/{beta_ws.id}/ai-settings", headers=headers
    )
    assert beta.status_code == 200
    assert beta.json()["model"] != "alpha-only-model"


def test_mark_all_read_is_workspace_scoped(
    client, token_for, multi_user, alpha_ws, beta_ws, seeded
):
    headers = {"Authorization": f"Bearer {token_for(multi_user)}"}

    client.post(
        f"/api/v1/workspaces/{alpha_ws.id}/notifications/mark-all-read", headers=headers
    )
    beta = client.get(
        f"/api/v1/workspaces/{beta_ws.id}/notifications", headers=headers
    )
    data = beta.json()
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    assert any(not row["is_read"] for row in items)


def test_outsider_sees_404_everywhere(client, token_for, outsider, alpha_ws):
    headers = {"Authorization": f"Bearer {token_for(outsider)}"}
    for path, _, _, _ in SCOPED_COLLECTIONS:
        response = client.get(
            f"/api/v1/workspaces/{alpha_ws.id}/{path}", headers=headers
        )
        assert response.status_code == 404, path