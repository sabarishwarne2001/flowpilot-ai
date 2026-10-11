"""A new organization's first workspace is ready for AI at once (campaign session 2, F-260).

    pytest tests/api/test_new_organization_ai_ready.py -q

`POST /organizations` created the first workspace without its AI settings (a workspace
added later got them from `workspace_service`); they appeared only when the worker
enriched the first document. A new customer who opened the assistant or Settings → AI
before uploading anything was told "AI settings have not been configured".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core import security
from app.core.ai_models import AI_MODELS
from app.models.organization import Organization
from app.models.user import User
from app.schemas.ai_settings import AIProvider
from tests.security.plans import put_on_plan


def test_the_first_workspace_has_ai_settings_before_any_upload(client, db_session: Session) -> None:
    scratch = Organization(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed")
    db_session.add(scratch)
    db_session.flush()
    put_on_plan(db_session, scratch, "free")
    user = User(email=f"new-{uuid.uuid4().hex[:8]}@example.com", hashed_password=security.get_password_hash("x"),
                is_active=True, email_verified_at=datetime.now(timezone.utc))
    db_session.add(user)
    db_session.commit()
    headers = {"Authorization": f"Bearer {security.create_access_token(subject=str(user.id))}"}

    created = client.post("/api/v1/organizations", json={"organization_name": "Fresh Co"}, headers=headers)
    assert created.status_code == 201, created.text
    workspaces = client.get("/api/v1/me/workspaces", headers=headers).json()
    workspace_id = next(w["id"] for w in workspaces if w["organization_id"] == created.json()["id"])

    settings = client.get(f"/api/v1/workspaces/{workspace_id}/ai-settings", headers=headers)
    assert settings.status_code == 200, f"a new organization's workspace has no AI settings: {settings.text}"
    assert settings.json()["model"] in AI_MODELS[AIProvider(settings.json()["provider"])]

    models = client.get(f"/api/v1/workspaces/{workspace_id}/assistant/models", headers=headers)
    assert models.status_code == 200, models.text
