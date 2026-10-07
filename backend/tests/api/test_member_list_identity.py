"""Organization -> Members shows who people are (F-153).

The member list rendered bare email addresses: the API already sent each member's display
name, which the page never read, and nothing said whether a member had a profile picture, so
the page could not show one without requesting (and 404-ing) an avatar for every member. The
member summary now says `has_avatar`, so the list shows the picture or the initials.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.uploaded_file import UploadedFile


def test_member_summaries_carry_the_name_and_whether_there_is_a_picture(
    client: TestClient, db_session: Session, tenant
) -> None:
    contributor = tenant.contributor.user
    contributor.display_name = "Casey Contributor"
    picture = UploadedFile(
        file_path=f"avatars/{uuid.uuid4().hex}.png",
        original_filename="me.png",
        mime_type="image/png",
        file_size=1024,
        checksum_sha256="0" * 64,
        owner_id=contributor.id,
        organization_id=tenant.organization.id,
    )
    db_session.add(picture)
    db_session.flush()
    contributor.avatar_file_id = picture.id
    db_session.commit()

    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/members",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    users = {item["user"]["id"]: item["user"] for item in response.json()["items"]}

    assert users[str(contributor.id)]["display_name"] == "Casey Contributor"
    assert users[str(contributor.id)]["has_avatar"] is True
    assert users[str(tenant.owner.user.id)]["has_avatar"] is False
