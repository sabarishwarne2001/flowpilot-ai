"""F-014 — an organization slug must never shadow a top-level page of the app.

Workspace URLs are `/:orgSlug/:workspaceSlug`. An organization allowed to take
the slug `request` would have every workspace URL answered by the public
document-request upload page (`/request/:token`), and the same holds for every
other first path segment the web app routes itself. This reads those segments
from the router and the route constants, so a new public page cannot be added
without reserving its segment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.exceptions import ReservedSlugError
from app.core.slugs import RESERVED_SLUGS, validate_slug

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"
ROUTE_SOURCES = (FRONTEND / "App.tsx", FRONTEND / "constants" / "routes.ts")


def _top_level_segments() -> set[str]:
    segments: set[str] = set()
    for source in ROUTE_SOURCES:
        text = source.read_text(encoding="utf-8")
        segments |= set(re.findall(r'"/([a-z][a-z0-9-]*)', text))
    return segments


@pytest.mark.skipif(not FRONTEND.exists(), reason="frontend sources not in this checkout")
def test_every_top_level_page_segment_is_a_reserved_slug() -> None:
    missing = sorted(_top_level_segments() - RESERVED_SLUGS)
    assert missing == [], f"organizations could take these page segments as a slug: {missing}"


@pytest.mark.parametrize(
    "slug",
    ["request", "verify-email", "forgot-password", "reset-password", "confirm-email-change", "public"],
)
def test_public_page_segments_are_refused_as_slugs(slug: str) -> None:
    with pytest.raises(ReservedSlugError):
        validate_slug(slug)
