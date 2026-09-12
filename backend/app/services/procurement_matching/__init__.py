"""ARCH-31 — procurement three-way matching.

NAMED `procurement_matching`, NOT `reconciliation`, DELIBERATELY
===============================================================

`app/services/reconciliation/` and `app/models/reconciliation.py` already
exist and belong to ARCH-18: supplier COGS reconciliation, which matches
provider invoices against metered usage to prove unit economics. That is a
different problem with a different vocabulary — its "invoice" is one Anthropic
or Groq sent to us.

ARCH-31 matches a tenant's purchase order against their goods receipt against
their supplier's invoice. Overloading the existing name would put two unrelated
meanings of "reconciliation", "engine" and "statement" in one import namespace,
and the first person to grep for `reconcile` six months from now would find
both and trust the wrong one. Tables are prefixed `procurement_*` for the same
reason.

Deliberately empty of re-exports, matching `app/services/analytics/__init__.py`:
the matcher imports scipy and sentence-transformers, and an eager re-export
here would pull both into every process that touches this package for any
reason, including API workers that never score a case.

    from app.services.procurement_matching import role_classifier
"""

from __future__ import annotations

__all__: list[str] = []