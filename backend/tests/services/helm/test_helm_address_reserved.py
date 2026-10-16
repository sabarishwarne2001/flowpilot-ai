"""Helm lives at `/:orgSlug/helm/…`, the workspace position of the address. No workspace (or
organization) may take the slug "helm", or its pages would be answered by Helm's."""

from __future__ import annotations

import pytest

from app.core.exceptions import ReservedSlugError
from app.core.slugs import RESERVED_SLUGS, validate_slug


def test_helm_is_a_reserved_slug() -> None:
    assert "helm" in RESERVED_SLUGS
    for slug in ("helm", "Helm", "HELM"):
        with pytest.raises(ReservedSlugError):
            validate_slug(slug)


def test_slugs_that_merely_start_with_helm_are_still_free() -> None:
    assert validate_slug("helmsley-foods") == "helmsley-foods"
