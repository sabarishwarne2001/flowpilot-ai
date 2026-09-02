"""ARCH-0V test package.

This file is required, not decorative. Without it, pytest's rootdir-relative
module naming collides when two test files share a basename across
directories — the same reason `tests/infra/__init__.py` was required in
ARCH-19. `test_isolation_matrix.py` here and any same-named file under
`tests/isolation/` would import as the same module and one would shadow the
other silently.
"""
