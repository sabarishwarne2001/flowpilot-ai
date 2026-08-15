"""Storage driver contract tests (ARCH-07 Step 5, ARCH-08 Step 12)."""

from __future__ import annotations

import pytest

from app.core.storage import LocalStorageDriver, ObjectNotFoundError
from app.core.storage.base import InvalidStorageKeyError, sanitize_key


@pytest.fixture(params=["local", "s3"])
def driver(request, tmp_path):
    if request.param == "local":
        yield LocalStorageDriver(root=tmp_path / "uploads")
        return

    moto = pytest.importorskip("moto")
    import boto3
    from app.core.storage.s3 import S3StorageDriver

    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="flowpilot-test")
        yield S3StorageDriver(bucket="flowpilot-test", prefix="test", client=client)


class TestKeyGrammar:

    @pytest.mark.parametrize(
        "key",
        [
            "../etc/passwd",
            "logos/../../etc/passwd",
            "/etc/passwd",
            "logos/../../../root/.ssh/id_rsa",
            "..",
            ".",
            "logos/./x.png",
            "logos//x.png",
            "logos\\x.png",
            "C:\\windows\\system32",
            "logos/x\x00.png",
            "",
            "logos/" + "a" * 600,
            "logos/x;rm -rf /.png",
            "logos/$(whoami).png",
        ],
    )
    def test_illegal_keys_are_rejected(self, key):
        with pytest.raises(InvalidStorageKeyError):
            sanitize_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "logos/9f8e.png",
            "avatars/0d1c-4a/ab12cd.png",
            "logos/a.b-c_d.png",
            "x.png",
        ],
    )
    def test_legal_keys_pass(self, key):
        assert sanitize_key(key) == key

    def test_put_refuses_traversal(self, driver):
        with pytest.raises(InvalidStorageKeyError):
            driver.put("../escaped.png", b"x", "image/png")

    def test_get_refuses_traversal(self, driver):
        with pytest.raises(InvalidStorageKeyError):
            driver.get("../../etc/passwd")

    def test_symlink_escape_is_contained(self, driver, tmp_path):
        if not isinstance(driver, LocalStorageDriver):
            pytest.skip("Symlink containment is specific to local filesystem driver.")

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.png").write_bytes(b"secret")

        link_dir = driver.root / "logos"
        link_dir.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            link_dir.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation requires Administrator privileges on Windows")

        with pytest.raises(InvalidStorageKeyError):
            driver.get("logos/secret.png")

    def test_exists_returns_false_for_illegal_key(self, driver):
        assert driver.exists("../etc/passwd") is False


class TestRoundTrip:

    def test_put_get(self, driver):
        assert driver.put("logos/a.png", b"payload", "image/png") == "logos/a.png"
        assert driver.get("logos/a.png") == b"payload"

    def test_put_creates_nested_directories(self, driver):
        driver.put("avatars/deep/nested/a.png", b"x", "image/png")
        assert driver.exists("avatars/deep/nested/a.png")

    def test_put_overwrites_atomically(self, driver):
        driver.put("logos/a.png", b"old", "image/png")
        driver.put("logos/a.png", b"new-and-longer", "image/png")
        assert driver.get("logos/a.png") == b"new-and-longer"

    def test_put_leaves_no_temp_files(self, driver):
        if not isinstance(driver, LocalStorageDriver):
            pytest.skip("Temp file cleanup test is specific to local filesystem driver.")
        driver.put("logos/a.png", b"x", "image/png")
        leftovers = [p for p in driver.root.rglob(".tmp-*")]
        assert leftovers == []

    def test_get_missing_raises(self, driver):
        with pytest.raises(ObjectNotFoundError):
            driver.get("logos/nope.png")

    def test_delete_is_idempotent(self, driver):
        driver.put("logos/a.png", b"x", "image/png")
        assert driver.delete("logos/a.png") is True
        assert driver.delete("logos/a.png") is False

    def test_exists(self, driver):
        assert driver.exists("logos/a.png") is False
        driver.put("logos/a.png", b"x", "image/png")
        assert driver.exists("logos/a.png") is True

    def test_stream(self, driver):
        driver.put("logos/a.png", b"streamed", "image/png")
        with driver.stream("logos/a.png") as handle:
            assert handle.read() == b"streamed"

    def test_stream_missing_raises(self, driver):
        with pytest.raises(ObjectNotFoundError):
            driver.stream("logos/nope.png")

    def test_size_without_reading(self, driver):
        driver.put("logos/a.png", b"12345", "image/png")
        assert driver.size("logos/a.png") == 5

    def test_iter_keys_skips_temp_files(self, driver):
        if not isinstance(driver, LocalStorageDriver):
            pytest.skip("Temp file filtering is specific to local filesystem driver.")
        driver.put("logos/a.png", b"x", "image/png")
        (driver.root / "logos" / ".tmp-junk.part").write_bytes(b"junk")
        assert driver.iter_keys("logos") == ["logos/a.png"]


class TestFactory:

    def test_singleton_identity(self, monkeypatch, tmp_path):
        from app.core import storage
        from app.core.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        storage.reset_storage_driver()
        assert storage.get_storage_driver() is storage.get_storage_driver()
        storage.reset_storage_driver()