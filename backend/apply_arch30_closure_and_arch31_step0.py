"""ARCH-30 A9 closure + ARCH-31 Step 0 wiring — anchored, idempotent, atomic.

Same engine as `apply_arch30_tranche4.py` and `..._final.py`, `_merge_by_path`
included.

WHAT IT DOES
    A9   replaces the scaffolding in verify_arch30_tranche4_final.py with
         working seed helpers, and fixes three call-signature errors the
         schema audit exposed in gates_a9 itself.
    S0   registers `capability.reconciliation`, adds `CapabilityRequiredError`
         to the ARCH-01 error envelope, and exports the new modules.

PRECONDITION
    Refuses unless both Tranche 4 patchers have run and the Step 0 new files
    are in place.

USAGE
    python apply_arch30_closure_and_arch31_step0.py --check
    python apply_arch30_closure_and_arch31_step0.py
    python apply_arch30_closure_and_arch31_step0.py        # no-op
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SENTINEL_PREFIX = "ARCH31-S0"
Mode = Literal["after", "before", "replace"]


@dataclass
class Patch:
    anchor: str
    payload: str
    sentinel: str
    mode: Mode = "after"
    occurrences: int = 1
    index: int = 0
    note: str = ""


@dataclass
class FilePatches:
    relpath: str
    patches: list[Patch] = field(default_factory=list)


class PatchError(RuntimeError):
    pass


@dataclass
class _Decoded:
    text: str
    bom: bytes
    newline: str


def _decode(raw: bytes) -> _Decoded:
    bom = b""
    if raw.startswith(b"\xef\xbb\xbf"):
        bom, raw = b"\xef\xbb\xbf", raw[3:]
    text = raw.decode("utf-8")
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return _Decoded(text.replace("\r\n", "\n"), bom, "\r\n" if crlf > lf else "\n")


def _encode(dec: _Decoded, text: str) -> bytes:
    body = text.replace("\n", dec.newline) if dec.newline != "\n" else text
    return dec.bom + body.encode("utf-8")


def _apply_one(text: str, patch: Patch, relpath: str) -> tuple[str, bool]:
    marker = f"{SENTINEL_PREFIX}:{patch.sentinel}"
    if marker in text:
        return text, False
    count = text.count(patch.anchor)
    if count != patch.occurrences:
        raise PatchError(
            f"{relpath}: anchor for {patch.sentinel!r} appeared {count} "
            f"time(s), expected exactly {patch.occurrences}.\n"
            f"  Anchor begins: {patch.anchor.splitlines()[0][:96]!r}\n  {patch.note}"
        )
    start = -1
    for _ in range(patch.index + 1):
        start = text.index(patch.anchor, start + 1)
    end = start + len(patch.anchor)
    if patch.mode == "after":
        return text[:end] + patch.payload + text[end:], True
    if patch.mode == "before":
        return text[:start] + patch.payload + text[start:], True
    return text[:start] + patch.payload + text[end:], True


def _merge_by_path(groups: list[FilePatches]) -> list[FilePatches]:
    """Collapse groups targeting the same file. See Tranche 4 for the bug."""
    merged: dict[str, FilePatches] = {}
    order: list[str] = []
    for group in groups:
        if group.relpath not in merged:
            merged[group.relpath] = FilePatches(group.relpath, [])
            order.append(group.relpath)
        merged[group.relpath].patches.extend(group.patches)
    return [merged[p] for p in order]


def run(root: Path, groups: list[FilePatches], *, check: bool) -> int:
    staged: dict[Path, bytes] = {}
    applied = skipped = 0
    for group in _merge_by_path(groups):
        path = root / group.relpath
        if not path.exists():
            raise PatchError(f"missing file: {group.relpath}")
        dec = _decode(path.read_bytes())
        text = dec.text
        touched = False
        for patch in group.patches:
            text, did = _apply_one(text, patch, group.relpath)
            if did:
                applied += 1
                touched = True
                print(f"  + {group.relpath}: {patch.sentinel}")
            else:
                skipped += 1
                print(f"  = {group.relpath}: {patch.sentinel} (already present)")
        if touched:
            staged[path] = _encode(dec, text)
    if check:
        print(f"\n--check: {applied} would apply, {skipped} already present.")
        return 0
    for path, raw in staged.items():
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".s0tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    print(f"\nApplied {applied} patch(es) across {len(staged)} file(s); {skipped} present.")
    return 0


REQUIRED_NEW_FILES = [
    "backend/app/core/normalize.py",
    "backend/app/services/procurement_matching/__init__.py",
    "backend/app/services/procurement_matching/role_classifier.py",
    "backend/app/api/capability_gate.py",
    "backend/alembic/versions/arch31_step0_document_roles.py",
]


def assert_preconditions(root: Path) -> None:
    missing = [p for p in REQUIRED_NEW_FILES if not (root / p).exists()]
    if missing:
        raise PatchError("Step 0 new files are not in place:\n  " + "\n  ".join(missing))
    final = root / "backend/app/api/deps.py"
    if "ARCH30-T4F:api-key-billing-gate" not in final.read_text(encoding="utf-8-sig"):
        raise PatchError(
            "apply_arch30_tranche4_final.py has not been applied. Step 0 "
            "patches the verifier that tranche delivered."
        )


# ===========================================================================
# A9 — the real seed helpers
# ===========================================================================

A9_BODY = '''# ARCH31-S0:a9-real-gates — A9, with the schema audited rather than assumed.
#
# The first cut of these gates got three things wrong, all of which the live
# schema settled:
#
#   1. Inbound events are `app.models.stripe_inbound_event.StripeInboundEvent`
#      (table `stripe_inbound_events`, with a `gateway` column that carries
#      'DODO'), not a model in `app.models.billing`. The table name is a
#      historical artefact of ARCH-15 shipping before the second gateway; the
#      CHECK `ck_stripe_inbound_events_gateway_event_id_present` is what makes
#      a non-Stripe row legal, and it requires `gateway_event_id`.
#
#   2. `quota_tiers` marks published tiers with `published_at IS NOT NULL`,
#      not `is_published`.
#
#   3. `reconcile_row(db, row)` is positional and takes no gateway argument,
#      and `apply_tier_for_status(db, *, account, subscription)` takes a
#      BillingAccount rather than a status string. The gateway is reached
#      through `get_dodo_gateway()`, so stubbing it means substituting the
#      module attribute and restoring it in `finally` — a stub that leaks
#      into the next gate is worse than no stub.


class _StubbedDodo:
    """Gateway stub. Every A9 gate runs with HTTP disabled.

    A gate that reaches the network is not a gate; it passes or fails on
    somebody else's uptime. The snapshot is shaped exactly like
    `DodoSubscriptionSnapshot`, so the reconcile path still exercises real
    parsing, real SQL and real state-version ordering.
    """

    def __init__(self, snapshot: Any) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def fetch_subscription(self, subscription_id: str) -> Any:
        self.calls.append(subscription_id)
        return self.snapshot


def gates_a9(rec: Recorder, database_url: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(database_url, future=True)

    # -- A9.1 --------------------------------------------------------------
    def dodo_inbound_reconciles() -> None:
        from app.services.billing import dodo_gateway, dodo_reconcile_service

        with Session(engine) as db:
            outer = db.begin()
            original = dodo_reconcile_service.get_dodo_gateway
            try:
                org_id, tier_id = _seed_org_and_tier(db)
                now = datetime.now(timezone.utc)
                snapshot = dodo_gateway.DodoSubscriptionSnapshot(
                    id="sub_a9_active",
                    status="active",
                    customer_id="cus_a9",
                    customer_email="a9@example.test",
                    product_id="prod_a9",
                    quantity=3,
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=29),
                    cancel_at_next_billing_date=False,
                    cancelled_at=None,
                    currency="USD",
                    state_version=int(now.timestamp() * 1_000_000),
                )
                stub = _StubbedDodo(snapshot)
                dodo_reconcile_service.get_dodo_gateway = lambda: stub

                row = _seed_inbound_dodo_event(
                    db, org_id, "subscription.active", "sub_a9_active"
                )
                outcome = dodo_reconcile_service.reconcile_row(db, row)
                db.flush()

                assert stub.calls == ["sub_a9_active"], (
                    f"the reconciler did not re-fetch from the gateway; it "
                    f"called {stub.calls!r}. Trusting the webhook payload "
                    f"instead of re-fetching is the D-9 defect."
                )
                sub = _fetch_subscription(db, org_id)
                assert sub is not None, (
                    f"no subscription row after subscription.active "
                    f"(outcome={outcome!r})"
                )
                assert str(sub["gateway"]).upper() == "DODO", sub["gateway"]
                assert sub["billing_account_id"] is not None, (
                    "billing account was not adopted; a subscription with no "
                    "account cannot be invoiced"
                )
                assert sub["quota_tier_id"] is not None, (
                    "tier pin is NULL after reconcile, so resolve_tier falls "
                    "back and the customer silently loses the limits they "
                    "are paying for"
                )
            finally:
                dodo_reconcile_service.get_dodo_gateway = original
                outer.rollback()

    rec.check("A9.1 Dodo subscription.active reconciles to a pinned subscription", dodo_inbound_reconciles)

    # -- A9.2 --------------------------------------------------------------
    def stripe_cancellation_lapses_to_free() -> None:
        from app.models.billing_account import BillingAccount
        from app.models.subscription import Subscription
        from app.services.billing import subscription_service

        with Session(engine) as db:
            outer = db.begin()
            try:
                org_id, tier_id = _seed_org_and_tier(db)
                sub_id, account_id = _seed_stripe_subscription(
                    db, org_id, tier_id, status="canceled"
                )
                _pin_tier(db, org_id, tier_id)
                before = _pinned_tier_key(db, org_id)
                assert before is not None and before != "free", (
                    f"the fixture did not pin a paid tier (got {before!r}); "
                    f"the gate would pass vacuously"
                )

                subscription = db.get(Subscription, sub_id)
                account = db.get(BillingAccount, account_id)
                subscription_service.apply_tier_for_status(
                    db, account=account, subscription=subscription
                )
                db.flush()

                after = _pinned_tier_key(db, org_id)
                assert after == "free", (
                    f"organization is still pinned to {after!r} after "
                    f"cancellation. resolve_tier falls back to this pointer "
                    f"once no LIVE subscription exists, so a cancelled "
                    f"Business customer keeps Business limits for free, "
                    f"forever."
                )
            finally:
                outer.rollback()

    rec.check("A9.2 Stripe cancellation releases the tier pin to free", stripe_cancellation_lapses_to_free)

    # -- A9.3 --------------------------------------------------------------
    def addon_sweep_revokes_and_disables() -> None:
        from app.services.billing import addon_service

        with Session(engine) as db:
            outer = db.begin()
            try:
                org_id, _ = _seed_org_and_tier(db)
                grant_id = _seed_addon_grant(
                    db, org_id, "addon.custom_domain", state="ACTIVE"
                )
                domain_id = _seed_verified_domain(db, org_id)
                _seed_addon_grant(
                    db, org_id, "addon.warehouse_sync", state="ACTIVE"
                )
                schedule_id = _seed_export_schedule(db, org_id)
                now = datetime.now(timezone.utc)

                # The clock is advanced by passing `now`, not by mutating the
                # grant's timestamps. sweep() takes it as a parameter for
                # exactly this reason, and moving the data instead of the
                # clock would test a state the product never reaches.
                addon_service.sweep(db, now=now + timedelta(days=2))
                db.flush()
                state = _grant_state(db, grant_id)
                assert state == "GRACE", (
                    f"expected GRACE once the period ended, got {state!r}"
                )
                assert not _domain_is_revoked(db, domain_id), (
                    "the domain was revoked during GRACE. Grace exists so "
                    "existing resources keep working; revoking here removes "
                    "the difference between GRACE and LAPSED."
                )

                addon_service.sweep(db, now=now + timedelta(days=120))
                db.flush()
                state = _grant_state(db, grant_id)
                assert state == "LAPSED", (
                    f"expected LAPSED after grace expired, got {state!r}"
                )
                assert _domain_is_revoked(db, domain_id), (
                    "the verified domain survived a LAPSED add-on. The halt "
                    "effect is the product: an add-on that lapses without "
                    "revoking what it granted is a free tier with extra steps."
                )
                assert not _schedule_is_enabled(db, schedule_id), (
                    "the export schedule is still enabled after warehouse "
                    "sync lapsed; it will keep shipping data the tenant no "
                    "longer pays for"
                )
            finally:
                outer.rollback()

    rec.check("A9.3 sweep walks ACTIVE -> GRACE -> LAPSED and fires halt effects", addon_sweep_revokes_and_disables)


# ---------------------------------------------------------------------------
# A9 seed helpers
#
# Every one writes inside the caller's transaction and never commits. The
# gate's outer begin() is rolled back unconditionally in `finally`, so a gate
# that fails mid-way leaves the database exactly as it found it — which is
# what makes it safe to point --db at a development database with real rows.
#
# They reuse existing tenancy rows rather than creating organizations. A gate
# that invents its own tenant is not exercising the constraints production
# rows live under, and creating an organization pulls in seats, memberships
# and a billing account, which is most of a fixture framework.
# ---------------------------------------------------------------------------


def _sql(db: Any, statement: str, **params: Any) -> Any:
    from sqlalchemy import text as sa_text

    return db.execute(sa_text(statement), params)


def _seed_org_and_tier(db: Any) -> tuple[Any, Any]:
    org_id = _sql(
        db, "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
    ).scalar_one_or_none()
    tier_id = _sql(
        db,
        "SELECT id FROM quota_tiers WHERE published_at IS NOT NULL "
        "AND is_active = true AND tier_key <> 'free' "
        "ORDER BY created_at DESC LIMIT 1",
    ).scalar_one_or_none()
    if org_id is None or tier_id is None:
        raise AssertionError(
            "A9 needs one organization and one published, active, non-free "
            "quota tier in the target database. Run the development seed "
            "first. The non-free requirement is not fussiness: A9.2 asserts "
            "a pin RELEASES to free, and a fixture already on free would "
            "pass vacuously."
        )
    return org_id, tier_id


def _seed_inbound_dodo_event(db: Any, org_id: Any, event_type: str, sub_id: str) -> Any:
    """Insert a Dodo webhook row the reconciler will accept.

    `gateway_event_id` is NOT NULL for non-Stripe rows — that is
    `ck_stripe_inbound_events_gateway_event_id_present`, and it exists because
    Dodo's idempotency key arrives in the `webhook-id` header rather than in
    the body. `stripe_event_id` stays NULL, which the sibling CHECK permits
    only when gateway is not STRIPE.
    """
    from app.models.stripe_inbound_event import StripeInboundEvent

    now = datetime.now(timezone.utc)
    unique = uuid.uuid4().hex[:12]
    row = StripeInboundEvent(
        stripe_event_id=None,
        gateway="DODO",
        gateway_event_id=f"whk_a9_{unique}",
        event_type=event_type,
        api_version=None,
        stripe_created_at=now,
        livemode=False,
        payload={
            "type": event_type,
            "data": {
                "subscription_id": sub_id,
                "customer": {"customer_id": "cus_a9", "email": "a9@example.test"},
                "product_id": "prod_a9",
                "quantity": 3,
            },
        },
        signature_header="a9-stubbed-signature",
        organization_id=org_id,
    )
    db.add(row)
    db.flush()
    return row


def _seed_stripe_subscription(
    db: Any, org_id: Any, tier_id: Any, *, status: str
) -> tuple[Any, Any]:
    """A billing account plus a subscription in `status`, returning both ids."""
    account_id = _sql(
        db,
        "SELECT id FROM billing_accounts WHERE organization_id = :org LIMIT 1",
        org=str(org_id),
    ).scalar_one_or_none()
    if account_id is None:
        account_id = uuid.uuid4()
        _sql(
            db,
            "INSERT INTO billing_accounts "
            "(id, organization_id, gateway, stripe_customer_id, created_at, updated_at) "
            "VALUES (:id, :org, 'STRIPE', :cus, now(), now())",
            id=str(account_id),
            org=str(org_id),
            cus=f"cus_a9_{uuid.uuid4().hex[:10]}",
        )

    tier_key = _sql(
        db, "SELECT tier_key FROM quota_tiers WHERE id = :tier", tier=str(tier_id)
    ).scalar_one()
    price_book_id = _sql(
        db, "SELECT id FROM price_books ORDER BY created_at DESC LIMIT 1"
    ).scalar_one_or_none()

    sub_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO subscriptions (id, billing_account_id, gateway, status, "
        "quota_tier_key, quota_tier_id, price_book_id, seats_purchased, "
        "current_period_start, current_period_end, cancel_at_period_end, "
        "stripe_subscription_id, gateway_subscription_id, stripe_state_version, "
        "created_at, updated_at) "
        "VALUES (:id, :acct, 'STRIPE', :status, :tier_key, :tier_id, :book, 1, "
        "now() - interval '10 days', now() + interval '20 days', false, "
        ":sid, :sid, :ver, now(), now())",
        id=str(sub_id),
        acct=str(account_id),
        status=status,
        tier_key=tier_key,
        tier_id=str(tier_id),
        book=str(price_book_id) if price_book_id else None,
        sid=f"sub_a9_{uuid.uuid4().hex[:10]}",
        ver=int(datetime.now(timezone.utc).timestamp() * 1_000_000),
    )
    return sub_id, account_id


def _seed_addon_grant(db: Any, org_id: Any, key: str, *, state: str) -> Any:
    """An add-on grant whose period has just ended, ready for the sweep."""
    grant_id = uuid.uuid4()
    _sql(
        db, "DELETE FROM organization_addons WHERE organization_id = :org "
        "AND addon_key = :key",
        org=str(org_id), key=key,
    )
    _sql(
        db,
        "INSERT INTO organization_addons (id, organization_id, addon_key, status, "
        "purchase_status, gateway, gateway_state_version, current_period_end, "
        "created_at, updated_at) "
        "VALUES (:id, :org, :key, :status, 'ACTIVE', 'STRIPE', :ver, "
        "now() + interval '1 day', now(), now())",
        id=str(grant_id),
        org=str(org_id),
        key=key,
        status=state,
        ver=int(datetime.now(timezone.utc).timestamp() * 1_000_000),
    )
    return grant_id


def _seed_verified_domain(db: Any, org_id: Any) -> Any:
    domain_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO custom_domains (id, organization_id, hostname, status, "
        "challenge_token, consecutive_failures, is_primary, certificate_status, "
        "created_at, updated_at) "
        "VALUES (:id, :org, :host, 'VERIFIED', :tok, 0, false, 'ACTIVE', "
        "now(), now())",
        id=str(domain_id),
        org=str(org_id),
        host=f"a9-{uuid.uuid4().hex[:10]}.example.test",
        tok=uuid.uuid4().hex,
    )
    return domain_id


def _seed_export_schedule(db: Any, org_id: Any) -> Any:
    """A destination plus an enabled schedule on it.

    `warehouse_destinations` requires a label, a kind from the enumerated
    three, an ACTIVE status, a JSONB config and an encrypted credential with
    its fingerprint. The credential is a placeholder string: nothing in this
    gate decrypts it, and seeding a real one would need the KMS.
    """
    dest_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO warehouse_destinations (id, organization_id, label, kind, "
        "status, config, encrypted_credential, credential_fingerprint, "
        "created_at, updated_at) "
        "VALUES (:id, :org, :label, 'SNOWFLAKE', 'ACTIVE', '{}'::jsonb, "
        "'a9-placeholder-ciphertext', :fp, now(), now())",
        id=str(dest_id),
        org=str(org_id),
        label=f"A9 {uuid.uuid4().hex[:6]}",
        fp=uuid.uuid4().hex,
    )
    schedule_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO export_schedules (id, organization_id, destination_id, "
        "datasets, cadence, hour_utc, lookback_days, enabled, "
        "consecutive_failure_count, next_run_at, created_at, updated_at) "
        "VALUES (:id, :org, :dest, '[\\"USAGE_ROLLUPS\\"]'::jsonb, 'DAILY', 2, 1, "
        "true, 0, now() + interval '1 day', now(), now())",
        id=str(schedule_id),
        org=str(org_id),
        dest=str(dest_id),
    )
    return schedule_id


def _fetch_subscription(db: Any, org_id: Any) -> Any:
    return _sql(
        db,
        "SELECT s.* FROM subscriptions s "
        "JOIN billing_accounts a ON a.id = s.billing_account_id "
        "WHERE a.organization_id = :org ORDER BY s.created_at DESC LIMIT 1",
        org=str(org_id),
    ).mappings().first()


def _pin_tier(db: Any, org_id: Any, tier_id: Any) -> None:
    _sql(
        db,
        "UPDATE organizations SET quota_tier_id = :tier WHERE id = :org",
        tier=str(tier_id),
        org=str(org_id),
    )


def _pinned_tier_key(db: Any, org_id: Any) -> Any:
    return _sql(
        db,
        "SELECT t.tier_key FROM organizations o "
        "JOIN quota_tiers t ON t.id = o.quota_tier_id WHERE o.id = :org",
        org=str(org_id),
    ).scalar_one_or_none()


def _grant_state(db: Any, grant_id: Any) -> Any:
    return _sql(
        db, "SELECT status FROM organization_addons WHERE id = :id", id=str(grant_id)
    ).scalar_one_or_none()


def _domain_is_revoked(db: Any, domain_id: Any) -> bool:
    status = _sql(
        db, "SELECT status FROM custom_domains WHERE id = :id", id=str(domain_id)
    ).scalar_one_or_none()
    return str(status).upper() == "REVOKED"


def _schedule_is_enabled(db: Any, schedule_id: Any) -> bool:
    return bool(
        _sql(
            db, "SELECT enabled FROM export_schedules WHERE id = :id",
            id=str(schedule_id),
        ).scalar_one_or_none()
    )
'''


def build_patches() -> list[FilePatches]:
    groups: list[FilePatches] = []

    # -- A9: replace everything from gates_a9 to the end of the helpers ----
    groups.append(
        FilePatches(
            "backend/verify_arch30_tranche4_final.py",
            [
                Patch(
                    note="A9 section, from the banner to the last stub helper",
                    sentinel="a9-real-gates",
                    mode="replace",
                    # Both filled in by _resolve_a9_anchor from the file
                    # itself: the block is ~250 lines of scaffolding and
                    # hard-coding it here would double this script's size
                    # and make it brittle against whitespace.
                    anchor="",
                    payload="",
                ),
            ],
        )
    )

    # -- Step 0: entitlements ---------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/core/entitlements.py",
            [
                Patch(
                    note="capability key constant",
                    sentinel="capability-reconciliation-key",
                    anchor='WAREHOUSE_SYNC_ADDON: str = "addon.warehouse_sync"\n',
                    payload=(
                        '\n'
                        '#: ARCH31-S0:capability-reconciliation-key. Three-way procurement\n'
                        '#: matching. A CAPABILITY, not an ADDON, and the distinction is\n'
                        '#: load-bearing: add-ons are separately purchasable line items with a\n'
                        '#: price, a halt effect and a grace ladder, and `ADDON_KEYS` is\n'
                        '#: asserted equal to `entitlement_service`\'s catalog at import.\n'
                        '#: Capabilities are bundled into tiers and have no independent price,\n'
                        '#: so adding this to ADDON_KEYS would fail that assertion at boot.\n'
                        'RECONCILIATION_CAPABILITY: str = "capability.reconciliation"\n'
                        '\n'
                        '#: Every capability key. Disjoint from ADDON_KEYS by construction;\n'
                        '#: verify_arch31_step0 asserts the two sets never intersect.\n'
                        'CAPABILITY_KEYS: tuple[str, ...] = (RECONCILIATION_CAPABILITY,)\n'
                    ),
                ),
                Patch(
                    note="entitlement registration",
                    sentinel="capability-reconciliation-entitlement",
                    anchor=(
                        '    Entitlement(\n'
                        '        name=WAREHOUSE_SYNC_ADDON,\n'
                        '        description=(\n'
                        '            "Warehouse sync: register destinations and run scheduled exports. "\n'
                        '            "Bundled with Enterprise; purchasable on other plans."\n'
                        '        ),\n'
                        '    ),\n'
                    ),
                    payload=(
                        '    # ARCH31-S0:capability-reconciliation-entitlement\n'
                        '    Entitlement(\n'
                        '        name=RECONCILIATION_CAPABILITY,\n'
                        '        description=(\n'
                        '            "Procurement three-way matching: reconcile purchase orders, "\n'
                        '            "goods receipts and supplier invoices line by line, with "\n'
                        '            "tolerance policies and evidence. Bundled into a tier, not "\n'
                        '            "purchasable on its own."\n'
                        '        ),\n'
                        '    ),\n'
                    ),
                ),
                Patch(
                    note="entitlements __all__",
                    sentinel="capability-reconciliation-export",
                    anchor='    "ADDON_KEYS",\n',
                    payload=(
                        '    # ARCH31-S0:capability-reconciliation-export\n'
                        '    "CAPABILITY_KEYS",\n'
                        '    "RECONCILIATION_CAPABILITY",\n'
                    ),
                ),
            ],
        )
    )

    # -- Step 0: error envelope -------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/core/billing_errors.py",
            [
                Patch(
                    note="after AddonRequiredError",
                    sentinel="capability-required-error",
                    anchor=(
                        'class AddonRequiredError(BillingAccessError):\n'
                        '    """The operation needs an add-on the organization does not currently have."""\n'
                        '\n'
                        '    code = "ADDON_REQUIRED"\n'
                    ),
                    payload=(
                        '\n'
                        '\n'
                        '# ARCH31-S0:capability-required-error\n'
                        'class CapabilityRequiredError(BillingAccessError):\n'
                        '    """The operation needs a capability this tier does not include.\n'
                        '\n'
                        '    Distinct from `AddonRequiredError` because the remedy is\n'
                        '    different, and the console says so. An add-on can be bought on\n'
                        '    the current plan; a capability is bundled, so the only route to\n'
                        '    it is a plan change. Returning ADDON_REQUIRED for a capability\n'
                        '    would send the customer to a purchase flow that has nothing to\n'
                        '    sell them.\n'
                        '\n'
                        '    Carries the same ARCH-01 envelope — `{code, message, details}` —\n'
                        '    and the same 402, so `ApiError` handling on the frontend needs no\n'
                        '    new branch.\n'
                        '    """\n'
                        '\n'
                        '    code = "CAPABILITY_REQUIRED"\n'
                    ),
                ),
                Patch(
                    note="billing_errors __all__",
                    sentinel="capability-required-export",
                    mode="replace",
                    anchor='__all__ = ["AddonRequiredError", "BillingAccessError", "BillingReadOnlyError"]\n',
                    payload=(
                        '# ARCH31-S0:capability-required-export\n'
                        '__all__ = [\n'
                        '    "AddonRequiredError",\n'
                        '    "BillingAccessError",\n'
                        '    "BillingReadOnlyError",\n'
                        '    "CapabilityRequiredError",\n'
                        ']\n'
                    ),
                ),
            ],
        )
    )

    return groups


