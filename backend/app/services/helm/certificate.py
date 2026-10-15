"""Certificates: the canonical, hashed record of one Guard decision.

A certificate is canonical JSON (sorted keys, no spaces, money as integer micros, dates as ISO
strings) of: the action, the digests of the inputs it relied on, every check with its result and
evidence, the mandate versions in force, the snapshot's hash, the engine version and the time.
Its SHA-256 is computed over exactly that text, so anyone holding the JSON can recompute the hash
and see it was not altered. Bank accounts only ever appear masked (last four characters).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from app.services.helm import ledger
from app.services.helm import vocabulary as v
from app.services.helm.guard import ActionView, Actor, GuardResult
from app.services.helm.mandates import base
from app.services.helm.mandates.registry import spec_hash
from app.services.helm.snapshot import Snapshot

FORMAT = "helm-certificate/1"


@dataclass(frozen=True)
class Certificate:
    canonical: str
    sha256: str
    passed: bool
    stage: str

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.canonical)


def issue(*, action: ActionView, result: GuardResult, snapshot: Snapshot, mandates: Sequence[base.ActiveMandate],
          actor: Actor, stage: str, issued_at: datetime, plan_id: Optional[str] = None) -> Certificate:
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    inputs: dict[str, Any] = {"snapshot_digest": snapshot.digest}
    facts: dict[str, Any] = {}
    if payable is not None:
        facts = {"document": payable.document, "document_number": payable.document_number,
                 "counterparty": payable.counterparty_name, "currency": payable.currency,
                 "amount_micros": payable.amount_micros, "due_date": payable.due_date,
                 "invoice_date": payable.invoice_date, "bank_account": payable.bank_account,
                 "confidence": payable.confidence, "settlement": payable.settlement_state,
                 "msme_deadline": payable.msme_deadline}
        inputs["facts_digest"] = ledger.sha256(facts)
    body = {
        "format": FORMAT,
        "stage": stage,
        "issued_at": issued_at,
        "engine_version": v.ENGINE_VERSION,
        "book_id": snapshot.book_id,
        "organization_id": snapshot.organization_id,
        "plan_id": plan_id,
        "action": action.as_json(),
        "facts": facts,
        "inputs": inputs,
        "snapshot": {"digest": snapshot.digest, "as_of": snapshot.as_of},
        "mandates": [{"mandate_id": m.id, "version": m.version, "template": m.template, "status": m.status,
                      "spec_hash": spec_hash(m.spec)} for m in sorted(mandates, key=lambda m: m.id)],
        "actor": {"user_id": actor.user_id, "role": actor.role},
        "checks": [c.as_json() for c in result.checks],
        "passed": result.passed,
        "needs_approval": result.needs_approval,
    }
    text = ledger.canonical(body)
    return Certificate(canonical=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), passed=result.passed,
                       stage=stage)


def verify(canonical_text: str, sha256: str) -> bool:
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest() == sha256


def masked(body: dict[str, Any]) -> dict[str, Any]:
    """The certificate as a person without access to the document's workspace sees it: every fact
    from the document replaced by "hidden", the checks' evidence dropped, the structure kept."""
    out = json.loads(json.dumps(body))
    for key in list((out.get("facts") or {}).keys()):
        out["facts"][key] = "hidden"
    action = out.get("action") or {}
    for key in ("title", "counterparty_key", "amount_micros", "work_item_id", "promise_id"):
        if key in action:
            action[key] = "hidden"
    for item in out.get("checks") or []:
        item["evidence"] = {}
        item["detail"] = "hidden: you cannot read this document's workspace"
    out["masked"] = True
    return out


__all__ = ["Certificate", "FORMAT", "issue", "masked", "verify"]
