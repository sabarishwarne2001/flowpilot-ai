"""ARCH-25 — white-label, custom domains and tenant branding services.

Two modules, split along the role boundary the API enforces:

  domain_service    Hostname ownership and TLS. Every write behind it is
                    OWNER-gated, because a vanity hostname resolves to a
                    tenant and claiming one is authentication-adjacent.
  branding_service  Tokens, assets and sender domains. ADMIN-gated: this is
                    presentation, and an administrator's job.

Nothing is re-exported here. `from app.services.branding import
domain_service` reads better at the call site than a flat namespace, and it
keeps the two role boundaries visible in the import line.
"""

from __future__ import annotations

__all__: list[str] = []