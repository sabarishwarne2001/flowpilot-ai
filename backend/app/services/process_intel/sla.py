"""ARCH49-S1:sla — SLA-breach prediction: gradient boosting, Brier-checked on held-out instances.

AN INSTANCE
===========
One object of an SLA object type (a review item, a case, a posting) from its
first event to its first terminal activity (`vocabulary.TERMINAL_ACTIVITIES`).
It is DUE at start + the workspace's target. It BREACHED if it ended after its
due time, or is still open past it; it is still OPEN and not yet due otherwise
(censored: its outcome is unknown, so it is predicted, never trained on).

TRAINING WITHOUT LOOKING AHEAD
==============================
Each instance with a known outcome contributes a snapshot at each LANDMARK age
((k + 1/2) / SNAPSHOTS_PER_INSTANCE of the target) at which it was still open:
the question a prediction answers in production ("still open at this age --
will it breach?"). Sampling each instance's own life instead would leak the
outcome (a late instance's snapshots would simply be older). A snapshot's features describe only what had
happened by then: its age and idle time against the target, how many events,
how many distinct activities, how much rework (a repeated activity), how many
by a person and by the agent, the hour of the week, the kind (review kind, case
template, posting object) and the last activity. Every fifth instance (a hash of
its id, so a re-run holds out the same ones) is held out with ALL its
snapshots: no snapshot of a held-out instance is ever trained on.

THE BRIER CHECK
===============
scikit-learn's HistGradientBoostingClassifier (1.9.0, already pinned) is fitted
on the training snapshots. On the held-out snapshots its Brier score is
compared with the base rate's (always predicting the training breach rate).
The model is ACCEPTED only if its Brier skill score, 1 - Brier / Brier(base),
exceeds MIN_SKILL -- otherwise the run is REFUSED with the reason and no
prediction is made: a model no better than the historical rate predicts
nothing. The migration's CHECK refuses an ACCEPTED run whose Brier does not
beat the baseline. The model used for predictions is the one that was
evaluated (trained on the training instances only). Nothing is pickled or
stored but the metrics: each run refits.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel import service
from app.services.process_intel import vocabulary as v

logger = logging.getLogger("app.services.process_intel.sla")

#: Instances are read from this far back.
LOOKBACK_DAYS = 180
MAX_CATEGORIES = 250
UNSEEN = 0


@dataclass
class Instance:
    object_id: str
    kind: str
    start: datetime
    end: Optional[datetime]
    events: list[tuple[datetime, str, str]] = field(default_factory=list)   # (at, activity, actor kind)
    work_item_id: Optional[str] = None


def due_at(instance: Instance, target: timedelta) -> datetime:
    return instance.start + target


def outcome(instance: Instance, target: timedelta, now: datetime) -> Optional[bool]:
    """True breached, False met, None still open and not yet due (censored)."""
    due = due_at(instance, target)
    if instance.end is not None:
        return instance.end > due
    if now > due:
        return True
    return None


def is_holdout(object_id: str) -> bool:
    return int(hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:8], 16) % v.HOLDOUT_EVERY == 0


def snapshot_times(instance: Instance, target: timedelta, now: datetime) -> list[datetime]:
    """LANDMARKS: the same ages for every instance -- (k + 1/2) / SNAPSHOTS_PER_INSTANCE of the target -- kept
    only while the instance was still open (and the moment has passed). Sampling each instance's OWN life
    instead would leak the outcome: a late instance's snapshots would be older, and age alone would
    separate the classes. At a landmark the question is the one asked in production: it is still open at this
    age -- will it breach?"""
    horizon = min(instance.end or now, now)
    n = v.SNAPSHOTS_PER_INSTANCE
    times = [instance.start + target * ((k + 0.5) / n) for k in range(n)]
    return [t for t in times if t < horizon]


@dataclass
class Encoders:
    kinds: dict[str, int]
    activities: dict[str, int]

    @staticmethod
    def build(instances: Sequence[Instance]) -> "Encoders":
        from collections import Counter

        kinds = Counter(i.kind for i in instances)
        activities = Counter(a for i in instances for _, a, _ in i.events)
        return Encoders(
            kinds={k: n + 1 for n, (k, _) in enumerate(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CATEGORIES])},
            activities={a: n + 1 for n, (a, _) in
                        enumerate(sorted(activities.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CATEGORIES])},
        )


def features(instance: Instance, at: datetime, target: timedelta, enc: Encoders) -> list[float]:
    seen = [(t, a, k) for t, a, k in instance.events if t <= at]
    target_seconds = max(target.total_seconds(), 1.0)
    distinct = {a for _, a, _ in seen}
    last_at = seen[-1][0] if seen else instance.start
    last_activity = seen[-1][1] if seen else ""
    return [
        (at - instance.start).total_seconds() / target_seconds,
        float(len(seen)),
        float(len(distinct)),
        float(len(seen) - len(distinct)),
        (at - last_at).total_seconds() / target_seconds,
        float(sum(1 for _, _, k in seen if k == v.ACTOR_PERSON)),
        float(sum(1 for _, _, k in seen if k == v.ACTOR_AGENT)),
        float(at.weekday() * 24 + at.hour),
        float(enc.kinds.get(instance.kind, UNSEEN)),
        float(enc.activities.get(last_activity, UNSEEN)),
    ]


@dataclass
class FitResult:
    status: str
    reason: Optional[str]
    instances_train: int = 0
    instances_holdout: int = 0
    snapshots_train: int = 0
    snapshots_holdout: int = 0
    base_rate: Optional[float] = None
    brier: Optional[float] = None
    brier_baseline: Optional[float] = None
    skill: Optional[float] = None
    reliability: list[dict[str, Any]] = field(default_factory=list)
    importances: dict[str, float] = field(default_factory=dict)
    model: Any = None
    encoders: Optional[Encoders] = None

    @property
    def accepted(self) -> bool:
        return self.status == v.RUN_ACCEPTED


def brier_score(p: Sequence[float], y: Sequence[int]) -> float:
    return float(sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)) if y else 0.0


def reliability(p: Sequence[float], y: Sequence[int], bins: int = v.RELIABILITY_BINS) -> list[dict[str, Any]]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, pi in enumerate(p) if (lo <= pi < hi) or (b == bins - 1 and pi == 1.0)]
        if idx:
            out.append({"bin": b, "low": round(lo, 3), "high": round(hi, 3), "count": len(idx),
                        "predicted": round(sum(p[i] for i in idx) / len(idx), 6),
                        "observed": round(sum(y[i] for i in idx) / len(idx), 6)})
    return out


def fit(instances: Sequence[Instance], target: timedelta, now: datetime) -> FitResult:
    """Train on the training instances, check on the held-out ones. Pure but for scikit-learn."""
    labelled = [(i, outcome(i, target, now)) for i in instances]
    known = [(i, y) for i, y in labelled if y is not None]
    train = [(i, y) for i, y in known if not is_holdout(i.object_id)]
    hold = [(i, y) for i, y in known if is_holdout(i.object_id)]
    result = FitResult(status=v.RUN_REFUSED, reason=None, instances_train=len(train), instances_holdout=len(hold))
    if len(train) < v.MIN_TRAIN_INSTANCES:
        result.reason = (f"Only {len(train)} finished instances to learn from; at least {v.MIN_TRAIN_INSTANCES} "
                         "are needed before a prediction means anything.")
        return result
    if len(hold) < v.MIN_HOLDOUT_INSTANCES:
        result.reason = (f"Only {len(hold)} held-out instances to check the model on; at least "
                         f"{v.MIN_HOLDOUT_INSTANCES} are needed.")
        return result
    if len({y for _, y in train}) < 2:
        result.reason = "Every instance learned from has the same outcome; there is nothing to tell apart."
        return result
    if len({y for _, y in hold}) < 2:
        result.reason = "Every held-out instance has the same outcome; the model cannot be checked against them."
        return result
    enc = Encoders.build([i for i, _ in train])
    x_train, y_train, x_hold, y_hold = [], [], [], []
    for inst, y in train:
        for t in snapshot_times(inst, target, now):
            x_train.append(features(inst, t, target, enc))
            y_train.append(int(y))
    for inst, y in hold:
        for t in snapshot_times(inst, target, now):
            x_hold.append(features(inst, t, target, enc))
            y_hold.append(int(y))
    result.snapshots_train, result.snapshots_holdout = len(x_train), len(x_hold)

    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier

    categorical = [v.FEATURES.index(name) for name in v.CATEGORICAL_FEATURES]
    model = HistGradientBoostingClassifier(
        max_iter=v.MODEL_MAX_ITER, learning_rate=v.MODEL_LEARNING_RATE, max_leaf_nodes=v.MODEL_MAX_LEAF_NODES,
        categorical_features=categorical, random_state=v.MODEL_RANDOM_STATE, early_stopping=False)
    model.fit(np.asarray(x_train, dtype=float), np.asarray(y_train, dtype=int))
    positive = list(model.classes_).index(1)
    p_hold = [float(p) for p in model.predict_proba(np.asarray(x_hold, dtype=float))[:, positive]]
    base = sum(y_train) / len(y_train)
    brier = brier_score(p_hold, y_hold)
    baseline = brier_score([base] * len(y_hold), y_hold)
    skill = 1.0 - brier / baseline if baseline > 0 else 0.0
    result.base_rate, result.brier, result.brier_baseline, result.skill = base, brier, baseline, skill
    result.reliability = reliability(p_hold, y_hold)
    try:
        from sklearn.inspection import permutation_importance

        imp = permutation_importance(model, np.asarray(x_hold, dtype=float), np.asarray(y_hold, dtype=int),
                                     scoring="neg_brier_score", n_repeats=3, random_state=v.MODEL_RANDOM_STATE)
        result.importances = {name: round(float(val), 6) for name, val in zip(v.FEATURES, imp.importances_mean)}
    except Exception:  # noqa: BLE001 - importances are a courtesy, never a refusal
        result.importances = {}
    if skill > v.MIN_SKILL and brier < baseline:
        result.status, result.model, result.encoders = v.RUN_ACCEPTED, model, enc
    else:
        result.reason = (f"The model's held-out Brier score {brier:.4f} does not beat the base rate's {baseline:.4f} "
                         f"by enough (skill {skill:.3f}, needs more than {v.MIN_SKILL}); no predictions are made.")
    return result


def predict(result: FitResult, instances: Sequence[Instance], target: timedelta, now: datetime) -> dict[str, float]:
    """P(breach) for every instance that is still open and not yet due."""
    import numpy as np

    if not result.accepted or result.model is None or result.encoders is None:
        return {}
    open_ones = [i for i in instances if outcome(i, target, now) is None]
    if not open_ones:
        return {}
    x = np.asarray([features(i, now, target, result.encoders) for i in open_ones], dtype=float)
    positive = list(result.model.classes_).index(1)
    return {i.object_id: float(p) for i, p in zip(open_ones, result.model.predict_proba(x)[:, positive])}


# ---------------------------------------------------------------------------
# From the event log
# ---------------------------------------------------------------------------

_KIND_ATTRIBUTE = {v.OBJECT_REVIEW_ITEM: ("review.opened", "kind"), v.OBJECT_CASE: ("case.opened", "template_id"),
                   v.OBJECT_POSTING: ("posting.planned", "object_kind")}


def work_steps(steps: Sequence[Any]) -> list[Any]:
    """The work, without what process intelligence itself wrote (vocabulary.SELF_ACTIVITY_PREFIXES: the agent's
    proposal bookkeeping, the SLA alert). ARCH49-S1:no-feedback.

    A prediction describes the work. Were its own alert or the agent's proposals features, raising the alert
    would move the very prediction that raised it -- a loop -- and every alerted or proposed-on item would
    carry an activity no finished item was trained on. What the agent DID to the work (a decision it applied)
    is still there: the owning source records the decision itself (review.decided, finding.confirmed)."""
    return [s for s in steps if not s.activity.startswith(v.SELF_ACTIVITY_PREFIXES)]


def load_instances(db: Session, *, workspace_id: uuid.UUID, object_type: str, now: datetime) -> list[Instance]:
    from app.services.process_intel import discovery

    traces, _ = discovery.load_traces(db, workspace_id=workspace_id, object_type=object_type,
                                      since=now - timedelta(days=LOOKBACK_DAYS), until=now)
    activity, attribute = _KIND_ATTRIBUTE[object_type]
    kinds = dict(db.execute(text(
        "SELECT o.object_id::text, min(e.attributes->>:attr) FROM process_event_objects o "
        "JOIN process_events e ON e.id = o.event_id WHERE o.workspace_id = :w AND o.object_type = :t "
        "AND o.activity = :a GROUP BY o.object_id"),
        {"w": workspace_id, "t": object_type, "a": activity, "attr": attribute}).all())
    documents = dict(db.execute(text(
        "SELECT a.object_id::text, min(b.object_id::text) FROM process_event_objects a JOIN process_event_objects b "
        "ON b.event_id = a.event_id AND b.object_type = 'DOCUMENT' AND b.qualifier = '' "
        "WHERE a.workspace_id = :w AND a.object_type = :t GROUP BY a.object_id"),
        {"w": workspace_id, "t": object_type}).all()) if object_type != v.OBJECT_DOCUMENT else {}
    terminal = set(v.TERMINAL_ACTIVITIES[object_type])
    out = []
    for trace in traces:
        steps = work_steps(trace.ordered())
        if not steps:
            continue
        end = next((s.at for s in steps if s.activity in terminal), None)
        events = [(s.at, s.activity, s.actor_kind) for s in steps if end is None or s.at <= end]
        out.append(Instance(object_id=trace.object_id, kind=kinds.get(trace.object_id) or "", start=steps[0].at,
                            end=end, events=events, work_item_id=documents.get(trace.object_id)))
    return out


def _q(value: Optional[float], places: str) -> Optional[Decimal]:
    return None if value is None else Decimal(repr(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def run(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, object_type: str,
        at: Optional[datetime] = None, instances: Optional[list[Instance]] = None) -> dict[str, Any]:
    """Fit, record the run, write the predictions, raise each AT_RISK alert once. Does not commit."""
    from app.models.process_intel import ProcessModelRun

    now = at or service.now()
    policy = service.sla_policy(db, workspace_id=workspace_id, object_type=object_type)
    target = timedelta(hours=int(policy["target_hours"]))
    instances = instances if instances is not None else load_instances(db, workspace_id=workspace_id,
                                                                        object_type=object_type, now=now)
    result = fit(instances, target, now)
    row = ProcessModelRun(
        id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, object_type=object_type,
        status=result.status, reason=result.reason, target_hours=int(policy["target_hours"]),
        instances_train=result.instances_train, instances_holdout=result.instances_holdout,
        snapshots_train=result.snapshots_train, snapshots_holdout=result.snapshots_holdout,
        base_rate=_q(result.base_rate, "0.00001"), brier=_q(result.brier, "0.000001"),
        brier_baseline=_q(result.brier_baseline, "0.000001"), skill=_q(result.skill, "0.00001"),
        reliability=result.reliability, importances=result.importances, engine_version=v.ENGINE_VERSION,
        trained_at=now)
    db.add(row)
    db.flush()
    predicted = predict(result, instances, target, now)
    alerts = write_predictions(db, workspace_id=workspace_id, organization_id=organization_id,
                               object_type=object_type, run_id=row.id, instances=instances, probabilities=predicted,
                               target=target, now=now, threshold=float(policy["at_risk_probability"]),
                               alerts=bool(policy["alerts_enabled"]), accepted=result.accepted)
    return {"object_type": object_type, "run_id": str(row.id), "status": result.status, "reason": result.reason,
            "brier": result.brier, "brier_baseline": result.brier_baseline, "skill": result.skill,
            "instances_train": result.instances_train, "instances_holdout": result.instances_holdout,
            "predicted": len(predicted), "alerts": alerts}


def write_predictions(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, object_type: str,
                      run_id: uuid.UUID, instances: Sequence[Instance], probabilities: dict[str, float],
                      target: timedelta, now: datetime, threshold: float, alerts: bool, accepted: bool) -> int:
    """Replace the object type's predictions: open instances (predicted or, past due, BREACHED); a refused run
    leaves only the BREACHED ones (a stale model's AT_RISK would mislead). Returns the alerts raised."""
    from app.services import outbox_service

    previous = {str(r[0]): (r[1], r[2]) for r in db.execute(text(
        "SELECT object_id, due_at, alerted_at FROM process_predictions WHERE workspace_id = :w AND object_type = :t"),
        {"w": workspace_id, "t": object_type}).all()}
    db.execute(text("DELETE FROM process_predictions WHERE workspace_id = :w AND object_type = :t"),
               {"w": workspace_id, "t": object_type})
    raised = 0
    rows = []
    for inst in instances:
        if inst.end is not None:
            continue
        due = due_at(inst, target)
        if now > due:
            state, probability = v.PREDICTION_BREACHED, 1.0
        elif accepted and inst.object_id in probabilities:
            probability = probabilities[inst.object_id]
            state = v.PREDICTION_AT_RISK if probability >= threshold else v.PREDICTION_OK
        else:
            continue
        prior = previous.get(inst.object_id)
        alerted_at = prior[1] if prior and prior[0] == due else None
        if state == v.PREDICTION_AT_RISK and alerted_at is None and alerts:
            outbox_service.emit_trigger(
                db, organization_id=organization_id, workspace_id=workspace_id, event_type=v.EVENT_SLA_AT_RISK,
                resource_id=uuid.UUID(inst.object_id),
                idempotency_key=f"{v.EVENT_SLA_AT_RISK}:{object_type}:{inst.object_id}:{due.isoformat()}"[:200],
                payload={"object_type": object_type, "object_id": inst.object_id, "kind": inst.kind or None,
                         "probability": round(probability, 4), "due_at": due.isoformat(),
                         "target_hours": round(target.total_seconds() / 3600, 3),
                         "age_hours": round((now - inst.start).total_seconds() / 3600, 3),
                         "work_item_id": inst.work_item_id})
            alerted_at = now
            raised += 1
        rows.append({"w": workspace_id, "o": organization_id, "t": object_type, "id": uuid.UUID(inst.object_id),
                     "run": run_id, "kind": (inst.kind or None) if len(inst.kind or "") <= 16 else None,
                     "start": inst.start, "due": due, "p": _q(probability, "0.00001"), "state": state, "at": now,
                     "alerted": alerted_at})
    if rows:
        db.execute(text(
            "INSERT INTO process_predictions (workspace_id, organization_id, object_type, object_id, run_id, kind, "
            "started_at, due_at, probability, state, predicted_at, alerted_at) VALUES (:w, :o, :t, :id, :run, :kind, "
            ":start, :due, :p, :state, :at, :alerted)"), rows)
    return raised


def latest_runs(db: Session, *, workspace_id: uuid.UUID) -> dict[str, Optional[dict[str, Any]]]:
    out: dict[str, Optional[dict[str, Any]]] = {}
    for object_type in v.SLA_OBJECT_TYPES:
        row = db.execute(text(
            "SELECT id, status, reason, target_hours, instances_train, instances_holdout, snapshots_train, "
            "snapshots_holdout, base_rate, brier, brier_baseline, skill, reliability, importances, trained_at "
            "FROM process_model_runs WHERE workspace_id = :w AND object_type = :t ORDER BY trained_at DESC LIMIT 1"),
            {"w": workspace_id, "t": object_type}).mappings().first()
        out[object_type] = None if row is None else {
            **{k: row[k] for k in ("status", "reason", "target_hours", "instances_train", "instances_holdout",
                                   "snapshots_train", "snapshots_holdout", "reliability", "importances",
                                   "trained_at")},
            "id": str(row["id"]),
            **{k: (float(row[k]) if row[k] is not None else None)
               for k in ("base_rate", "brier", "brier_baseline", "skill")}}
    return out


__all__ = ["FitResult", "Instance", "brier_score", "due_at", "features", "fit", "is_holdout", "latest_runs",
           "load_instances", "outcome", "predict", "reliability", "run", "snapshot_times", "write_predictions"]
