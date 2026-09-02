"""ARCH-13 — the automation engine.

Module layout follows the Part 1 spine, one module per arrow:

    cycle_detector.py   TRIGGER -> DECISION guard (A7, Step 13.2)
    budget.py           DECISION -> ACTION guard  (A6, Step 13.3)
    graph_service.py    the DAG itself            (Step 13.4)
    executor.py         the walk                  (Step 13.5)
    extraction.py       document text -> data     (Step 13.6)
"""

from __future__ import annotations

__all__: list[str] = []
