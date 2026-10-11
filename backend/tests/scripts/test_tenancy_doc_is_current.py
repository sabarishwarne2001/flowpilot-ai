"""docs/hardening/TENANCY-AND-PLANS.md is the code's own account of itself (campaign session 1).

    pytest tests/scripts/test_tenancy_doc_is_current.py -q

The document's tables are computed by `scripts/generate_tenancy_doc.py` from the
permission functions, the plan seed, the capability names and the add-on catalog.
When any of them changes and the document is not regenerated, this fails: run
`python scripts/generate_tenancy_doc.py` and commit the result.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "generate_tenancy_doc.py"


def _generator():
    spec = importlib.util.spec_from_file_location("generate_tenancy_doc_for_tests", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tenancy_document_matches_the_code() -> None:
    generator = _generator()
    expected = generator.render()
    committed = generator.DOC.read_text(encoding="utf-8")
    assert committed == expected, (
        "docs/hardening/TENANCY-AND-PLANS.md is stale: run "
        "`python scripts/generate_tenancy_doc.py` in backend/ and commit it."
    )


def test_the_document_says_what_the_free_plan_is() -> None:
    """A spot check that the generator reads the seed, not a copy of it."""
    text = _generator().render()
    assert "| Document uploads | 25 (refuse) |" in text
    assert "| Seats | 2 | purchased seats |" in text
