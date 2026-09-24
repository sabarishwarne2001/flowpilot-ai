"""ARCH42-S1:package — Entity Resolution & Document Knowledge Graph.

  vocabulary      every constant the engine and its gates share (pure)
  normalize       names and identifiers into comparable, validated form (pure)
  embedding       hashed character/token vectors for ANN blocking (pure)
  fellegi_sunter  comparison vectors, match weights, EM fitting (pure)
  annotations     the x-entity vocabulary; extracted fields -> mentions (pure)
  crypto          identifier HMACs under every configured key; sealing
  gate            capability.entity_graph: "may resolution run here?"
  presets         which preset fits a document; enabled presets -> prompt
  blocking        candidate records: identifier HMAC, pg_trgm, pgvector ANN
  match_models    the per-workspace, per-kind EM parameters
  graph           clusters, conflict guard, reversible merge / unmerge / split
  resolver        one document -> mentions, identifiers, edges, reviews
  erasure         ARCH-20: documents, subjects and records leave no identifier
  sweep           the nightly pass (scripts/sweep_entities.py)
"""
