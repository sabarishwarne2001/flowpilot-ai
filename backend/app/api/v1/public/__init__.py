"""ARCH-21 §3.1 — the public developer gateway package.

Kept as a package rather than a single module so that a second gateway
version can land beside `gateway.py` without either importing the other. The
deprecation machinery in `gateway.py` assumes exactly that: v1 gains `Sunset`
and `Deprecation` headers pointing at a successor that lives next to it.
"""

from app.api.v1.public.gateway import router

__all__ = ["router"]
