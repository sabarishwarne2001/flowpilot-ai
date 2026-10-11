"""A document preset a workspace enables changes what the model is asked for (campaign session 2, brief C).

    pytest tests/engines/test_presets_reach_the_model_live.py -q

Settings -> Document presets lets an admin apply and enable an industry preset (a résumé,
an intake form). The control is only real if a matching document's extraction then asks the
model for the preset's fields, and only while the preset is enabled in that workspace.
"""

from __future__ import annotations

import pytest

from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.test_batch_ingestion_live import platform_presets  # noqa: F401  (fixture)

RESUME = [[
    "Curriculum Vitae - Priya Raman",
    "priya.raman@example.com  +91 98765 43210",
    "Work experience: Senior Analyst, Northwind Traders (2019 - 2026)",
    "Education: B.Com, University of Madras",
]]


def _resume_preset(engines: Engines) -> dict:
    presets = engines.get("/document-presets").json()
    return next(p for p in presets if p["document_type"] == "resume")


def _extract(engines: Engines, marker: str) -> str | None:
    engines.llm.record(marker, "Resume", {"candidate_name": "Priya Raman"})
    pages = [[f"{RESUME[0][0]} {marker}", *RESUME[0][1:]]]
    response = engines.client.post(
        engines.url("/work-items"), files={"file": (f"{marker}.pdf", make_pdf(pages), "application/pdf")},
        headers=engines.tenant.owner.headers,
    )
    assert response.status_code == 201, response.text
    before = len(engines.llm.preset_contexts)
    drain()
    assert len(engines.llm.preset_contexts) == before + 1, "the document's fields were never extracted"
    return engines.llm.preset_contexts[-1]


@pytest.mark.usefixtures("platform_presets")
def test_an_enabled_preset_adds_its_fields_to_the_extraction_prompt(engines: Engines) -> None:
    assert _extract(engines, "CV-NONE-1") is None, "a preset nobody applied reached the model"

    preset = _resume_preset(engines)
    applied = engines.post("/document-presets/apply", {"preset_id": preset["id"]})
    assert applied.status_code == 200, applied.text
    enabled = engines.post(f"/document-presets/{preset['id']}/enabled", {"enabled": True})
    assert enabled.status_code == 200, enabled.text

    context = _extract(engines, "CV-ON-1")
    assert context and preset["label"] in context, context
    for field in ("candidate_name", "years_experience", "most_recent_employer"):
        assert field in context, (field, context)

    disabled = engines.post(f"/document-presets/{preset['id']}/enabled", {"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert _extract(engines, "CV-OFF-1") is None, "a disabled preset still reached the model"
