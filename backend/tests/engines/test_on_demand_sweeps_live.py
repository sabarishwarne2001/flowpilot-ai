"""The on-demand sweeps that need no outside system run by hand to success (campaign session 2, brief D).

    pytest tests/engines/test_on_demand_sweeps_live.py -q

`process.sweep_workspace` (enqueued by the process-intelligence cron for each enabled
workspace) and `revops.sweep` (the daily revenue-operations sweep, also started from the
RevOps console) run through their real handlers on an organization with processed documents.
"""

from __future__ import annotations

import uuid

from app.services import job_service
from tests.engines.conftest import Engines, drain


def test_the_process_and_revops_sweeps_run_to_success(engines: Engines) -> None:
    engines.plan("enterprise")
    for n in range(3):
        engines.process(f"INV-SW-{n}.pdf", [["TAX INVOICE", f"Invoice Number: INV-SW-{n}", "Total: 10.00 USD"]],
                        marker=f"INV-SW-{n}", classification="Invoice", entities={"invoice_number": f"INV-SW-{n}"})
    job_service.enqueue(engines.db, job_type="process.sweep_workspace", organization_id=engines.org,
                        payload={"workspace_id": str(engines.ws)}, idempotency_key=f"process:{uuid.uuid4()}")
    job_service.enqueue(engines.db, job_type="revops.sweep", organization_id=None, payload={},
                        idempotency_key=f"revops:{uuid.uuid4()}")
    engines.db.commit()

    runs = drain(only=["process.sweep_workspace", "revops.sweep"])  # raises if either failed
    assert sorted(run.job_type for run in runs) == ["process.sweep_workspace", "revops.sweep"], runs
    assert all((run.result or {}).get("outcome") != "SKIPPED" for run in runs), [run.result for run in runs]
