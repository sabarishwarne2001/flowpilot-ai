"""Phase 2 — TruthMesh, the cross-document digital twin (capability.truthmesh).

Pure engine (no I/O):  facts (a document's twin), linker (typed links from identifiers, parties and
                       semantic similarity), conflicts (relation-aware discrepancy rules), ripple
                       (what-if traversal), risk (node and workspace scores).
Around it:             loader (the database), service (builds, persistence, conflict lifecycle,
                       simulations), gate.
"""

from app.services.truthmesh import vocabulary

__all__ = ["vocabulary"]
