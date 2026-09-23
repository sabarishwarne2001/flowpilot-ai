"""ARCH41-S2:package — Extraction Memory: a tenant's reviewed corrections,
turned into better extraction for documents of the same layout.

  fingerprint  layout signature of a document (pure)
  templates    layout clusters in the database
  harvest      reviewed values -> encrypted exemplars
  anchors      deterministic label->value rules; learning, replay, Wilson bound
  retrieval    exemplar selection for a document
  prompt       the fenced, budgeted memory block; per-document application
  trials       randomized proof, arm assignment, the autonomy hold
  drift        retiring rules and layouts that stop earning their place
  gate         capability + mode: the only answer to "may memory run here?"
  sweep        the nightly orchestration (scripts/sweep_extraction_memory.py)
"""
