"""ARCH-19 — Infrastructure, High Availability & Ingress.

This package needs its own __init__.py, and that is not decoration.

tests/core/ has no __init__.py, so pytest imports tests/core/test_pool_profiles.py
under the bare module name `test_pool_profiles`. This package also contains a
test_pool_profiles.py, and a second bare `test_pool_profiles` would collide at
import time with pytest's "import file mismatch" error. With __init__.py present
pytest walks up while packages exist and names this one
`tests.infra.test_pool_profiles`, which cannot collide. The same applies to
test_reranker_degradation.py against tests/services/.
"""
