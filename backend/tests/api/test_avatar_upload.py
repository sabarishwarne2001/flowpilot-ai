"""
ARCH-06 Step 7 — avatar upload and validated serving. Exit criteria E9–E11.
"""

from __future__ import annotations

import gc
import io
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import settings
from app.services import avatar_service


def _avatar_dir() -> Path:
    return Path(settings.UPLOAD_DIR) / "avatars"


def make_png(size: tuple[int, int] = (64, 64), colour=(10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def make_jpeg_with_exif(marker: str = "SECRET-CAMERA-MAKE") -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (64, 64), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010F] = marker
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _get_avatar_files() -> list[Path]:
    avatar_dir = _avatar_dir()
    if not avatar_dir.exists():
        return []
    return [p for p in avatar_dir.rglob("*") if p.is_file()]


@pytest.fixture(autouse=True)
def clean_avatar_dir():
    """Removes all files in avatar directory recursively before and after each test run."""
    avatar_dir = _avatar_dir()

    def sweep():
        gc.collect()
        if avatar_dir.exists():
            for path in sorted(avatar_dir.rglob("*"), reverse=True):
                if path.is_file():
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                elif path.is_dir() and path != avatar_dir:
                    try:
                        path.rmdir()
                    except OSError:
                        pass

    sweep()
    yield
    sweep()


class TestE9MagicByteValidation:

    @pytest.mark.parametrize(
        "label,payload",
        [
            ("html", b"<html><script>alert(1)</script></html>"),
            (
                "svg",
                b'<svg xmlns="http://www.w3.org/2000/svg">'
                b"<script>alert(1)</script></svg>",
            ),
            ("empty", b""),
            ("text", b"just some text, definitely not an image"),
            (
                "polyglot",
                b"\x89PNG\r\n\x1a\n" + b"<script>alert(1)</script>" * 20,
            ),
        ],
    )
    def test_non_image_with_image_content_type_is_rejected(
        self, client, tenant, label, payload
    ):
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", payload, "image/png")},
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 400, (
            f"{label} payload was accepted with a forged image/png header."
        )
        assert _get_avatar_files() == [], (
            f"{label} payload was written to disk before being rejected."
        )

    def test_a_real_image_with_a_wrong_header_is_still_accepted(
        self, client, tenant
    ):
        response = client.post(
            "/api/v1/me/avatar",
            files={
                "file": ("avatar.bin", make_png(), "application/octet-stream")
            },
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 200
        assert response.json()["mime_type"] == "image/png"

    def test_exif_is_stripped(self, client, tenant):
        raw = make_jpeg_with_exif()
        assert b"SECRET-CAMERA-MAKE" in raw, "fixture no longer carries EXIF"

        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("photo.jpg", raw, "image/jpeg")},
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 200

        written = _get_avatar_files()
        assert len(written) == 1
        assert b"SECRET-CAMERA-MAKE" not in written[0].read_bytes()

    def test_an_image_below_the_minimum_size_is_refused(self, client, tenant):
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png((8, 8)), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 400

    def test_a_large_normal_image_is_downscaled_to_1024(self, client, tenant):
        """Owner decision N-023: an image up to 50 megapixels is accepted and
        shrunk so its longer side is 1024 px (a 5000x5000 photo is 25 MP)."""
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png((5000, 5000)), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 200, response.text

        written = _get_avatar_files()
        assert len(written) == 1
        with Image.open(written[0]) as stored:
            assert max(stored.size) == avatar_service.MAX_DIMENSION

    def test_an_image_over_50_megapixels_is_refused(self, client, tenant):
        """Owner decision N-023: anything over 50 megapixels is an image bomb."""
        buffer = io.BytesIO()
        Image.new("1", (7100, 7100), 0).save(buffer, format="PNG")  # 50.41 MP
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", buffer.getvalue(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 400, response.text
        assert "50 megapixels" in response.json()["detail"]
        assert _get_avatar_files() == []

    def test_a_decompression_bomb_is_refused_before_it_is_decoded(self, client, tenant):
        """PHASE 4. A flat 12000x12000 image is a few hundred KB of PNG but
        144 M pixels: below Pillow's own bomb error, and about 1 GB once
        converted to RGBA. It is refused from its header, before decoding."""
        buffer = io.BytesIO()
        Image.new("1", (12000, 12000), 0).save(buffer, format="PNG")
        assert len(buffer.getvalue()) < 2 * 1024 * 1024
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", buffer.getvalue(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 400, response.text

    def test_unauthenticated_upload_is_refused(self, client):
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png(), "image/png")},
        )
        assert response.status_code == 401


