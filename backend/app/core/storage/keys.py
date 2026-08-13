"""Legacy path compatibility (ARCH-07 Steps 5-7).
"""

from __future__ import annotations

import logging

from app.core.storage.base import sanitize_key

logger = logging.getLogger(__name__)

_LEGACY_PREFIX = "/uploads/"


def legacy_path_to_key(stored_path: str) -> str:
    """Coerce a stored file_path — key or legacy public URL — into a key."""
    if stored_path.startswith(_LEGACY_PREFIX):
        logger.warning(
            "ARCH07_LEGACY_PATH | file_path is still in public-URL form: %s. "
            "Step 6 normalisation has not run for this row.",
            stored_path,
        )
        return sanitize_key(stored_path[len(_LEGACY_PREFIX):])
    return sanitize_key(stored_path)