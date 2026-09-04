"""ARCH-26 — enterprise analytics, BI egress and warehouse sync.

Deliberately empty of re-exports.

`app/services/analytics/__init__.py` importing `export_engine` would pull
pyarrow into every process that touches `app.services.analytics` for any
reason, including the API workers that never generate a bundle. B2-a's whole
point is that pyarrow is imported lazily, inside the function that writes
Parquet, and an eager re-export here would quietly undo it.

Import the submodule you need:

    from app.services.analytics import export_engine
    from app.services.analytics import sync_service
    from app.services.analytics.connectors import get_connector
"""

from __future__ import annotations

__all__: list[str] = []
