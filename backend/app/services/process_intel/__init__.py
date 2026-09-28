"""ARCH49-S1:process-intel — Process Intelligence & the Governed Exception Agent.

    vocabulary   the closed names, limits and defaults (pure; imports nothing of the app)
    service      THE clock (`now()`), the capability gate, SLA policies
    sources      what already records activity, read into events (outbox, audit, jobs, the
                 review hub, executions, cases, postings, radar findings, documents, the agent)
    ingest       the incremental, idempotent build of the object-centric event log
    mining       directly-follows graphs, variants and their timings (pure)
    petri        Petri nets and token replay (pure)
    conformance  flows (ARCH-37) and case templates (ARCH-43) as nets, replayed
    sla          SLA-breach prediction: gradient boosting, Brier-checked on held-out instances
    cost         cost-to-serve from ARCH-18/24's cost truth
    discovery    the discovery read model (traces from the log, mined, costed)
    agent/       the governed exception agent (see agent/__init__.py)

Enterprise only, ONE key: capability.process_intelligence (the agent rides it).
This file imports nothing, so the pure modules load offline.
"""
