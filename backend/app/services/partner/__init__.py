"""ARCH-27 — partner tenancy, revenue share and the signed marketplace.

Three modules, split along the boundary of what each is authoritative about:

    tenancy_service      who a partner may see        (invariants 1, 2)
    rev_share_service    what a partner is owed       (invariants 3, 4)
    marketplace_service  what a tenant may run        (invariants 5, 6)

The split matters for the gate. `verify_arch27.py` G6 walks every public
function in `tenancy_service` and `rev_share_service` asserting a book-scope
or organization predicate on every query, with a short, named exemption list.
Folding all three into one module would make that check either unenforceably
broad or riddled with exemptions.

Nothing is imported eagerly here. `app/services/partner/marketplace_service`
pulls in `cryptography.hazmat` and the ARCH-13 graph validator, neither of
which belongs in the import graph of a worker that only settles payouts.
"""

from __future__ import annotations

__all__ = [
    "marketplace_service",
    "rev_share_service",
    "tenancy_service",
]