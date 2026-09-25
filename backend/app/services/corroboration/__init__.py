"""ARCH45-S1:package — the Universal Document Corroborator & Discrepancy Matrix
(capability.universal_corroborator, Enterprise).

Pure engine (no I/O):  normalize, segment (clauses with geometry), encoders,
                       align (Hungarian + N-way groups), fields, entities,
                       lines, rules (ARCH-33), materiality -> engine.
Around it:             inputs (stored material -> DocInput), loader (the
                       database), fingerprint (the cache key), service (runs,
                       decisions, staleness), report (PDF / CSV / JSON), gate.
Planted-difference sets: synthetic (verify_arch45 gates).
"""

from app.services.corroboration import vocabulary

__all__ = ["vocabulary"]
