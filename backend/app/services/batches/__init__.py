"""Phase 1 — the batch processing & document dispatch engine (capability.batch_dispatch).

    vocabulary   lanes, tags, limits and defaults
    canonical    the field schema each document type is held to (built in, or an enabled preset)
    healing      renames drifted keys and retypes values to that schema; apply and undo
    confidence   document- and field-level confidence from verification
    dispatch     the lane each document goes to, and why
    service      batches: create, membership, progress, analytics
    packages     export packages: a zip with a SHA-256 manifest, built by the worker; verification
"""
