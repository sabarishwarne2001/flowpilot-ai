# Performance — measurements before launch (campaign session 4, §B)

Measured, not estimated. Each figure names the machine, the command and the date, so a later run
can be compared with it. The sandbox is a shared cloud container (4 vCPU class); a production VPS
of the size RUNBOOK §9.1 asks for is in the same range.

## Bulk delete of 500 documents (HANDOFF carry-over, CAMPAIGN-MAP §4.1)

- **Question:** does one `POST /workspaces/{id}/work-items/bulk` deleting 500 documents (the
  per-request maximum, `MAX_BULK_IDS`) finish well inside a proxy's timeout, or must it move onto
  the `work_items.bulk` job?
- **Run (2026-10-13):** the browser-test stack (uvicorn, the worker with every profile, Postgres 16,
  local storage), Enterprise tenant. 500 one-page PDFs uploaded through the real upload API (8 at a
  time), processing settled, then one bulk delete of all 500.
  Script: `backend/perf/bulk_delete_timing.py` (its docstring has the command).
- **Result:** HTTP 200 in **6.0 s**, 500 deleted, 0 refused (uploads took 67 s including processing).
- **Limits it must stay under:** production Caddy sets no response timeout on `/api/*`
  (`backend/deploy/Caddyfile`); uvicorn has none. A CDN in front would cut at 100 s (Cloudflare) or
  60 s (a typical load balancer). With object storage over the network (MinIO), each of the 500
  object deletions adds a few milliseconds: an estimated 7 to 9 s in production, still far inside.
- **Decision:** the bulk delete stays inline, with its per-document results in the response. The
  `work_items.bulk` job stays registered and unused (the ledger row is `blocked` with this reason);
  it is the path to take if `MAX_BULK_IDS` is ever raised far above 500.