class TestE10CrossTenantIsolation:

    @staticmethod
    def _set_avatar(client, persona) -> None:
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png(), "image/png")},
            headers=persona.headers,
        )
        assert response.status_code == 200, response.text

    def test_foreign_tenant_member_cannot_read_an_avatar(
        self, client, tenant
    ):
        self._set_avatar(client, tenant.ws_admin)

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
            headers=tenant.other_org_member.headers,
        )
        assert response.status_code == 404

    def test_a_colleague_in_the_same_organization_can_read_it(
        self, client, tenant
    ):
        self._set_avatar(client, tenant.ws_admin)

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
            headers=tenant.viewer.headers,
        )
        assert response.status_code == 200

    def test_owner_can_read_their_own(self, client, tenant):
        self._set_avatar(client, tenant.ws_admin)

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 200

    def test_unknown_user_id_is_404(self, client, tenant):
        response = client.get(
            f"/api/v1/users/{uuid.uuid4()}/avatar",
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 404

    def test_unauthenticated_read_is_refused(self, client, tenant):
        self._set_avatar(client, tenant.ws_admin)

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar"
        )
        assert response.status_code == 401

    def test_delete_only_ever_affects_the_caller(self, client, tenant):
        self._set_avatar(client, tenant.ws_admin)
        before = _get_avatar_files()
        assert len(before) == 1

        response = client.delete(
            "/api/v1/me/avatar", headers=tenant.other_org_member.headers
        )
        assert response.status_code == 404

        assert _get_avatar_files() == before
        assert (
            client.get(
                f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
                headers=tenant.ws_admin.headers,
            ).status_code
            == 200
        )


class TestE11ReplacementCleansUp:

    def test_replacing_unlinks_the_previous_file(self, client, tenant):
        first = client.post(
            "/api/v1/me/avatar",
            files={"file": ("a.png", make_png(colour=(10, 20, 30)), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert first.status_code == 200
        first_id = first.json()["file_id"]

        on_disk = _get_avatar_files()
        assert len(on_disk) == 1
        original_path: Path = on_disk[0]

        second = client.post(
            "/api/v1/me/avatar",
            files={"file": ("b.png", make_png(colour=(90, 80, 70)), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert second.status_code == 200
        assert second.json()["file_id"] != first_id

        assert not original_path.exists(), "The previous avatar was abandoned."
        assert len(_get_avatar_files()) == 1

    def test_deleting_removes_the_file_and_the_pointer(self, client, tenant):
        client.post(
            "/api/v1/me/avatar",
            files={"file": ("a.png", make_png(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert len(_get_avatar_files()) == 1

        response = client.delete(
            "/api/v1/me/avatar", headers=tenant.ws_admin.headers
        )
        assert response.status_code == 204
        assert _get_avatar_files() == []

        assert (
            client.get(
                f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
                headers=tenant.ws_admin.headers,
            ).status_code
            == 404
        )

    def test_deleting_with_no_avatar_is_404(self, client, tenant):
        response = client.delete(
            "/api/v1/me/avatar", headers=tenant.ws_admin.headers
        )
        assert response.status_code == 404


class TestB7ServingHeaders:

    def test_streamed_avatar_carries_the_validated_type_and_nosniff(
        self, client, tenant
    ):
        client.post(
            "/api/v1/me/avatar",
            files={"file": ("weird-name.bin", make_png(), "text/plain")},
            headers=tenant.ws_admin.headers,
        )

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"] == "inline"
        assert "private" in response.headers.get("cache-control", "")

    def test_response_does_not_leak_the_storage_path(self, client, tenant):
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("a.png", make_png(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        body = response.json()

        assert set(body) == {"file_id", "mime_type", "file_size"}
        assert "uploads/" not in response.text


class TestHasAvatarFlag:
    """F-049 / F-028. The app asked for /users/{id}/avatar on every page and
    logged a 404 for every user without one (most users). /auth/me now says
    whether there is an avatar, so the app only asks when there is."""

    def test_me_reports_no_avatar_until_one_is_uploaded(self, client, tenant):
        before = client.get("/api/v1/auth/me", headers=tenant.ws_admin.headers)
        assert before.status_code == 200
        assert before.json()["has_avatar"] is False

        uploaded = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        assert client.get("/api/v1/auth/me", headers=tenant.ws_admin.headers).json()["has_avatar"] is True

        removed = client.delete("/api/v1/me/avatar", headers=tenant.ws_admin.headers)
        assert removed.status_code == 204
        assert client.get("/api/v1/auth/me", headers=tenant.ws_admin.headers).json()["has_avatar"] is False
