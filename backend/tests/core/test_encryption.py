"""ARCH-07 Steps 8-9 — encryption and rotation tests."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core import encryption
from app.core.config import settings


@pytest.fixture
def two_keys(monkeypatch):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()

    def configure(*keys: str) -> None:
        monkeypatch.setattr(settings, "_encryption_key_list", list(keys), raising=False)
        encryption.reset_encryption()

    configure(old)
    yield old, new, configure
    encryption.reset_encryption()


class TestRoundTrip:

    def test_encrypt_decrypt(self, two_keys):
        token = encryption.encrypt_password("hunter2")
        assert token != "hunter2"
        assert encryption.decrypt_password(token) == "hunter2"

    def test_ciphertext_is_non_deterministic(self, two_keys):
        assert encryption.encrypt_password("x") != encryption.encrypt_password("x")

    def test_unicode_survives(self, two_keys):
        secret = "påsswörd–✓"
        assert encryption.decrypt_password(
            encryption.encrypt_password(secret)
        ) == secret

    def test_over_length_plaintext_is_refused(self, two_keys):
        with pytest.raises(encryption.CiphertextTooLongError):
            encryption.encrypt_password("x" * 400)

    def test_ciphertext_fits_the_column(self, two_keys):
        token = encryption.encrypt_password("x" * encryption.MAX_PLAINTEXT_LENGTH)
        assert len(token) <= encryption.MAX_CIPHERTEXT_LENGTH

    def test_garbage_raises_decryption_error(self, two_keys):
        with pytest.raises(encryption.DecryptionError):
            encryption.decrypt_password("not-a-fernet-token")


class TestMultiFernetRotation:

    def test_secondary_key_still_decrypts(self, two_keys):
        old, new, configure = two_keys
        legacy = encryption.encrypt_password("legacy-secret")

        configure(new, old)
        assert encryption.decrypt_password(legacy) == "legacy-secret"

    def test_new_writes_use_the_head_key(self, two_keys):
        old, new, configure = two_keys
        configure(new, old)
        token = encryption.encrypt_password("fresh")

        configure(new)
        assert encryption.decrypt_password(token) == "fresh"

    def test_decrypting_key_index_identifies_the_writer(self, two_keys):
        old, new, configure = two_keys
        legacy = encryption.encrypt_password("legacy")

        configure(new, old)
        assert encryption.decrypting_key_index(legacy) == 1

        fresh = encryption.encrypt_password("fresh")
        assert encryption.decrypting_key_index(fresh) == 0

    def test_decrypting_key_index_is_none_for_unknown_key(self, two_keys):
        old, new, configure = two_keys
        stranger = Fernet(Fernet.generate_key()).encrypt(b"x").decode()
        configure(new, old)
        assert encryption.decrypting_key_index(stranger) is None

    def test_rotate_moves_ciphertext_to_the_head(self, two_keys):
        old, new, configure = two_keys
        legacy = encryption.encrypt_password("secret")

        configure(new, old)
        rotated = encryption.rotate_ciphertext(legacy)
        assert encryption.decrypting_key_index(rotated) == 0

        configure(new)
        assert encryption.decrypt_password(rotated) == "secret"

    def test_dropping_the_old_key_early_breaks_unrotated_rows(self, two_keys):
        old, new, configure = two_keys
        legacy = encryption.encrypt_password("secret")

        configure(new)
        with pytest.raises(encryption.DecryptionError):
            encryption.decrypt_password(legacy)


class TestSweeperIdempotence:

    def test_second_run_rotates_zero_rows(self, db_session, two_keys):
        from app.services.encryption_rotation_service import (
            reencrypt_all_smtp_passwords,
        )

        old, new, configure = two_keys
        configure(new, old)

        first = reencrypt_all_smtp_passwords(db_session, dry_run=False)
        assert sum(r.rotated for r in first) >= 0

        second = reencrypt_all_smtp_passwords(db_session, dry_run=False)
        assert sum(r.rotated for r in second) == 0

    def test_dry_run_changes_nothing(self, db_session, two_keys):
        from sqlalchemy import text
        from app.services.encryption_rotation_service import (
            reencrypt_all_smtp_passwords,
        )

        old, new, configure = two_keys
        configure(new, old)

        before = db_session.execute(
            text("SELECT id, encrypted_password FROM email_settings ORDER BY id")
        ).all()
        reencrypt_all_smtp_passwords(db_session, dry_run=True)
        after = db_session.execute(
            text("SELECT id, encrypted_password FROM email_settings ORDER BY id")
        ).all()
        assert before == after