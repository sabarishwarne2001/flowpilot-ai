"""F-124 — SCIM tokens are hashed with a secret key, not a constant (campaign session 3).

`scim_service._pepper()` read an undeclared `SCIM_TOKEN_PEPPER` (always None) and fell back to
`str(settings.JWT_SECRET_KEY)`. `JWT_SECRET_KEY` is a pydantic `SecretStr`, whose `str()` is the
mask `**********`, so every SCIM token in every deployment was hashed with that same public
constant. The key is now derived from the deployment's API key pepper (a declared secret the
production guard requires), with its own label so a SCIM hash can never be confused with an API
key hash.
"""

from __future__ import annotations

import hashlib
import hmac

from pydantic import SecretStr

from app.services.identity import scim_service


def test_the_scim_token_key_is_not_the_secret_mask() -> None:
    masked = hmac.new(b"**********", b"secret-part", hashlib.sha256).digest()
    assert scim_service.hash_secret("secret-part") != masked


def test_the_scim_token_key_follows_the_deployment_secret(monkeypatch) -> None:
    settings = scim_service.get_settings()
    first = scim_service.hash_secret("secret-part")
    monkeypatch.setattr(settings, "API_KEY_PEPPER", SecretStr("another-deployment-pepper-0123456789abcdef"))
    second = scim_service.hash_secret("secret-part")
    assert first != second


def test_a_scim_hash_is_not_an_api_key_hash() -> None:
    from app.core.api_key_secret import hash_secret as api_key_hash

    assert scim_service.hash_secret("secret-part").hex() != api_key_hash("secret-part")
