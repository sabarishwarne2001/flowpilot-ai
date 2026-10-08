"""F-156, live: the entity and obligation search boxes match their text literally.

Both passed the text straight into an ILIKE pattern, so "_" and "%" (LIKE wildcards) listed every
entity and every obligation in the workspace instead of the ones containing that character.
"""

from __future__ import annotations

from tests.engines.conftest import Engines

CONTRACT = [
    "MASTER SERVICES AGREEMENT CTR-WILD-1",
    "between Contoso Retail Private Limited and Acme Supplies Ltd",
    "1. Term. This Agreement commences on 1 January 2026 and continues for a period of twelve (12) months.",
    "2. Renewal. This Agreement shall automatically renew for successive periods of twelve (12) months",
    "unless either party gives written notice of non-renewal at least sixty (60) days before the end of",
    "the then-current term.",
    "3. Payment. The Customer shall pay the monthly fee on or before the 5th day of each month.",
]


def _process(engines: Engines) -> None:
    engines.process("msa-wild.pdf", [CONTRACT], marker="CTR-WILD-1", classification="Contract",
                    entities={"party_names": ["Contoso Retail Private Limited", "Acme Supplies Ltd"],
                              "agreement_date": "2026-01-01"})


def _names(engines: Engines, path: str, q: str, key: str) -> list[str]:
    response = engines.get(path, params={"q": q})
    assert response.status_code == 200, response.text
    return [row[key] for row in response.json()["items"]]


def test_entity_search_treats_wildcards_as_text(engines: Engines) -> None:
    _process(engines)
    assert _names(engines, "/entities", "acme", "display_name"), "the contract's parties were not resolved"

    assert _names(engines, "/entities", "_", "display_name") == []
    assert _names(engines, "/entities", "%", "display_name") == []


def test_obligation_search_treats_wildcards_as_text(engines: Engines) -> None:
    _process(engines)
    assert _names(engines, "/obligations", "renew", "title"), "no renewal obligation was read"

    assert _names(engines, "/obligations", "_", "title") == []
    assert _names(engines, "/obligations", "%", "title") == []
