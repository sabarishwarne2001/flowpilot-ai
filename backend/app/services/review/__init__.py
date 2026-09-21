"""ARCH-40 — the unified review hub.

A projection over three existing tables, never a fourth store.

ARCH40-S1:review-package.
"""

from app.services.review import projection, resolution, vocabulary

__all__ = ["projection", "resolution", "vocabulary"]
