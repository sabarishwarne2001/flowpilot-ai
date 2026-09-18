"""ARCH-38 — batch ingestion and universal document intelligence.

`archive` is pure and imports nothing from this package, so every zip limit can
be exercised without a database. The rest take a Session.
"""

from __future__ import annotations

__all__ = [
    "archive",
    "batch_service",
    "bulk_service",
    "preset_service",
    "retention_service",
    "upload_session_service",
]
