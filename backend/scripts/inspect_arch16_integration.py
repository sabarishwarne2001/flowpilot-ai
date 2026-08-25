#!/usr/bin/env python3
"""Inspect real repository signatures for ARCH-16 integration."""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 70)
print("ARCH-16 REPOSITORY INTEGRATION INSPECTION")
print("=" * 70)

# 1. SSRF Client
try:
    import app.core.ssrf_client as ssrf
    print("[1] ssrf_client symbols:", [f for f in dir(ssrf) if not f.startswith("_")])
except Exception as e:
    print("[1] ssrf_client error:", e)

# 2. Encryption & SMTP
try:
    import app.core.encryption as enc
    print("[2a] encryption symbols:", [f for f in dir(enc) if not f.startswith("_")])
except Exception as e:
    print("[2a] encryption error:", e)

try:
    import app.core.smtp as smtp
    print("[2b] smtp symbols:", [f for f in dir(smtp) if not f.startswith("__")])
except Exception as e:
    print("[2b] smtp error:", e)

# 3. Outbox Service
try:
    import app.services.outbox_service as ob
    print("[3] outbox_service.emit signature:", inspect.signature(ob.emit))
except Exception as e:
    print("[3] outbox_service error:", e)

# 4. Audit Service
try:
    import app.services.audit_service as audit
    print("[4] audit_service.record signature:", inspect.signature(audit.record))
except Exception as e:
    print("[4] audit_service error:", e)

# 5. Dunning Service
try:
    import app.services.billing.dunning_service as dunning
    print("[5] dunning_service symbols:", [f for f in dir(dunning) if not f.startswith("_")])
except Exception as e:
    print("[5] dunning_service error:", e)

# 6. Transactions
try:
    import app.core.transactions as tx
    print("[6] transactions symbols:", [f for f in dir(tx) if not f.startswith("_")])
except Exception as e:
    print("[6] transactions error:", e)

# 7. Job Model & Enums
try:
    from app.models.job import Job, JobStatus
    print("[7] Job columns:", [c.name for c in Job.__table__.columns])
    print("[7] JobStatus values:", [e.value for e in JobStatus])
except Exception as e:
    print("[7] job model error:", e)

print("=" * 70)