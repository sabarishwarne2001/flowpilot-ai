"""ARCH-50 — RevOps: plan price books (annual intervals, INR), promo codes, invoiced enterprise contracts and
revenue metrics.

ARCH50-S1:revops

    vocabulary   the closed sets (mirrored by the migration's CHECKs; verify_arch50 T2 proves the parity)
    service      the milestone's one clock, the error envelope, money helpers
    price_books  DRAFT -> PUBLISHED (immutable, digested; one per currency) -> RETIRED; resolve a plan price
    promos       quote, reserve at checkout, redeem when the subscription or contract starts, release, expire
    contracts    invoiced enterprise contracts: activate (assigns the plan), issue invoices per period
                 (idempotent), pay, void, end; the daily sweep
    metrics      MRR / ARR per currency from live subscriptions and active contracts, movements, receivables,
                 and the frozen monthly snapshots
"""
