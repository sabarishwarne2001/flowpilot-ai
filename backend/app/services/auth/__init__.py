"""ARCH-28 — authentication hardening services.

A package rather than a module because ARCH-28 adds the adversarial layer for
SAML and the same shape is expected for OIDC. `app/services/identity/` remains
the ARCH-16 protocol implementation; this package holds the things that exist
to say no.

Nothing is re-exported eagerly. `saml_security` imports `cryptography` lazily
inside `certificate_window`, and pulling that into every `app.services.auth`
import would put certificate parsing on the web tier's import path for no
reason.
"""

from __future__ import annotations

__all__ = ["saml_security"]
