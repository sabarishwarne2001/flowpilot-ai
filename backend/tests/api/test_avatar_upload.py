"""
ARCH-06 Step 7 — avatar upload and validated serving. Exit criteria E9–E11.

Every assertion here was first proven directly against the real service and a
live Postgres instance before this file was written; see
STEP7-VERIFICATION-GATE.md for the captured output.

WHY THE FIXTURES BUILD REAL IMAGES INSTEAD OF USING A CONSTANT BLOB
----------------------------------------------------------------------
Step 1b's suite used a hardcoded 1x1 PNG, which was correct then because
nothing decoded it. Step 7 decodes everything, and a 1x1 image now fails
`MIN_DIMENSION` — correctly. These fixtures generate real images with Pillow
so the tests exercise the actual decode path rather than a byte string that
happens to satisfy it.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.services import avatar_service


def make_png(size: tuple[int, int] = (64, 64), colour=(10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def make_jpeg_with_exif(marker: str = "SECRET-CAMERA-MAKE") -> bytes:
    """
    A JPEG carrying an identifiable EXIF tag.

    EXIF on a phone photo routinely includes GPS. A user setting an avatar
    should not thereby publish where they live, so the marker below stands in
    for anything that must not survive the re-encode.
    """
    buffer = io.BytesIO()
    image = Image.new("RGB", (64, 64), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010F] = marker
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def clean_avatar_dir():
    """
    Removes files this test module wrote, in both directions.

    Runs before as well as after: a previous failed run must not leave bytes
    that make a later assertion about disk state pass for the wrong reason.
    """
    def sweep():
        for path in avatar_service.AVATAR_DIR.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    sweep()
    yield
    sweep()


# ===========================================================================
# E9 — the bytes are validated, not the header
# ===========================================================================

class TestE9MagicByteValidation:
    """
    A.2.2: `file.content_type` is whatever the client typed. These tests all
    send a truthful-looking `image/png` header with untruthful bytes.
    """

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
        """
        E9. The `polyglot` case is the one that separates real validation from
        a magic-number check: it opens with a genuine PNG signature and
        carries a script payload after it.

        The SVG case matters because A.2.2 named it specifically — "an SVG
        variant would be immediately exploitable if .svg were ever added to
        ALLOWED_TYPES". Pillow will not decode SVG, so it fails closed, but
        the test pins that.
        """
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", payload, "image/png")},
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 400, (
            f"{label} payload was accepted with a forged image/png header."
        )
        assert list(avatar_service.AVATAR_DIR.glob("*")) == [], (
            f"{label} payload was written to disk before being rejected."
        )

    def test_a_real_image_with_a_wrong_header_is_still_accepted(
        self, client, tenant
    ):
        """
        The converse, and the reason this is validation rather than a
        stricter header check: the header is not consulted AT ALL. A real PNG
        mislabelled `application/octet-stream` is a real PNG.
        """
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

        written = list(avatar_service.AVATAR_DIR.glob("*"))
        assert len(written) == 1
        assert b"SECRET-CAMERA-MAKE" not in written[0].read_bytes()

    @pytest.mark.parametrize("size", [(8, 8), (5000, 5000)])
    def test_dimension_bounds_are_enforced(self, client, tenant, size):
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png(size), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 400

    def test_unauthenticated_upload_is_refused(self, client):
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("avatar.png", make_png(), "image/png")},
        )
        assert response.status_code == 401


# ===========================================================================
# E10 — cross-tenant isolation on read and delete
# ===========================================================================

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
        """
        E10, the load-bearing half.

        `other_org_member` is a legitimate, ACTIVE platform user who simply
        belongs to a different organization. 404 rather than 403 — a 403
        would confirm the account exists, turning this route into a
        membership oracle over arbitrary user ids.
        """
        self._set_avatar(client, tenant.ws_admin)

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
            headers=tenant.other_org_member.headers,
        )
        assert response.status_code == 404

    def test_a_colleague_in_the_same_organization_can_read_it(
        self, client, tenant
    ):
        """
        The positive control, and the reason this route is not simply
        owner-only. An avatar is meant to be seen by colleagues — a member
        directory that 404s on every face is not isolation, it is a broken
        feature. A test suite that only asserted refusals would pass on an
        endpoint that refused everyone.
        """
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
        """
        §B.7's whole point. Under the `StaticFiles` mount this replaces, an
        avatar's bytes were reachable by anyone who knew or guessed the URL.
        """
        self._set_avatar(client, tenant.ws_admin)

        response = client.get(
            f"/api/v1/users/{tenant.ws_admin.user.id}/avatar"
        )
        assert response.status_code == 401

    def test_delete_only_ever_affects_the_caller(self, client, tenant):
        """
        There is no route that deletes another user's avatar — DELETE is
        `/me/avatar` and resolves its target from the token, so a
        cross-tenant delete is not expressible as a request. This test pins
        that property by confirming a foreign caller's delete leaves the
        victim's avatar intact rather than merely returning an error code.
        """
        self._set_avatar(client, tenant.ws_admin)
        before = list(avatar_service.AVATAR_DIR.glob("*"))
        assert len(before) == 1

        response = client.delete(
            "/api/v1/me/avatar", headers=tenant.other_org_member.headers
        )
        assert response.status_code == 404

        assert list(avatar_service.AVATAR_DIR.glob("*")) == before
        assert (
            client.get(
                f"/api/v1/users/{tenant.ws_admin.user.id}/avatar",
                headers=tenant.ws_admin.headers,
            ).status_code
            == 200
        )


# ===========================================================================
# E11 — replacing an avatar removes the previous file
# ===========================================================================

class TestE11ReplacementCleansUp:

    def test_replacing_unlinks_the_previous_file(self, client, tenant):
        """
        E11, asserted on the filesystem rather than on the response.

        A.2.3: "Replacing a workspace logo writes a new file and abandons the
        old one." The single-file assertion is what catches a regression to
        that behaviour — a version that wrote the new file and left the old
        one would pass any check that only looked at the pointer.
        """
        first = client.post(
            "/api/v1/me/avatar",
            files={"file": ("a.png", make_png(colour=(10, 20, 30)), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert first.status_code == 200
        first_id = first.json()["file_id"]

        on_disk = list(avatar_service.AVATAR_DIR.glob("*"))
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
        assert len(list(avatar_service.AVATAR_DIR.glob("*"))) == 1

    def test_deleting_removes_the_file_and_the_pointer(self, client, tenant):
        client.post(
            "/api/v1/me/avatar",
            files={"file": ("a.png", make_png(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        assert len(list(avatar_service.AVATAR_DIR.glob("*"))) == 1

        response = client.delete(
            "/api/v1/me/avatar", headers=tenant.ws_admin.headers
        )
        assert response.status_code == 204
        assert list(avatar_service.AVATAR_DIR.glob("*")) == []

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


# ===========================================================================
# §B.7 — the response headers are the security property
# ===========================================================================

class TestB7ServingHeaders:

    def test_streamed_avatar_carries_the_validated_type_and_nosniff(
        self, client, tenant
    ):
        """
        §B.7. `nosniff` is what stops a browser overriding Content-Type by
        inspecting content — A.2.2 describes the unmitigated version as "one
        same-origin serving decision away from stored XSS".

        The Content-Type assertion matters because it comes from the stored,
        validated MIME rather than from the file extension, which is what the
        `StaticFiles` mount this route replaces used.
        """
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
        """
        The upload response carries metadata only. Publishing a storage key
        would reintroduce the guessable-URL surface §B.7 closes.
        """
        response = client.post(
            "/api/v1/me/avatar",
            files={"file": ("a.png", make_png(), "image/png")},
            headers=tenant.ws_admin.headers,
        )
        body = response.json()

        assert set(body) == {"file_id", "mime_type", "file_size"}
        assert "uploads/" not in response.text