def _resolve_a9_anchor(root: Path, groups: list[FilePatches]) -> None:
    """Fill in the A9 replace anchor by reading the current file.

    The block spans ~250 lines of scaffolding, so hard-coding it in this
    script would double its size and make it brittle against whitespace. The
    boundaries are two unique landmarks this repo owns; everything between
    them is replaced wholesale.
    """
    path = root / "backend/verify_arch30_tranche4_final.py"
    text = path.read_text(encoding="utf-8-sig")
    if f"{SENTINEL_PREFIX}:a9-real-gates" in text:
        groups[0].patches[0].anchor = "\u0000never-matches\u0000"
        groups[0].patches[0].occurrences = 0
        groups[0].patches[0].payload = ""
        return
    start_marker = "def gates_a9(rec: Recorder, database_url: str) -> None:"
    end_marker = '    raise AssertionError("_schedule_is_enabled is not implemented in this delivery.")\n'
    if start_marker not in text or end_marker not in text:
        raise PatchError(
            "verify_arch30_tranche4_final.py does not contain the A9 "
            "scaffolding this patch replaces. Either it was already replaced "
            "by hand, or the file is not the one Tranche 4 FINAL delivered."
        )
    start = text.index(start_marker)
    # Walk back to the preceding section banner so the replacement owns it.
    banner = text.rindex("# ===========================================================================\n# A9", 0, start)
    end = text.index(end_marker) + len(end_marker)
    groups[0].patches[0].anchor = text[banner:end]
    groups[0].patches[0].payload = A9_BODY


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    root = Path(args.root).resolve() if args.root else here.parent

    print(f"ARCH-30 A9 closure + ARCH-31 Step 0 — root: {root}")
    try:
        assert_preconditions(root)
        groups = build_patches()
        _resolve_a9_anchor(root, groups)
        if groups[0].patches[0].occurrences == 0:
            groups.pop(0)
            print("  = backend/verify_arch30_tranche4_final.py: a9-real-gates (already present)")
        return run(root, groups, check=args.check)
    except PatchError as exc:
        print(f"\nREFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())