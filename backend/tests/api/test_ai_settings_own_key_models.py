"""Settings -> AI offers premium models on the organization's own key only (campaign session 2, N-054).

    pytest tests/api/test_ai_settings_own_key_models.py -q

The platform's keys serve GPT-OSS 20B and 120B; every other listed model needs the
organization's own provider key (BYOK). Saving one without that key would be a setting that
does nothing (the platform default would answer every call), so it is refused with the way
on, and the page can show which models are ready.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.workspace_service import add_default_settings


def _payload(provider: str, model: str) -> dict:
    return {"provider": provider, "model": model, "temperature": 0.3, "max_output_tokens": 1024, "top_p": 1.0,
            "frequency_penalty": 0.0, "presence_penalty": 0.0, "enable_streaming": True}


def _ready(db: Session, tenant) -> None:
    add_default_settings(db, workspace_id=tenant.workspace.id, actor_id=tenant.owner.user.id)
    db.commit()


def test_a_model_the_platform_serves_is_saved(client, db_session: Session, tenant) -> None:
    _ready(db_session, tenant)
    response = client.put(f"/api/v1/workspaces/{tenant.workspace.id}/ai-settings",
                          json=_payload("GROQ", "openai/gpt-oss-120b"), headers=tenant.ws_admin.headers)
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "openai/gpt-oss-120b"


def test_a_premium_model_without_the_organizations_key_is_refused_with_the_way_on(
    client, db_session: Session, tenant
) -> None:
    _ready(db_session, tenant)
    response = client.put(f"/api/v1/workspaces/{tenant.workspace.id}/ai-settings",
                          json=_payload("GEMINI", "gemini-2.5-pro"), headers=tenant.ws_admin.headers)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "OWN_KEY_REQUIRED", body
    assert "own" in body["message"] and "key" in body["message"]
    assert body["details"]["model"] == "gemini-2.5-pro"


def test_a_model_nobody_lists_is_refused(client, db_session: Session, tenant) -> None:
    _ready(db_session, tenant)
    response = client.put(f"/api/v1/workspaces/{tenant.workspace.id}/ai-settings",
                          json=_payload("GROQ", "llama-3.3-70b-versatile"), headers=tenant.ws_admin.headers)
    assert response.status_code == 422, response.text


def test_the_page_can_say_which_models_are_ready(client, db_session: Session, tenant) -> None:
    _ready(db_session, tenant)
    response = client.get(f"/api/v1/workspaces/{tenant.workspace.id}/ai-settings/models/availability",
                          headers=tenant.contributor.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["GROQ"]["openai/gpt-oss-20b"] == "PLATFORM"
    assert body["GEMINI"]["gemini-2.5-pro"] == "OWN_KEY_NEEDED"
