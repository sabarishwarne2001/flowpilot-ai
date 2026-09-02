import os
import pytest
from pydantic import ValidationError
from app.core.config import Settings


def test_singular_email_encryption_key_raises_tombstone(monkeypatch):
    monkeypatch.setenv("EMAIL_ENCRYPTION_KEY", "somelegacykey")
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretjwtkeythatislongenough1234567890")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert "EMAIL_ENCRYPTION_KEY was removed in ARCH-08 Step 1" in str(excinfo.value)
