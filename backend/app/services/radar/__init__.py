"""ARCH-34 — Cross-Document Anomaly & Duplicate Ingestion Radar.

THE INVARIANT THIS PACKAGE EXISTS TO HOLD
=========================================

A finding is never asserted without the evidence that produced it, and no
layer may claim a duplicate it cannot show.

THE PURE / IMPURE BOUNDARY
==========================

Pure, stdlib-only (plus this package's own modules and ARCH-33's equally pure
family parsers). No `Session`, no clock, no network, no settings:

    vocabulary.py      the closed vocabularies, shared with the migration
    fingerprint.py     SHA-256, MinHash, mean chunk embedding
    layers/            L0..L3, ordered, early-exit
    price_surge.py     robust z over the trailing window
    drift.py           clause alignment + ARCH-33 family parsers

Impure, holding the `Session` (ARCH-34 Tranche 2):

    candidates.py      ORM rows -> layers.Candidate. THE NAMED BOUNDARY.
    sweep.py           the incremental, digest-keyed pairwise pass
    findings.py        upsert, severity policy, audit, outbox, notification
    suppressions.py    a reviewer's "this is fine, and here is why"

`candidates.to_candidates()` is the line that makes "everything downstream is
gateable offline" checkable rather than aspirational, exactly as
`assertions/retrieve.to_chunks()` is for ARCH-33.

THIRD-PARTY LICENSES INTRODUCED BY THIS PHASE
=============================================

None. MinHash is hand-rolled over the standard library's `hashlib` — see
`fingerprint.py` for why `datasketch` was evaluated, is MIT, and is still not
used. No AGPL anywhere in the dependency set, and no new recurring cost: the
embedding half of the radar reuses vectors ARCH-11 already computed and paid
for, and adds no model call.
"""

from __future__ import annotations
