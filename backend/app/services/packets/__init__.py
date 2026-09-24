"""ARCH43-S1:packets — the Universal Packet Dicer (ARCH-43 Tranche 2).

A scanned bundle (one PDF, many documents) is diced into child documents:

  features.py         per-page boundary signals from what OCR already stored
                      (ARCH-41 header-skeleton fingerprints, page-number
                      resets, blank and separator sheets, classifier changes,
                      document-number changes). Pure.
  page_classifier.py  a deterministic per-page document type: ARCH-31's role
                      rules plus ARCH-38's preset classifier hints. Pure.
  model.py            the logistic boundary model. Fitted with scikit-learn by
                      scripts/fit_packet_boundaries.py; SCORED with plain math,
                      so detection runs on the LIGHT worker profile.
  synthetic.py        labelled synthetic packets (fit and verification).
  planner.py          probabilities -> a split plan (segments). Pure.
  service.py          plans in the database: detect, reviewer correction,
                      approve / reject, and apply (pikepdf, OCR profile).
  lineage.py          parent/child queries and entity-mention supersession.
  gate.py             capability.case_intelligence.
"""
