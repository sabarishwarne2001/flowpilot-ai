"""ARCH-11 Step 7 — the reranker microservice package.

Imports nothing from `app.services`, `app.models`, or `app.api`. That is the
constraint that keeps it deployable as its own container without dragging the
application's dependency tree — and the reason `main.py` re-declares its
configuration from environment variables instead of importing `settings`.
"""
