"""ARCH43-S1:cases — Case Intelligence (ARCH-43 Tranche 3).

  dsl.py         the consistency rules (EQUAL, FUZZY_EQUAL, DATE_ORDER,
                 WITHIN_DAYS, SUM_EQUALS). Pure.
  doc_types.py   one document type per work item, from what the platform
                 already knows (ARCH-31 roles, the enrichment's
                 classification, the ARCH-43 page classifier).
  templates.py   draft -> publish (immutable) -> retire; versions.
  assembly.py    documents into cases by ARCH-42 entity root, ARCH-38 batch or
                 by hand; completeness + rules -> status; triggers
                 case.completed / case.inconsistent.
  requests.py    missing-document requests: single-use, expiring tokens
                 stored only as SHA-256.
"""
