#!/usr/bin/env python3
"""ARCH-30 Tranche 4 FINAL — anchored, idempotent, atomic patcher (A4, A5, A6, A7, A8).

Same engine as `apply_arch30_tranche4.py`, including `_merge_by_path` — the
fix for the bug where two patch groups naming the same file both resolved
against the on-disk bytes and the second staged write silently discarded the
first group's patches.

PRECONDITION
    Refuses unless `apply_arch30_tranche4.py` (A1 + A3) has already run. A9's
    gates live in the verifier and need nothing from here; A2 is deferred to
    ARCH-31 Step 0, where `app/core/normalize.py` gives it somewhere to live.

USAGE
    python apply_arch30_tranche4_final.py --check
    python apply_arch30_tranche4_final.py
    python apply_arch30_tranche4_final.py          # again: no-op, exit 0
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SENTINEL_PREFIX = "ARCH30-T4F"

Mode = Literal["after", "before", "replace"]


# ===========================================================================
# Engine (identical to Tranche 4's, _merge_by_path included)
# ===========================================================================


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
    encoding: str
    bom: bytes
    newline: str


def _decode(raw: bytes) -> _Decoded:
    bom = b""
    if raw.startswith(b"\xef\xbb\xbf"):
        bom = b"\xef\xbb\xbf"
        raw = raw[3:]
    text = raw.decode("utf-8")
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    newline = "\r\n" if crlf > lf else "\n"
    return _Decoded(
        text=text.replace("\r\n", "\n"), encoding="utf-8", bom=bom, newline=newline
    )


def _encode(dec: _Decoded, text: str) -> bytes:
    body = text.replace("\n", dec.newline) if dec.newline != "\n" else text
    return dec.bom + body.encode(dec.encoding)


def _apply_one(text: str, patch: Patch, relpath: str) -> tuple[str, bool]:
    marker = f"{SENTINEL_PREFIX}:{patch.sentinel}"
    if marker in text:
        return text, False

    count = text.count(patch.anchor)
    if count != patch.occurrences:
        raise PatchError(
            f"{relpath}: anchor for {patch.sentinel!r} appeared {count} "
            f"time(s), expected exactly {patch.occurrences}.\n"
            f"  Anchor begins: {patch.anchor.splitlines()[0][:96]!r}\n"
            f"  {patch.note}"
        )

    start = -1
    for _ in range(patch.index + 1):
        start = text.index(patch.anchor, start + 1)
    end = start + len(patch.anchor)

    if patch.mode == "after":
        return text[:end] + patch.payload + text[end:], True
    if patch.mode == "before":
        return text[:start] + patch.payload + text[start:], True
    if patch.mode == "replace":
        return text[:start] + patch.payload + text[end:], True
    raise PatchError(f"{relpath}: unknown mode {patch.mode!r}")


def _merge_by_path(groups: list[FilePatches]) -> list[FilePatches]:
    """Collapse groups that target the same file into one.

    `run` resolves every group against the bytes ON DISK and stages the
    result, because staging is what makes the run atomic. Two groups naming
    the same path would therefore both read the original bytes, and the
    second one's staged content — holding only its own patches — would
    overwrite the first. Merging keeps `build_patches` organised by ARCH item
    while the engine stays correct.
    """
    merged: dict[str, FilePatches] = {}
    order: list[str] = []
    for group in groups:
        if group.relpath not in merged:
            merged[group.relpath] = FilePatches(group.relpath, [])
            order.append(group.relpath)
        merged[group.relpath].patches.extend(group.patches)
    return [merged[relpath] for relpath in order]


def run(root: Path, groups: list[FilePatches], *, check: bool) -> int:
    staged: dict[Path, bytes] = {}
    applied = 0
    skipped = 0

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
        print(
            f"\n--check: {applied} patch(es) would apply, "
            f"{skipped} already present. Nothing written."
        )
        return 0

    for path, raw in staged.items():
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".t4ftmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    print(
        f"\nApplied {applied} patch(es) across {len(staged)} file(s); "
        f"{skipped} already present."
    )
    return 0


REQUIRED_NEW_FILES = [
    "backend/app/services/identity/security_emitters.py",
    "frontend/src/components/billing/MemberAccessNotice.tsx",
]


def assert_preconditions(root: Path) -> None:
    missing = [p for p in REQUIRED_NEW_FILES if not (root / p).exists()]
    if missing:
        raise PatchError(
            "Tranche 4 FINAL new files are not in place. Drop them in first:\n  "
            + "\n  ".join(missing)
        )
    sync = (root / "backend/app/services/analytics/sync_service.py")
    if not sync.exists() or "ARCH30-T4:compute-next-run-tz" not in sync.read_text(
        encoding="utf-8-sig"
    ):
        raise PatchError(
            "apply_arch30_tranche4.py (A1 + A3) has not been applied. This "
            "script patches files that run modified, and applying it to an "
            "unpatched tree will mis-anchor."
        )


# ===========================================================================
# The patches
# ===========================================================================


def build_patches() -> list[FilePatches]:
    groups: list[FilePatches] = []

    # =====================================================================
    # A6 — billing write gate coverage for API keys
    # =====================================================================
    groups.append(
        FilePatches(
            "backend/app/api/deps.py",
            [
                Patch(
                    note="require_api_key final return",
                    sentinel="api-key-billing-gate",
                    mode="before",
                    anchor=(
                        '    return PublicApiPrincipal(\n'
                        '        api_key=key,\n'
                        '        membership=membership,\n'
                        '        organization=organization,\n'
                        '        tier=tier,\n'
                        '        ef_search=ef_search_for(tier),\n'
                        '    )\n'
                    ),
                    payload=(
                        '    # ARCH30-T4F:api-key-billing-gate — A6.\n'
                        '    #\n'
                        '    # The D-11 audit found the read-only gate wired into\n'
                        '    # `get_organization_context`, `get_sso_compliant_organization_context`\n'
                        '    # and `get_workspace_context` — and nowhere on the API-key path. A\n'
                        '    # LAPSED organization could still POST /v1/query (which spends LLM\n'
                        '    # and embedding quota) and POST /v1/workflows/{id}/trigger (which\n'
                        '    # commits to the outbox) with a key minted while it was paying.\n'
                        '    #\n'
                        '    # Gated HERE, in the shared dependency, rather than route by route.\n'
                        '    # `assert_billing_writes_allowed` already returns immediately for\n'
                        '    # non-mutating methods, so reads are untouched, and every public API\n'
                        '    # route that exists now or is added later is covered without anybody\n'
                        '    # having to remember. `verify_arch30_tranche4_final --gates a6` walks\n'
                        '    # the live route table and fails if a mutating API-key route ever\n'
                        '    # resolves without passing through this dependency.\n'
                        '    from app.api.billing_write_gate import assert_billing_writes_allowed\n'
                        '\n'
                        '    assert_billing_writes_allowed(\n'
                        '        request, db, organization_id=organization.id\n'
                        '    )\n'
                        '\n'
                    ),
                ),
            ],
        )
    )

    # =====================================================================
    # A5 — member-readable access summary
    # =====================================================================
    groups.append(
        FilePatches(
            "backend/app/schemas/invoice.py",
            [
                Patch(
                    note="after BillingAccessResponse",
                    sentinel="access-summary-schema",
                    mode="before",
                    anchor="class CheckoutSessionRequest(BaseModel):\n",
                    payload=(
                        '# ARCH30-T4F:access-summary-schema — A5.\n'
                        'class BillingAccessSummaryResponse(BaseModel):\n'
                        '    """What an ordinary member may know about the account\'s health.\n'
                        '\n'
                        '    Three fields, and the omissions are the design. No amounts, no\n'
                        '    invoice ids, no dunning step history, no subscription status, no\n'
                        '    gateway identifiers. A member needs to understand why an upload\n'
                        '    was refused and roughly how long they have; none of the commercial\n'
                        '    detail in `BillingAccessResponse` helps with that, and all of it\n'
                        '    would be visible to every seat in the organization.\n'
                        '\n'
                        '    `state` is deliberately a three-value vocabulary of its own rather\n'
                        '    than the internal access state passed through. The internal states\n'
                        '    carry dunning semantics that would leak commercial position by\n'
                        '    their names alone; RESTRICTED says what a member experiences.\n'
                        '    """\n'
                        '\n'
                        '    state: Literal["ACTIVE", "GRACE", "RESTRICTED"]\n'
                        '    is_read_only: bool = Field(\n'
                        '        description="True when writes are refused. Reads and export "\n'
                        '        "always continue."\n'
                        '    )\n'
                        '    grace_ends_at: Optional[datetime] = Field(\n'
                        '        default=None,\n'
                        '        description="When the grace window closes, if there is one. "\n'
                        '        "Null in ACTIVE and in RESTRICTED, where it has already "\n'
                        '        "closed.",\n'
                        '    )\n'
                        '\n'
                        '\n'
                    ),
                ),
                Patch(
                    note="invoice.py typing import",
                    sentinel="access-summary-typing",
                    mode="replace",
                    anchor="from typing import Any, Optional\n",
                    payload=(
                        "# ARCH30-T4F:access-summary-typing — A5.\n"
                        "from typing import Any, Literal, Optional\n"
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "backend/app/api/v1/billing.py",
            [
                Patch(
                    note="billing.py deps import",
                    sentinel="access-summary-deps-import",
                    mode="replace",
                    anchor=(
                        "from app.api.deps import OrganizationContext, RequireOrgOwner, RequireOrgRole, get_db\n"
                    ),
                    payload=(
                        "# ARCH30-T4F:access-summary-deps-import — A5.\n"
                        "from app.api.deps import (\n"
                        "    OrganizationContext,\n"
                        "    RequireOrgMember,\n"
                        "    RequireOrgOwner,\n"
                        "    RequireOrgRole,\n"
                        "    get_db,\n"
                        ")\n"
                    ),
                ),
                Patch(
                    note="billing.py schema import",
                    sentinel="access-summary-schema-import",
                    mode="replace",
                    anchor=(
                        "from app.schemas.invoice import (\n"
                        "    BillingAccessResponse,\n"
                    ),
                    payload=(
                        "from app.schemas.invoice import (\n"
                        "    BillingAccessResponse,\n"
                        "    # ARCH30-T4F:access-summary-schema-import — A5.\n"
                        "    BillingAccessSummaryResponse,\n"
                    ),
                ),
                Patch(
                    note="after get_billing_access",
                    sentinel="access-summary-route",
                    anchor=(
                        '        subscription_status=(\n'
                        '            (live.status.value if hasattr(live.status, "value") else str(live.status))\n'
                        '            if live\n'
                        '            else None\n'
                        '        ),\n'
                        '        grace_ends_at=live.grace_ends_at if live else None,\n'
                        '    )\n'
                    ),
                    payload=(
                        '\n'
                        '\n'
                        '# ARCH30-T4F:access-summary-route — A5.\n'
                        '@router.get(\n'
                        '    "/organizations/{organization_id}/billing/access-summary",\n'
                        '    response_model=BillingAccessSummaryResponse,\n'
                        '    summary="Whether this organization is read-only (any member)",\n'
                        ')\n'
                        'def get_billing_access_summary(\n'
                        '    organization_id: uuid.UUID,\n'
                        '    context: OrganizationContext = Depends(RequireOrgMember),\n'
                        '    db: Session = Depends(get_db),\n'
                        ') -> BillingAccessSummaryResponse:\n'
                        '    """The member-readable half of `/billing/access`.\n'
                        '\n'
                        '    D-11 gave every write path a read-only gate and gave the console\n'
                        '    exactly one way to explain it — `DunningBanner`, which reads\n'
                        '    `/billing/access` and is therefore mounted only for OWNER, ADMIN and\n'
                        '    BILLING. An ordinary member hit a refused upload with no explanation\n'
                        '    anywhere on screen, because the three roles who could see the reason\n'
                        '    were the three least likely to be uploading.\n'
                        '\n'
                        '    Org-scoped under `/organizations/{organization_id}/` rather than a\n'
                        '    bare `/billing/access-summary`, matching every other billing route:\n'
                        '    a member of several organizations must be able to ask about one of\n'
                        '    them, and an endpoint that infers the tenant from the session is an\n'
                        '    ARCH-02 isolation argument waiting to be lost.\n'
                        '    """\n'
                        '    state = dunning_service.access_state(\n'
                        '        db, organization_id=context.organization_id\n'
                        '    )\n'
                        '    live = subscription_service.live_subscription_for_organization(\n'
                        '        db, organization_id=context.organization_id\n'
                        '    )\n'
                        '\n'
                        '    status_value = (\n'
                        '        (live.status.value if hasattr(live.status, "value") else str(live.status))\n'
                        '        if live\n'
                        '        else None\n'
                        '    )\n'
                        '    grace_ends_at = live.grace_ends_at if live else None\n'
                        '\n'
                        '    if not state.writes_allowed:\n'
                        '        # Already closed. Returning the expired date here would read as\n'
                        '        # "you have until <date in the past>", which is worse than no\n'
                        '        # date at all.\n'
                        '        summary_state = "RESTRICTED"\n'
                        '        exposed_grace = None\n'
                        '    elif status_value == "past_due" and grace_ends_at is not None:\n'
                        '        summary_state = "GRACE"\n'
                        '        exposed_grace = grace_ends_at\n'
                        '    else:\n'
                        '        summary_state = "ACTIVE"\n'
                        '        exposed_grace = None\n'
                        '\n'
                        '    return BillingAccessSummaryResponse(\n'
                        '        state=summary_state,  # type: ignore[arg-type]\n'
                        '        is_read_only=not state.writes_allowed,\n'
                        '        grace_ends_at=exposed_grace,\n'
                        '    )\n'
                    ),
                ),
            ],
        )
    )

    # =====================================================================
    # A7 — Dodo seat proration preview
    # =====================================================================
    groups.append(
        FilePatches(
            "backend/app/services/billing/dodo_gateway.py",
            [
                Patch(
                    note="after set_subscription_seats",
                    sentinel="dodo-preview-change-plan",
                    mode="replace",
                    anchor=(
                        '        return self.fetch_subscription(subscription_id)\n'
                        '\n'
                        '    # -- checkout ---------------------------------------------------------\n'
                    ),
                    payload=(
                        '        return self.fetch_subscription(subscription_id)\n'
                        '\n'
                        '    # ARCH30-T4F:dodo-preview-change-plan — A7.\n'
                        '    def preview_change_plan(\n'
                        '        self,\n'
                        '        *,\n'
                        '        subscription_id: str,\n'
                        '        product_id: str,\n'
                        '        quantity: int,\n'
                        '        proration_mode: str = "prorated_immediately",\n'
                        '    ) -> "DodoProrationPreview":\n'
                        '        """`POST /subscriptions/{id}/change-plan/preview` — what it would cost.\n'
                        '\n'
                        '        Verified against Dodo\'s published OpenAPI (public v1.113.27,\n'
                        '        `change_plan_preview_handler`) rather than guessed, because two\n'
                        '        details here are the whole correctness of the number:\n'
                        '\n'
                        '        1. The request body is the SAME schema as the real change-plan\n'
                        '           call (`UpdateSubscriptionPlanReq`), so `product_id` is\n'
                        '           required even when only the quantity moves. A seat change\n'
                        '           passes the product the subscription is already pinned to —\n'
                        '           the same thing `set_subscription_seats` does, so the preview\n'
                        '           and the write cannot disagree about what is being changed.\n'
                        '\n'
                        '        2. `immediate_charge.summary.total_amount` is an int32 in the\n'
                        '           currency\'s SMALLEST unit (cents for USD, paise for INR, yen\n'
                        '           for JPY). This codebase stores money in micros, so the\n'
                        '           conversion is x10_000 — not x1_000_000. Getting that wrong\n'
                        '           understates every disclosure by two orders of magnitude and\n'
                        '           looks entirely plausible on screen.\n'
                        '\n'
                        '        `next_billing_date` comes from `new_plan`, which the OpenAPI\n'
                        '        marks required on the response — it is the end of the period the\n'
                        '        proration is measured against.\n'
                        '\n'
                        '        Raises rather than returning a sentinel. The caller\n'
                        '        (`seat_service.seat_price_disclosure`) already catches\n'
                        '        everything and reports an unknown proration, and "unknown" and\n'
                        '        "zero" must never collapse into the same value on a price\n'
                        '        disclosure.\n'
                        '        """\n'
                        '        if not subscription_id:\n'
                        '            raise DodoGatewayError("No subscription id to preview.")\n'
                        '        if not product_id:\n'
                        '            raise DodoGatewayError(\n'
                        '                "Dodo\'s change-plan preview requires the product id even "\n'
                        '                "when only the quantity changes."\n'
                        '            )\n'
                        '        if int(quantity) < 1:\n'
                        '            raise DodoGatewayError(\n'
                        '                "Dodo subscriptions need at least one unit."\n'
                        '            )\n'
                        '        allowed = {\n'
                        '            "prorated_immediately",\n'
                        '            "full_immediately",\n'
                        '            "difference_immediately",\n'
                        '            "do_not_bill",\n'
                        '        }\n'
                        '        if proration_mode not in allowed:\n'
                        '            raise DodoGatewayError(\n'
                        '                f"Unknown Dodo proration mode {proration_mode!r}; expected "\n'
                        '                f"one of {sorted(allowed)}."\n'
                        '            )\n'
                        '\n'
                        '        raw = self._request(\n'
                        '            "POST",\n'
                        '            f"/subscriptions/{quote(str(subscription_id), safe=\'\')}"\n'
                        '            f"/change-plan/preview",\n'
                        '            {\n'
                        '                "product_id": product_id,\n'
                        '                "quantity": int(quantity),\n'
                        '                "proration_billing_mode": proration_mode,\n'
                        '            },\n'
                        '        )\n'
                        '\n'
                        '        charge = raw.get("immediate_charge") or {}\n'
                        '        summary = charge.get("summary") or {}\n'
                        '        if "total_amount" not in summary:\n'
                        '            raise DodoGatewayError(\n'
                        '                "Dodo preview returned no immediate_charge.summary."\n'
                        '                "total_amount; refusing to report a proration of zero "\n'
                        '                "for a response we did not understand."\n'
                        '            )\n'
                        '\n'
                        '        try:\n'
                        '            minor_units = int(summary["total_amount"])\n'
                        '        except (TypeError, ValueError) as exc:\n'
                        '            raise DodoGatewayError(\n'
                        '                f"Dodo preview total_amount was not an integer: "\n'
                        '                f"{summary.get(\'total_amount\')!r}"\n'
                        '            ) from exc\n'
                        '\n'
                        '        new_plan = raw.get("new_plan") or {}\n'
                        '        return DodoProrationPreview(\n'
                        '            # Smallest currency unit -> micros. See the docstring.\n'
                        '            prorated_amount_micros=minor_units * 10_000,\n'
                        '            currency=str(summary.get("currency") or "USD").upper(),\n'
                        '            next_billing_date=_parse_instant(\n'
                        '                new_plan.get("next_billing_date")\n'
                        '            ),\n'
                        '            raw=raw,\n'
                        '        )\n'
                        '\n'
                        '    # -- checkout ---------------------------------------------------------\n'
                    ),
                ),
                Patch(
                    note="preview dataclass, before DodoSubscriptionSnapshot",
                    sentinel="dodo-preview-dataclass",
                    mode="before",
                    anchor="@dataclass(frozen=True)\nclass DodoSubscriptionSnapshot:\n",
                    payload=(
                        '# ARCH30-T4F:dodo-preview-dataclass — A7.\n'
                        '@dataclass(frozen=True)\n'
                        'class DodoProrationPreview:\n'
                        '    """What Dodo would charge immediately for a seat change.\n'
                        '\n'
                        '    `prorated_amount_micros` is already converted out of Dodo\'s minor\n'
                        '    units. Deliberately not Optional: a preview that could not produce\n'
                        '    a number raises instead, because a disclosure showing 0 when the\n'
                        '    truth is unknown is a worse outcome than showing nothing.\n'
                        '    """\n'
                        '\n'
                        '    prorated_amount_micros: int\n'
                        '    currency: str\n'
                        '    next_billing_date: Optional[datetime]\n'
                        '    raw: dict[str, Any] = field(default_factory=dict)\n'
                        '\n'
                        '\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "backend/app/services/billing/seat_service.py",
            [
                Patch(
                    note="PRORATION_SOURCE constants",
                    sentinel="proration-source-dodo",
                    anchor='PRORATION_SOURCE_STRIPE: str = "STRIPE_PREVIEW"\n',
                    payload=(
                        '# ARCH30-T4F:proration-source-dodo — A7. A separate value, not "PREVIEW".\n'
                        '# The console shows the source next to the figure and a customer on a\n'
                        '# Merchant-of-Record gateway is entitled to know which vendor quoted it.\n'
                        'PRORATION_SOURCE_DODO: str = "DODO_PREVIEW"\n'
                    ),
                ),
                Patch(
                    note="seat_price_disclosure proration block",
                    sentinel="dodo-proration-wiring",
                    mode="replace",
                    anchor=(
                        '    try:\n'
                        '        if gateway is None and subscription.gateway != "STRIPE":\n'
                        '            # ARCH-30 Tranche 3. Only the Stripe adapter offers a preview here;\n'
                        '            # asking Stripe about a Dodo subscription would fail and be\n'
                        '            # reported as Stripe being unreachable.\n'
                        '            raise LookupError(f"no proration preview for {subscription.gateway}")\n'
                        '        client = gateway or stripe_gateway.get_gateway()\n'
                        '        preview = client.preview_seat_change(\n'
                        '            subscription_id=subscription.stripe_subscription_id,\n'
                        '            seats=after,\n'
                        '            timeout_seconds=SEAT_PREVIEW_TIMEOUT_SECONDS,\n'
                        '        )\n'
                        '        proration = int(preview.proration_micros)\n'
                        '        proration_source = PRORATION_SOURCE_STRIPE\n'
                        '        if preview.period_start is not None:\n'
                        '            period_start = preview.period_start\n'
                        '        if preview.period_end is not None:\n'
                        '            period_end = preview.period_end\n'
                    ),
                    payload=(
                        '    # ARCH30-T4F:dodo-proration-wiring — A7. Tranche 3 reported "unknown"\n'
                        '    # for every Dodo subscription because only the Stripe adapter had a\n'
                        '    # preview. Dodo does have one — POST /subscriptions/{id}/change-plan/\n'
                        '    # preview — and every Indian customer on the Merchant-of-Record path\n'
                        '    # was being shown a shrug where a number belonged.\n'
                        '    gateway_name = str(subscription.gateway or "").upper()\n'
                        '    try:\n'
                        '        if gateway_name == "DODO" and gateway is None:\n'
                        '            from app.models.quota_tier import QuotaTier\n'
                        '            from app.services.billing import dodo_gateway as _dodo\n'
                        '\n'
                        '            # Exactly the source `apply_seat_change` uses for the\n'
                        '            # real write: QuotaTier.gateway_price_id, reached through\n'
                        '            # the tier the subscription is pinned to. A preview that\n'
                        '            # resolved the product any other way could quote a number\n'
                        '            # for a different plan than the one the write would move\n'
                        '            # the customer to, which is worse than quoting nothing.\n'
                        '            tier = db.get(QuotaTier, subscription.quota_tier_id)\n'
                        '            if tier is None or not tier.gateway_price_id:\n'
                        '                raise LookupError(\n'
                        '                    "tier has no gateway price id; nothing to preview"\n'
                        '                )\n'
                        '            dodo_preview = _dodo.get_dodo_gateway().preview_change_plan(\n'
                        '                subscription_id=subscription.gateway_subscription_id,\n'
                        '                product_id=tier.gateway_price_id,\n'
                        '                quantity=after,\n'
                        '                # The SAME mode the write will use. Previewing\n'
                        '                # `prorated_immediately` and then writing whatever the\n'
                        '                # setting says quotes a figure for an operation that\n'
                        '                # never happens.\n'
                        '                proration_mode=str(\n'
                        '                    settings.BILLING_DODO_SEAT_PRORATION_MODE\n'
                        '                ),\n'
                        '            )\n'
                        '            proration = int(dodo_preview.prorated_amount_micros)\n'
                        '            proration_source = PRORATION_SOURCE_DODO\n'
                        '            if dodo_preview.next_billing_date is not None:\n'
                        '                # Dodo reports the end of the period the proration is\n'
                        '                # measured against; the start stays as the subscription\n'
                        '                # records it, since the preview does not restate it.\n'
                        '                period_end = dodo_preview.next_billing_date\n'
                        '        else:\n'
                        '            if gateway is None and gateway_name != "STRIPE":\n'
                        '                raise LookupError(\n'
                        '                    f"no proration preview for {subscription.gateway}"\n'
                        '                )\n'
                        '            client = gateway or stripe_gateway.get_gateway()\n'
                        '            preview = client.preview_seat_change(\n'
                        '                subscription_id=subscription.stripe_subscription_id,\n'
                        '                seats=after,\n'
                        '                timeout_seconds=SEAT_PREVIEW_TIMEOUT_SECONDS,\n'
                        '            )\n'
                        '            proration = int(preview.proration_micros)\n'
                        '            proration_source = PRORATION_SOURCE_STRIPE\n'
                        '            if preview.period_start is not None:\n'
                        '                period_start = preview.period_start\n'
                        '            if preview.period_end is not None:\n'
                        '                period_end = preview.period_end\n'
                    ),
                ),
                Patch(
                    note="seat_service __all__",
                    sentinel="proration-source-dodo-export",
                    anchor='    "PRORATION_SOURCE_STRIPE",\n',
                    payload=(
                        '    # ARCH30-T4F:proration-source-dodo-export — A7.\n'
                        '    "PRORATION_SOURCE_DODO",\n'
                    ),
                ),
            ],
        )
    )

    # =====================================================================
    # A8 — tenancy security emitters
    # =====================================================================
    groups.append(
        FilePatches(
            "backend/app/api/v1/identity_admin.py",
            [
                Patch(
                    note="identity_admin imports",
                    sentinel="security-emitters-import",
                    mode="replace",
                    anchor="from app.services.identity.errors import IdentityError\n",
                    payload=(
                        "from app.services.identity.errors import IdentityError\n"
                        "# ARCH30-T4F:security-emitters-import — A8.\n"
                        "from app.services.identity import security_emitters\n"
                    ),
                ),
                Patch(
                    note="create_scim_key audit",
                    sentinel="emit-scim-created",
                    anchor=(
                        '    write_audit(db, organization_id=organization_id, action="CREATED",\n'
                        '                resource_type="SCIM_API_KEY", resource_id=row.id,\n'
                        '                principal=_principal(user),\n'
                        '                details={"display_name": row.display_name})\n'
                    ),
                    payload=(
                        '    # ARCH30-T4F:emit-scim-created — A8. An audit row is a record;\n'
                        '    # this is the signal. A SCIM key outlives the account that minted\n'
                        '    # it by design, so its creation must not be silent.\n'
                        '    security_emitters.emit_quietly(\n'
                        '        security_emitters.notify_scim_key_created,\n'
                        '        db,\n'
                        '        organization_id=organization_id,\n'
                        '        display_name=row.display_name,\n'
                        '        actor=user,\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="rotate_scim_key audit",
                    sentinel="emit-scim-rotated",
                    anchor=(
                        '    write_audit(db, organization_id=organization_id, action="ROTATED",\n'
                        '                resource_type="SCIM_API_KEY", resource_id=row.id,\n'
                        '                principal=_principal(user),\n'
                        '                details={"overlap_until": str(row.previous_secret_expires_at)})\n'
                    ),
                    payload=(
                        '    # ARCH30-T4F:emit-scim-rotated — A8.\n'
                        '    security_emitters.emit_quietly(\n'
                        '        security_emitters.notify_scim_key_rotated,\n'
                        '        db,\n'
                        '        organization_id=organization_id,\n'
                        '        display_name=row.display_name,\n'
                        '        overlap_until=row.previous_secret_expires_at,\n'
                        '        actor=user,\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="add_certificate audit",
                    sentinel="emit-idp-certificate",
                    anchor=(
                        '    write_audit(db, organization_id=organization_id, action="CREATED",\n'
                        '                resource_type="IDP_CERTIFICATE", resource_id=cert.id,\n'
                        '                principal=_principal(user),\n'
                        '                details={"side": side, "fingerprint": cert.fingerprint_sha256})\n'
                    ),
                    payload=(
                        '    # ARCH30-T4F:emit-idp-certificate — A8. The signing certificate is\n'
                        '    # the trust anchor for every assertion this IdP sends; adding one\n'
                        '    # is adding a key that can vouch for any identity in the tenant.\n'
                        '    security_emitters.emit_quietly(\n'
                        '        security_emitters.notify_idp_certificate_added,\n'
                        '        db,\n'
                        '        organization_id=organization_id,\n'
                        '        config_name=config.display_name,\n'
                        '        side=side,\n'
                        '        fingerprint=cert.fingerprint_sha256,\n'
                        '        actor=user,\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="activate audit",
                    sentinel="emit-idp-activated",
                    anchor=(
                        '    write_audit(db, organization_id=organization_id, action="UPDATED",\n'
                        '                resource_type="ENTERPRISE_IDP_CONFIG", resource_id=config.id,\n'
                        '                principal=_principal(user), details={"is_active": True})\n'
                    ),
                    payload=(
                        '    # ARCH30-T4F:emit-idp-activated — A8. Activation decides who may\n'
                        '    # become a member and with what role. An attacker who can activate\n'
                        '    # their own IdP does not need to break a password.\n'
                        '    security_emitters.emit_quietly(\n'
                        '        security_emitters.notify_idp_config_activated,\n'
                        '        db,\n'
                        '        organization_id=organization_id,\n'
                        '        display_name=config.display_name,\n'
                        '        protocol=str(getattr(config.protocol, "value", config.protocol)),\n'
                        '        actor=user,\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="update_policy body",
                    sentinel="emit-security-policy",
                    mode="replace",
                    anchor=(
                        '    except ValueError as exc:\n'
                        '        raise HTTPException(409, str(exc)) from exc\n'
                        '    return get_policy(organization_id, membership=membership, db=db)\n'
                    ),
                    payload=(
                        '    except ValueError as exc:\n'
                        '        raise HTTPException(409, str(exc)) from exc\n'
                        '    # ARCH30-T4F:emit-security-policy — A8. Field names, never values:\n'
                        '    # "session timeout changed" is enough to make somebody go and look,\n'
                        '    # while restating the new enforcement in a notification hands\n'
                        '    # anyone who has already taken an inbox a map of what is enforced.\n'
                        '    security_emitters.emit_quietly(\n'
                        '        security_emitters.notify_security_policy_updated,\n'
                        '        db,\n'
                        '        organization_id=organization_id,\n'
                        '        changed_fields=[\n'
                        '            key for key, value in (payload or {}).items()\n'
                        '            if value is not None\n'
                        '        ],\n'
                        '        actor=user,\n'
                        '    )\n'
                        '    return get_policy(organization_id, membership=membership, db=db)\n'
                    ),
                ),
            ],
        )
    )

    # =====================================================================
    # A4 — console parity (domains)
    # =====================================================================
    groups.append(
        FilePatches(
            "frontend/src/pages/organization/OrganizationBranding.tsx",
            [
                Patch(
                    note="DomainRowProps",
                    sentinel="domainrow-maintain-prop",
                    mode="replace",
                    anchor=(
                        'interface DomainRowProps {\n'
                        '  readonly domain: CustomDomainDetail;\n'
                        '  readonly isOwner: boolean;\n'
                        '  readonly busy: boolean;\n'
                    ),
                    payload=(
                        'interface DomainRowProps {\n'
                        '  readonly domain: CustomDomainDetail;\n'
                        '  readonly isOwner: boolean;\n'
                        '  readonly busy: boolean;\n'
                        '  /**\n'
                        '   * ARCH30-T4F:domainrow-maintain-prop — A4. The server\'s\n'
                        '   * `can_maintain`, not a local derivation from the add-on state. The\n'
                        '   * backend already refuses verify / reissue / set-primary /\n'
                        '   * request-certificate in GRACE and LAPSED; before this, the console\n'
                        '   * still offered all four, so the only way to discover the rule was\n'
                        '   * to press a button and read a 402. Destructive and cleanup actions\n'
                        '   * are deliberately NOT gated on this — a tenant who has stopped\n'
                        '   * paying must always be able to stop serving and release a hostname.\n'
                        '   */\n'
                        '  readonly canMaintain: boolean;\n'
                        '  readonly maintainReason: string;\n'
                    ),
                ),
                Patch(
                    note="DomainRow destructuring",
                    sentinel="domainrow-maintain-destructure",
                    mode="replace",
                    anchor=(
                        'const DomainRow: React.FC<DomainRowProps> = ({\n'
                        '  domain,\n'
                        '  isOwner,\n'
                        '  busy,\n'
                    ),
                    payload=(
                        '// ARCH30-T4F:domainrow-maintain-destructure — A4.\n'
                        'const DomainRow: React.FC<DomainRowProps> = ({\n'
                        '  domain,\n'
                        '  isOwner,\n'
                        '  busy,\n'
                        '  canMaintain,\n'
                        '  maintainReason,\n'
                    ),
                ),
                Patch(
                    note="verify + reissue buttons",
                    sentinel="domainrow-gate-verify-reissue",
                    mode="replace",
                    anchor=(
                        '        <button\n'
                        '          type="button"\n'
                        '          className={SECONDARY}\n'
                        '          disabled={busy}\n'
                        '          onClick={() => onVerify(domain.id)}\n'
                        '        >\n'
                        '          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />\n'
                        '          Verify now\n'
                        '        </button>\n'
                        '        <button\n'
                        '          type="button"\n'
                        '          className={SECONDARY}\n'
                        '          disabled={busy}\n'
                        '          onClick={() => onReissue(domain.id)}\n'
                        '        >\n'
                        '          <RefreshCw className="h-3.5 w-3.5" aria-hidden />\n'
                        '          New challenge\n'
                        '        </button>\n'
                    ),
                    payload=(
                        '        {/* ARCH30-T4F:domainrow-gate-verify-reissue — A4 */}\n'
                        '        <button\n'
                        '          type="button"\n'
                        '          className={SECONDARY}\n'
                        '          disabled={busy || !canMaintain}\n'
                        '          title={canMaintain ? undefined : maintainReason}\n'
                        '          onClick={() => onVerify(domain.id)}\n'
                        '        >\n'
                        '          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />\n'
                        '          Verify now\n'
                        '        </button>\n'
                        '        <button\n'
                        '          type="button"\n'
                        '          className={SECONDARY}\n'
                        '          disabled={busy || !canMaintain}\n'
                        '          title={canMaintain ? undefined : maintainReason}\n'
                        '          onClick={() => onReissue(domain.id)}\n'
                        '        >\n'
                        '          <RefreshCw className="h-3.5 w-3.5" aria-hidden />\n'
                        '          New challenge\n'
                        '        </button>\n'
                    ),
                ),
                Patch(
                    note="certificate button",
                    sentinel="domainrow-gate-certificate",
                    mode="replace",
                    anchor=(
                        '          disabled={busy || !domain.may_request_certificate}\n'
                        '          onClick={() => onCertificate(domain.id)}\n'
                    ),
                    payload=(
                        '          /* ARCH30-T4F:domainrow-gate-certificate — A4. Two independent\n'
                        '             reasons to refuse: the domain is not in a state where a\n'
                        '             certificate can be issued, or the add-on no longer covers\n'
                        '             maintenance. Both disable; the tooltip names whichever\n'
                        '             applies. */\n'
                        '          disabled={busy || !domain.may_request_certificate || !canMaintain}\n'
                        '          title={canMaintain ? undefined : maintainReason}\n'
                        '          onClick={() => onCertificate(domain.id)}\n'
                    ),
                ),
                Patch(
                    note="make primary button",
                    sentinel="domainrow-gate-primary",
                    mode="replace",
                    anchor=(
                        '            className={SECONDARY}\n'
                        '            disabled={busy}\n'
                        '            onClick={() => onPrimary(domain.id, true)}\n'
                    ),
                    payload=(
                        '            className={SECONDARY}\n'
                        '            /* ARCH30-T4F:domainrow-gate-primary — A4 */\n'
                        '            disabled={busy || !canMaintain}\n'
                        '            title={canMaintain ? undefined : maintainReason}\n'
                        '            onClick={() => onPrimary(domain.id, true)}\n'
                    ),
                ),
                Patch(
                    note="DomainRow call site",
                    sentinel="domainrow-call-maintain",
                    mode="replace",
                    anchor=(
                        '                  domain={domain}\n'
                        '                  isOwner={isOwner}\n'
                        '                  busy={busy}\n'
                    ),
                    payload=(
                        '                  domain={domain}\n'
                        '                  isOwner={isOwner}\n'
                        '                  busy={busy}\n'
                        '                  canMaintain={domainCanMaintain}\n'
                        '                  maintainReason={domainMaintainReason}\n'
                    ),
                ),
                Patch(
                    note="domainCanCreate derivation",
                    sentinel="domain-maintain-derivation",
                    anchor='  const domainCanCreate = domainAddon.access?.can_create ?? false;\n',
                    payload=(
                        '  // ARCH30-T4F:domain-maintain-derivation — A4. `can_maintain` is a\n'
                        '  // separate server flag from `can_create` and the split is the point:\n'
                        '  // GRACE forbids creating new domains while still allowing the ones on\n'
                        '  // file to be verified, and LAPSED forbids both. Deriving one from the\n'
                        '  // other in the console would get GRACE wrong in one direction or the\n'
                        '  // other. Defaults to `true` while the entitlement query is in flight,\n'
                        '  // so a slow response does not flicker every control to disabled.\n'
                        '  const domainCanMaintain = domainAddon.access?.can_maintain ?? true;\n'
                        '  // ARCH30-T4F:domainrow-call-maintain — A4. Passed to every row.\n'
                        '  const domainMaintainReason =\n'
                        '    domainAddon.access?.state === "LAPSED"\n'
                        '      ? "Custom domains have lapsed. You can still stop serving or release a hostname; verification and certificates need the add-on restored."\n'
                        '      : "Custom domains are in a grace period. Verification, certificates and primary changes are paused until billing is resolved.";\n'
                    ),
                ),
            ],
        )
    )

    # =====================================================================
    # A4 — console parity (schedule create)
    # =====================================================================
    groups.append(
        FilePatches(
            "frontend/src/pages/organization/OrganizationAnalytics.tsx",
            [
                Patch(
                    note="ScheduleForm props",
                    sentinel="scheduleform-cancreate-prop",
                    mode="replace",
                    anchor=(
                        'const ScheduleForm: React.FC<{\n'
                        '  organizationId: string;\n'
                        '  destinations: WarehouseDestination[];\n'
                        '}> = ({ organizationId, destinations }) => {\n'
                    ),
                    payload=(
                        '// ARCH30-T4F:scheduleform-cancreate-prop — A4. The backend calls\n'
                        '// `addon_gate.require_addon(..., allow_grace=False)` on schedule\n'
                        '// creation, so GRACE and LAPSED both refuse it. The console offered the\n'
                        '// button anyway and the only way to find out was a 402.\n'
                        'const ScheduleForm: React.FC<{\n'
                        '  organizationId: string;\n'
                        '  destinations: WarehouseDestination[];\n'
                        '  canCreate: boolean;\n'
                        '  createReason: string;\n'
                        '}> = ({ organizationId, destinations, canCreate, createReason }) => {\n'
                    ),
                ),
                Patch(
                    note="create schedule button",
                    sentinel="scheduleform-cancreate-button",
                    mode="replace",
                    anchor=(
                        '        disabled={!destinationId || datasets.length === 0 || create.isPending}\n'
                        '        onClick={() => create.mutate()}\n'
                    ),
                    payload=(
                        '        /* ARCH30-T4F:scheduleform-cancreate-button — A4 */\n'
                        '        disabled={\n'
                        '          !canCreate ||\n'
                        '          !destinationId ||\n'
                        '          datasets.length === 0 ||\n'
                        '          create.isPending\n'
                        '        }\n'
                        '        title={canCreate ? undefined : createReason}\n'
                        '        onClick={() => create.mutate()}\n'
                    ),
                ),
                Patch(
                    note="ScheduleForm call site",
                    sentinel="scheduleform-cancreate-call",
                    mode="replace",
                    anchor=(
                        '          <ScheduleForm\n'
                        '            organizationId={organizationId}\n'
                        '            destinations={destinations.data ?? []}\n'
                        '          />\n'
                    ),
                    payload=(
                        '          {/* ARCH30-T4F:scheduleform-cancreate-call — A4 */}\n'
                        '          <ScheduleForm\n'
                        '            organizationId={organizationId}\n'
                        '            destinations={destinations.data ?? []}\n'
                        '            canCreate={warehouseCanCreate}\n'
                        '            createReason={warehouseCreateReason}\n'
                        '          />\n'
                    ),
                ),
                Patch(
                    note="warehouse create reason",
                    sentinel="warehouse-create-reason",
                    anchor='  const warehouseCanCreate = warehouseAddon.access?.can_create ?? false;\n',
                    payload=(
                        '  // ARCH30-T4F:warehouse-create-reason — A4.\n'
                        '  const warehouseCreateReason =\n'
                        '    warehouseAddon.access?.state === "LAPSED"\n'
                        '      ? "Warehouse sync has lapsed. Existing schedules can still be paused or deleted; creating new ones needs the add-on restored."\n'
                        '      : warehouseAddon.access?.state === "GRACE"\n'
                        '        ? "Warehouse sync is in a grace period. Existing schedules keep running; new ones cannot be created until billing is resolved."\n'
                        '        : "Warehouse sync is needed to create schedules.";\n'
                    ),
                ),
            ],
        )
    )

    # =====================================================================
    # A5 — frontend wiring
    # =====================================================================
    groups.append(
        FilePatches(
            "frontend/src/services/api/billing.ts",
            [
                Patch(
                    note="BILLING_ENDPOINTS.access",
                    sentinel="ts-access-summary-endpoint",
                    anchor=(
                        '  access: (organizationId: string) =>\n'
                        '    `/organizations/${org(organizationId)}/billing/access`,\n'
                    ),
                    payload=(
                        '  // ARCH30-T4F:ts-access-summary-endpoint — A5.\n'
                        '  accessSummary: (organizationId: string) =>\n'
                        '    `/organizations/${org(organizationId)}/billing/access-summary`,\n'
                    ),
                ),
                Patch(
                    note="getBillingAccess",
                    sentinel="ts-access-summary-call",
                    anchor=(
                        'export const getBillingAccess = async (\n'
                        '  organizationId: string,\n'
                        '): Promise<BillingAccessResponse> => {\n'
                        '  const response = await apiClient.get<BillingAccessResponse>(\n'
                        '    BILLING_ENDPOINTS.access(organizationId),\n'
                        '  );\n'
                        '  return response.data;\n'
                        '};\n'
                    ),
                    payload=(
                        '\n'
                        '/**\n'
                        ' * ARCH30-T4F:ts-access-summary-call — A5. The member-readable summary.\n'
                        ' *\n'
                        ' * Three fields and no amounts. Callable by any member, which is the\n'
                        ' * whole point: `getBillingAccess` above 403s for everyone outside\n'
                        ' * OWNER / ADMIN / BILLING, which is why an ordinary member saw no\n'
                        ' * explanation at all when the organization went read-only.\n'
                        ' */\n'
                        'export interface BillingAccessSummary {\n'
                        '  readonly state: "ACTIVE" | "GRACE" | "RESTRICTED";\n'
                        '  readonly is_read_only: boolean;\n'
                        '  readonly grace_ends_at: string | null;\n'
                        '}\n'
                        '\n'
                        'export const getBillingAccessSummary = async (\n'
                        '  organizationId: string,\n'
                        '): Promise<BillingAccessSummary> => {\n'
                        '  const response = await apiClient.get<BillingAccessSummary>(\n'
                        '    BILLING_ENDPOINTS.accessSummary(organizationId),\n'
                        '  );\n'
                        '  return response.data;\n'
                        '};\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/services/api/queryKeys.ts",
            [
                Patch(
                    note="billingKeys.access",
                    sentinel="ts-access-summary-key",
                    anchor=(
                        '  access: (organizationId: string) =>\n'
                        '    [...billingKeys.all(organizationId), "access"] as const,\n'
                    ),
                    payload=(
                        '  // ARCH30-T4F:ts-access-summary-key — A5. A key of its own, not a\n'
                        '  // variant of `access`: the two endpoints return different shapes and\n'
                        '  // sharing a cache entry would let a member\'s summary satisfy an\n'
                        '  // owner\'s query for the full payload.\n'
                        '  accessSummary: (organizationId: string) =>\n'
                        '    [...billingKeys.all(organizationId), "access-summary"] as const,\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/layouts/OrganizationLayout.tsx",
            [
                Patch(
                    note="OrganizationLayout import",
                    sentinel="ts-member-notice-import",
                    anchor='import DunningBanner from "@/components/billing/DunningBanner";\n',
                    payload=(
                        '// ARCH30-T4F:ts-member-notice-import — A5.\n'
                        'import MemberAccessNotice from "@/components/billing/MemberAccessNotice";\n'
                    ),
                ),
                Patch(
                    note="OrganizationLayout banner",
                    sentinel="ts-member-notice-render",
                    mode="replace",
                    anchor='          <DunningBanner organizationId={organizationId} canManageBilling />\n',
                    payload=(
                        '          {/* ARCH30-T4F:ts-member-notice-render — A5. Mutually\n'
                        '              exclusive: a billing-capable role gets the full banner\n'
                        '              with the portal button, everybody else gets the summary\n'
                        '              with no amounts and no actions they cannot take. */}\n'
                        '          {canSeeBilling ? (\n'
                        '            <DunningBanner organizationId={organizationId} canManageBilling />\n'
                        '          ) : (\n'
                        '            <MemberAccessNotice organizationId={organizationId} />\n'
                        '          )}\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/layouts/DashboardLayout.tsx",
            [
                Patch(
                    note="DashboardLayout import",
                    sentinel="ts-member-notice-import-dash",
                    anchor='import DunningBanner from "@/components/billing/DunningBanner";\n',
                    payload=(
                        '// ARCH30-T4F:ts-member-notice-import-dash — A5.\n'
                        'import MemberAccessNotice from "@/components/billing/MemberAccessNotice";\n'
                    ),
                ),
            ],
        )
    )

    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    root = Path(args.root).resolve() if args.root else here.parent

    print(f"ARCH-30 Tranche 4 FINAL (A4, A5, A6, A7, A8) — root: {root}")
    try:
        assert_preconditions(root)
        return run(root, build_patches(), check=args.check)
    except PatchError as exc:
        print(f"\nREFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())