"""ARCH-30 Tranche 1 (T4-F1) — capability entitlements.

THE DEFECT THIS MODULE CLOSES
=============================

ARCH-29 Tranche 2 (D-2) made inference on the platform's provider account a
tier entitlement: a `quota_tier_entries` row with `limit_key =
"llm.platform_key"`, where presence is the grant. The reader was correct —
`model_routing_service._platform_key_entitled` — and so was the seed, which
added the row to every tier.

The writer between them refused it. `quota_service.publish_tier` runs every
entry through `_validate`, and `_validate` accepted exactly two kinds of key:
the wildcard `*` and a billable member of `USAGE_EVENT_TYPES`. A capability is
neither. Executed against the seed's own definitions, all four tiers were
rejected with "'llm.platform_key' is neither the wildcard '*' nor a billable
usage event type", and because `seed_quota_tiers.py` publishes every tier in
one transaction, the rollback took all four with it.

Nothing downstream could notice. `resolve_tier` found no tier carrying the
row, `_platform_key_entitled` answered False — correctly, by its own
fail-closed rule — and every tenant without BYOK was refused inference. Each
component did what it was written to do. The vocabulary between them had no
word for the thing being passed.

It survived `verify_arch29_tranche2.py` because every check in that gate reads
source. G7 there asserts the seed's keys have display labels; nothing asserted
the seed's tiers could be PUBLISHED. `verify_arch30_tranche1.py` G1 executes
the real validator against the real seed rows for exactly that reason.

WHY A SEPARATE VOCABULARY, AND NOT A USAGE EVENT TYPE
=====================================================

The one-line fix is to register `llm.platform_key` in `usage_events._BASE_TYPES`
with `billable=True`. That fix is wrong in four places at once:

  * `quota_service.quota_status` iterates `USAGE_EVENT_TYPES` and would render
    a meter row for a capability, "0 of 0 used", on every usage page;
  * `usage_events._overage_variants` would mint `llm.platform_key.overage`, a
    billable event type for exceeding a permission;
  * `usage_events.resolve` would accept it as an emittable event, so a
    metering bug could record "consumption" of an entitlement;
  * `spend_control_service.effective_limits` would treat the row's
    `max_cost_micros = 0` as a real zero-dollar ceiling if anything ever asked
    for that key.

A meter answers "how much". An entitlement answers "whether". Registering one
as the other makes every consumer of the meter vocabulary wrong in a
different way, and none of them fails loudly. `_assert_disjoint_from_meters`
below makes the two vocabularies structurally unable to share a name: a
collision raises at import, which stops the application from starting rather
than letting it serve with an ambiguous key.

THE ROW SHAPE
=============

`quota_tier_entries` carries CHECK `at_least_one_ceiling`, so an entitlement
row cannot leave both ceilings null. The canonical shape — the one the seed
already writes — is:

    max_cost_micros = 0, max_quantity = NULL, overage_policy = 'REFUSE',
    grace_quantity = NULL, overage_price_tier_key = NULL, period = 'MONTH'

`shape_violation` enforces exactly that. Any other shape means someone
believed the row meters something, and publishing it would encode that belief
in an immutable tier version. Period is pinned to MONTH because the unique
index is `(quota_tier_id, limit_key, period)`: allowing a DAY and a MONTH copy
of the same grant would give a presence check two rows to disagree about.

ADD-ONS (ARCH-30 TRANCHE 2, D-8)
=================================

`addon.custom_domain` and `addon.warehouse_sync` are registered here now that
they have readers: `entitlement_service.tier_grants` for the tier source and
`app.api.addon_gate.require_addon` on every create and maintain endpoint of
the two routers they gate. A tier grants an add-on by carrying the row, in
exactly the canonical shape `llm.platform_key` uses; a PURCHASED add-on is
recorded in `organization_addons` instead, because a purchase is a gateway
subscription with its own lifecycle and a published tier version is immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.core.usage_events import TOTAL_COST_KEY, USAGE_EVENT_TYPES

__all__ = [
    "ADDON_KEYS",
    # ARCH31-S0:capability-reconciliation-export
    "CAPABILITY_KEYS",
    "RECONCILIATION_CAPABILITY",
    # ARCH32-S1:capability-redaction-key
    "REDACTION_CAPABILITY",
    # ARCH33-S1:capability-assertions-key
    "SEMANTIC_ASSERTIONS_CAPABILITY",
    "ANOMALY_RADAR_CAPABILITY",
    "CALIBRATED_AUTONOMY_CAPABILITY",
    # HM-S1:capability-keys-export
    "DEVELOPER_API_CAPABILITY",
    "OUTGOING_WEBHOOKS_CAPABILITY",
    "CUSTOM_BRANDING_CAPABILITY",
    "CUSTOM_EMAIL_CAPABILITY",
    "ENTERPRISE_IDENTITY_CAPABILITY",
    "PRIORITY_SLO_CAPABILITY",
    "EXTRACTION_MEMORY_CAPABILITY",
    "ENTITY_GRAPH_CAPABILITY",
    "CASE_INTELLIGENCE_CAPABILITY",
    "TABLE_INTELLIGENCE_CAPABILITY",
    # ARCH45-S1:capability-corroborator-export
    "UNIVERSAL_CORROBORATOR_CAPABILITY",
    # ARCH46-S1:capability-obligations-export
    "OBLIGATIONS_CAPABILITY",
    # ARCH47-S1:capability-erp-posting-export
    "ERP_POSTING_CAPABILITY",
    "CANONICAL_MAX_COST_MICROS",
    "CUSTOM_DOMAIN_ADDON",
    "WAREHOUSE_SYNC_ADDON",
    "CANONICAL_OVERAGE_POLICY",
    "CANONICAL_PERIOD",
    "ENTITLEMENT_KEYS",
    "Entitlement",
    "PLATFORM_KEY",
    "is_entitlement_key",
    "shape_violation",
]


@dataclass(frozen=True)
class Entitlement:
    """One capability a tier grants by carrying a row keyed by `name`."""

    name: str
    description: str


#: ARCH-29 D-2. Inference on the PLATFORM's provider account.
#:
#: `model_routing_service.PLATFORM_KEY_LIMIT_KEY` holds the same literal. It is
#: not imported from here, so that this tranche does not edit a module the
#: Tranche 2 gate reads; `verify_arch30_tranche1.py` G4 asserts the two agree.
PLATFORM_KEY: str = "llm.platform_key"

#: ARCH-30 Tranche 2 (D-8). Serving the tenant on its own verified hostname.
CUSTOM_DOMAIN_ADDON: str = "addon.custom_domain"

#: ARCH-30 Tranche 2 (D-8). Scheduled exports into the tenant's warehouse.
WAREHOUSE_SYNC_ADDON: str = "addon.warehouse_sync"

#: ARCH31-S0:capability-reconciliation-key. Three-way procurement
#: matching. A CAPABILITY, not an ADDON, and the distinction is
#: load-bearing: add-ons are separately purchasable line items with a
#: price, a halt effect and a grace ladder, and `ADDON_KEYS` is
#: asserted equal to `entitlement_service`'s catalog at import.
#: Capabilities are bundled into tiers and have no independent price,
#: so adding this to ADDON_KEYS would fail that assertion at boot.
RECONCILIATION_CAPABILITY: str = "capability.reconciliation"

#: ARCH32-S1:capability-redaction-key. Zero-leakage geometric PII
#: redaction. A CAPABILITY, not an ADDON, and the distinction is the
#: same one ARCH-31 recorded above: add-ons are separately purchasable
#: line items with a price, a halt effect and a grace ladder, and
#: `ADDON_KEYS` is asserted equal to `entitlement_service`'s catalog at
#: import. §8 of the roadmap bundles redaction as "Enterprise; add-on
#: for Business", and the add-on half of that sentence is a PACKAGING
#: decision made in a published tier version — not a second key here.
#: Putting it in ADDON_KEYS would fail the catalog assertion at boot.
REDACTION_CAPABILITY: str = "capability.redaction"

#: ARCH33-S1:capability-assertions-key. Semantic assertion automation
#: with confidence triage. A CAPABILITY, not an ADDON, and the
#: distinction is the same one ARCH-31 and ARCH-32 recorded above:
#: add-ons are separately purchasable line items with a price, a halt
#: effect and a grace ladder, and `ADDON_KEYS` is asserted equal to
#: `entitlement_service`'s catalog at import. Putting this in
#: ADDON_KEYS would fail that catalog assertion at boot.
#:
#: §4.7 is also specific that the LLM family runs "through the tenant's
#: existing model route under existing metering and spend limits", so
#: there is no second key here for the AI half. One capability gates
#: the feature; the existing `llm.platform_key` entitlement and the
#: existing token meters gate the inference.
SEMANTIC_ASSERTIONS_CAPABILITY: str = "capability.semantic_assertions"

#: ARCH34-S2:capability-anomaly-radar-key. Cross-document anomaly and
#: duplicate ingestion radar. A CAPABILITY, not an ADDON, and the
#: distinction is the same one ARCH-31, ARCH-32 and ARCH-33 recorded
#: above: add-ons are separately purchasable line items with a price, a
#: halt effect and a grace ladder, and `ADDON_KEYS` is asserted equal to
#: `entitlement_service`'s catalog at import. Putting this in ADDON_KEYS
#: would fail that catalog assertion at boot.
#:
#: ONE KEY, NOT TWO. §5.7 gives Business duplicate detection through L2
#: and Enterprise the full detector set including L3 and contract drift.
#: That is a PACKAGING decision made in a published tier version and in
#: `sweep.settings_for`, not a second entitlement key — a second key
#: would mean a tenant could hold "radar" without "radar L3" and the
#: console would have to render two lock states for one feature.
ANOMALY_RADAR_CAPABILITY: str = "capability.anomaly_radar"

#: ARCH35-S1:capability-calibrated-autonomy-key. Calibrated autonomy and
#: conformal risk control. A CAPABILITY, not an ADDON, for the reason every
#: phase since ARCH-31 records: ADDON_KEYS is asserted equal to
#: entitlement_service's priced catalog at import. Not metered either:
#: refitting is platform maintenance, and the value is the reviews a tenant
#: no longer has to do. Tenants without it keep the fixed thresholds.
CALIBRATED_AUTONOMY_CAPABILITY: str = "capability.calibrated_autonomy"

#: HM-S1:capability-developer-api. Programmatic access: issuing and rotating
#: FlowPilot API keys, and authenticating any request with one. Enforced at
#: the key-management routes AND in `deps` where a key authenticates, so a key
#: minted before a downgrade stops working rather than outliving the plan.
DEVELOPER_API_CAPABILITY: str = "capability.developer_api"

#: HM-S1:capability-outgoing-webhooks. Registering and changing outgoing
#: webhook endpoints, rotating their secrets and redelivering events.
OUTGOING_WEBHOOKS_CAPABILITY: str = "capability.outgoing_webhooks"

#: HM-S1:capability-custom-branding. Brand colours, logo and favicon. Vanity
#: hostnames are the `addon.custom_domain` grant, bundled into the same tiers.
CUSTOM_BRANDING_CAPABILITY: str = "capability.custom_branding"

#: HM-S1:capability-custom-email. Organization SMTP, workspace email overrides
#: and a custom From: domain.
CUSTOM_EMAIL_CAPABILITY: str = "capability.custom_email"

#: HM-S1:capability-enterprise-identity. SAML/OIDC single sign-on and SCIM
#: directory sync CONFIGURATION. Sign-in through an already-active identity
#: provider is never refused on plan grounds: a downgrade must not lock a
#: tenant's users out of their own data.
ENTERPRISE_IDENTITY_CAPABILITY: str = "capability.enterprise_identity"

#: HM-S1:capability-priority-slo. The priority 99.9% service-level commitment.
#: Declarative: it gates no endpoint; it is carried so that the promise is a
#: row in the tier version the customer bought, not a sentence on a web page.
PRIORITY_SLO_CAPABILITY: str = "capability.priority_slo"

#: ARCH41-S2:capability-extraction-memory. Extraction memory: reviewed
#: corrections become per-layout examples and proven anchor rules that make
#: the next extraction of the same layout better. A CAPABILITY, not an ADDON,
#: for the reason every phase since ARCH-31 records. Not metered: the prompt
#: tokens it adds are already metered as llm.input_token.
EXTRACTION_MEMORY_CAPABILITY: str = "capability.extraction_memory"

#: ARCH42-S1:capability-entity-graph. Entity resolution and the document
#: knowledge graph: one canonical record per person, organization, address,
#: account, asset or shipment across every document. A CAPABILITY, not an
#: ADDON, for the reason every phase since ARCH-31 records. Not metered:
#: resolution makes no LLM call and runs on the LIGHT worker profile.
ENTITY_GRAPH_CAPABILITY: str = "capability.entity_graph"

#: ARCH43-S1:capability-case-intelligence. The Universal Packet Dicer and Case
#: Intelligence: scanned bundles split into child documents with lineage, and
#: (Tranche 3) documents assembled into cases with completeness and consistency
#: checks. ONE key for the milestone, as ARCH-34 recorded. Not metered:
#: boundary detection is plain arithmetic over text OCR already stored.
CASE_INTELLIGENCE_CAPABILITY: str = "capability.case_intelligence"

#: ARCH44-S1:capability-table-intelligence. The Complex Table & Hierarchical
#: Grid Extractor: borderless, multi-page, rotated and ruled tables into typed
#: cells with confidence, arithmetic validation, learned column mappings and
#: CSV/XLSX/JSON export. ONE key for the milestone. Not metered: extraction
#: reads the OCR already stored and the PDF text layer; nothing is re-OCR'd.
TABLE_INTELLIGENCE_CAPABILITY: str = "capability.table_intelligence"

#: ARCH45-S1:capability-universal-corroborator. The Universal Document
#: Corroborator & Discrepancy Matrix: 2 to 5 documents aligned by fields,
#: canonical entities, clauses and line items, with materiality, ARCH-33 rules
#: and a PDF report. ONE key for the milestone; Enterprise only. Not metered:
#: alignment runs on the platform's own SentenceTransformer (ENRICH profile)
#: and ARCH-33's deterministic parsers; no model is called.
UNIVERSAL_CORROBORATOR_CAPABILITY: str = "capability.universal_corroborator"

#: ARCH46-S1:capability-obligations. Obligations & Temporal Intelligence:
#: renewal, notice, payment, delivery, expiry and reporting obligations read
#: from documents, deterministic date arithmetic over stored holiday calendars,
#: a sweep that raises due-soon and overdue Flow Builder triggers exactly once,
#: and signed, revocable iCal feeds. ONE key for the milestone; Business and
#: Enterprise. Not metered: extraction is deterministic (no model call).
OBLIGATIONS_CAPABILITY: str = "capability.obligations"

#: ARCH47-S1:capability-erp-posting. ERP & System-of-Record Posting: approved
#: outcomes (reconciled invoices, confirmed tables, completed cases) posted
#: exactly once to CSV/XLSX, EDI X12, UBL 2.1, Tally Prime XML, SFTP and
#: REST/OData targets through versioned, declarative mappings, with an
#: idempotency ledger and acknowledgement reconciliation. ONE key for the
#: milestone; Business and Enterprise. Not metered: posting is deterministic
#: (no model call).
ERP_POSTING_CAPABILITY: str = "capability.erp_posting"

#: Every capability key. Disjoint from ADDON_KEYS by construction;
#: verify_arch31_step0 asserts the two sets never intersect.
CAPABILITY_KEYS: tuple[str, ...] = (
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
    SEMANTIC_ASSERTIONS_CAPABILITY,
    ANOMALY_RADAR_CAPABILITY,
    CALIBRATED_AUTONOMY_CAPABILITY,
    DEVELOPER_API_CAPABILITY,
    OUTGOING_WEBHOOKS_CAPABILITY,
    CUSTOM_BRANDING_CAPABILITY,
    CUSTOM_EMAIL_CAPABILITY,
    ENTERPRISE_IDENTITY_CAPABILITY,
    PRIORITY_SLO_CAPABILITY,
    EXTRACTION_MEMORY_CAPABILITY,
    ENTITY_GRAPH_CAPABILITY,
    CASE_INTELLIGENCE_CAPABILITY,
    TABLE_INTELLIGENCE_CAPABILITY,
    UNIVERSAL_CORROBORATOR_CAPABILITY,
    OBLIGATIONS_CAPABILITY,
    ERP_POSTING_CAPABILITY,
)

#: Every add-on key. `entitlement_service` asserts its catalog equals this set
#: at import, so a key cannot be registered without a price and a halt effect.
ADDON_KEYS: tuple[str, ...] = (CUSTOM_DOMAIN_ADDON, WAREHOUSE_SYNC_ADDON)

_ENTITLEMENTS: tuple[Entitlement, ...] = (
    Entitlement(
        name=PLATFORM_KEY,
        description=(
            "Inference on the platform's provider account. Withheld, the "
            "tenant must configure BYOK, and model routing refuses rather "
            "than falling back onto the operator's key."
        ),
    ),
    Entitlement(
        name=CUSTOM_DOMAIN_ADDON,
        description=(
            "Custom domains: claim, verify and serve the tenant on its own "
            "hostname. Bundled with Enterprise; purchasable on other plans."
        ),
    ),
    Entitlement(
        name=WAREHOUSE_SYNC_ADDON,
        description=(
            "Warehouse sync: register destinations and run scheduled exports. "
            "Bundled with Enterprise; purchasable on other plans."
        ),
    ),
    # ARCH31-S0:capability-reconciliation-entitlement
    Entitlement(
        name=RECONCILIATION_CAPABILITY,
        description=(
            "Procurement three-way matching: reconcile purchase orders, "
            "goods receipts and supplier invoices line by line, with "
            "tolerance policies and evidence. Bundled into a tier, not "
            "purchasable on its own."
        ),
    ),
    # ARCH32-S1:capability-redaction-key
    Entitlement(
        name=REDACTION_CAPABILITY,
        description=(
            "Zero-leakage redaction: rebuild a document from redacted "
            "pixels so removed content does not exist in the output, with "
            "a leak-checked sanitized PDF and a manifest that carries no "
            "redacted text. Bundled into a tier."
        ),
    ),
    # ARCH33-S1:capability-assertions-key
    Entitlement(
        name=SEMANTIC_ASSERTIONS_CAPABILITY,
        description=(
            "Semantic assertions: write a clause requirement as a workflow "
            "step, have it compiled to a typed check, and route anything "
            "uncertain or failing to review with the paragraph attached. "
            "Bundled into a tier, not purchasable on its own."
        ),
    ),
    # ARCH-34 defect, fixed by ARCH-35: the radar key was declared and put in
    # CAPABILITY_KEYS but never registered here, so has_capability raised on
    # every radar request and no tier version could carry the key.
    Entitlement(
        name=ANOMALY_RADAR_CAPABILITY,
        description=(
            "Forensic audit radar: duplicate documents, unit-price surges and "
            "contract drift, each raised with the evidence side by side. "
            "Bundled into a tier, not purchasable on its own."
        ),
    ),
    # ARCH-35
    Entitlement(
        name=CALIBRATED_AUTONOMY_CAPABILITY,
        description=(
            "Calibrated autonomy: automatic approval decided by each tenant's "
            "own reviewed outcomes, with a stated, measured bound on the error "
            "rate of what is approved without a human. Bundled into a tier."
        ),
    ),
    # HM-S1:capability-entitlements
    Entitlement(
        name=DEVELOPER_API_CAPABILITY,
        description="Developer API: issue and rotate API keys and call FlowPilot programmatically.",
    ),
    Entitlement(
        name=OUTGOING_WEBHOOKS_CAPABILITY,
        description="Outgoing webhooks: signed event delivery to endpoints the tenant registers.",
    ),
    Entitlement(
        name=CUSTOM_BRANDING_CAPABILITY,
        description="Custom branding: the tenant's colours, logo and favicon across the console.",
    ),
    Entitlement(
        name=CUSTOM_EMAIL_CAPABILITY,
        description="Custom email: organization SMTP, workspace overrides and a custom sender domain.",
    ),
    Entitlement(
        name=ENTERPRISE_IDENTITY_CAPABILITY,
        description="Enterprise identity: SAML/OIDC single sign-on and SCIM directory sync configuration.",
    ),
    Entitlement(
        name=PRIORITY_SLO_CAPABILITY,
        description="Priority 99.9% service-level commitment. Declarative; gates no endpoint.",
    ),
    # ARCH41-S2:capability-extraction-memory-entitlement
    Entitlement(
        name=EXTRACTION_MEMORY_CAPABILITY,
        description=(
            "Extraction memory: learn from reviewed corrections per document "
            "layout, prove the improvement in a randomized trial, and apply it "
            "to the next extraction. Bundled into a tier."
        ),
    ),
    # ARCH42-S1:capability-entity-graph-entitlement
    Entitlement(
        name=ENTITY_GRAPH_CAPABILITY,
        description=(
            "Entity resolution and the document knowledge graph: canonical "
            "people, organizations, addresses, accounts, assets and shipments "
            "across every document, with relationships. Bundled into a tier."
        ),
    ),
    # ARCH43-S1:capability-case-intelligence-entitlement
    Entitlement(
        name=CASE_INTELLIGENCE_CAPABILITY,
        description=(
            "Case intelligence and the packet dicer: scanned bundles split into "
            "child documents with lineage, and documents assembled into cases. "
            "Bundled into a tier."
        ),
    ),
    # ARCH44-S1:capability-table-intelligence-entitlement
    Entitlement(
        name=TABLE_INTELLIGENCE_CAPABILITY,
        description=(
            "Table intelligence: complex, multi-page and rotated tables extracted "
            "into typed cells with confidence, arithmetic validation and "
            "CSV/XLSX export. Bundled into a tier."
        ),
    ),
    # ARCH45-S1:capability-corroborator-entitlement
    Entitlement(
        name=UNIVERSAL_CORROBORATOR_CAPABILITY,
        description=(
            "Universal document corroborator: compare 2 to 5 documents field by field, "
            "party by party, clause by clause and line by line, with a materiality-scored "
            "discrepancy matrix and a PDF report. Bundled into a tier."
        ),
    ),
    # ARCH46-S1:capability-obligations-entitlement
    Entitlement(
        name=OBLIGATIONS_CAPABILITY,
        description=(
            "Obligations & temporal intelligence: renewals, notice deadlines, payments, deliveries, "
            "expiries and reports read from documents, with business-day arithmetic, owners, "
            "due-soon and overdue triggers, and subscribable calendar feeds. Bundled into a tier."
        ),
    ),
    # ARCH47-S1:capability-erp-posting-entitlement
    Entitlement(
        name=ERP_POSTING_CAPABILITY,
        description=(
            "ERP & system-of-record posting: reconciled invoices, confirmed tables and completed cases posted "
            "exactly once as vendor bills, purchase orders, goods receipts, journal entries and payment "
            "references to CSV/XLSX, EDI X12, UBL 2.1, Tally, SFTP and REST/OData targets (QuickBooks Online, "
            "Zoho Books, Business Central, S/4HANA, NetSuite), with acknowledgement reconciliation. "
            "Bundled into a tier."
        ),
    ),
)

ENTITLEMENT_KEYS: dict[str, Entitlement] = {e.name: e for e in _ENTITLEMENTS}

#: Satisfies CHECK `at_least_one_ceiling` without expressing a ceiling.
CANONICAL_MAX_COST_MICROS: int = 0
CANONICAL_OVERAGE_POLICY: str = "REFUSE"
CANONICAL_PERIOD: str = "MONTH"


def is_entitlement_key(key: str) -> bool:
    """Exact membership. No prefix matching: `llm.` is shared with meters."""
    return key in ENTITLEMENT_KEYS


def _enum_value(value: Any) -> Any:
    """`SpendLimitPeriod.MONTH` and `"MONTH"` compare the same way here."""
    return getattr(value, "value", value)


def shape_violation(
    *,
    limit_key: str,
    period: Any,
    max_quantity: Optional[Decimal],
    max_cost_micros: Optional[int],
    overage_policy: Any,
    overage_price_tier_key: Optional[str],
    grace_quantity: Optional[Decimal],
) -> Optional[str]:
    """Why this row is not a well-formed entitlement, or None if it is.

    Returns a sentence rather than raising so the caller owns the exception
    type. `quota_service._validate` raises `QuotaTierValidationError`, which
    the publish path and the admin API already render; a second exception
    class here would need its own handler at every call site.

    Each message names the field and says what the wrong value would MEAN,
    because the person reading it is about to publish an immutable tier
    version and "invalid shape" does not tell them which belief to drop.
    """
    if not is_entitlement_key(limit_key):
        return f"'{limit_key}' is not a registered entitlement."

    prefix = f"Entitlement '{limit_key}'"

    if max_quantity is not None:
        return (
            f"{prefix} carries max_quantity={max_quantity}. An entitlement is "
            f"granted by presence and has no quantity; a quantity means this "
            f"row is being used as a meter."
        )
    if max_cost_micros != CANONICAL_MAX_COST_MICROS:
        return (
            f"{prefix} must have max_cost_micros={CANONICAL_MAX_COST_MICROS} "
            f"(got {max_cost_micros!r}). The zero only satisfies the "
            f"at_least_one_ceiling CHECK; any other value reads as a spend "
            f"ceiling on a capability."
        )
    if _enum_value(overage_policy) != CANONICAL_OVERAGE_POLICY:
        return (
            f"{prefix} must use overage_policy={CANONICAL_OVERAGE_POLICY!r} "
            f"(got {_enum_value(overage_policy)!r}). There is no overage of a "
            f"permission to warn about or bill."
        )
    if overage_price_tier_key is not None:
        return (
            f"{prefix} carries overage_price_tier_key="
            f"{overage_price_tier_key!r}. An entitlement is never priced per "
            f"unit."
        )
    if grace_quantity is not None:
        return (
            f"{prefix} carries grace_quantity={grace_quantity}. Grace is an "
            f"allowance above a quantity, and an entitlement has none."
        )
    if _enum_value(period) != CANONICAL_PERIOD:
        return (
            f"{prefix} must use period={CANONICAL_PERIOD!r} (got "
            f"{_enum_value(period)!r}). One grant, one row: the unique index "
            f"includes period, so a second period would be a second copy."
        )
    return None


def _assert_disjoint_from_meters() -> None:
    """Refuse to import if an entitlement shares a name with a meter.

    Import time, not a unit test, because the failure it prevents is silent at
    runtime: `is_limit_key` and `is_entitlement_key` would both answer True for
    the same string, and whichever branch `_validate` tests first would decide
    what the row means. That is an ordering accident, not a design.
    """
    meters = set(USAGE_EVENT_TYPES) | {TOTAL_COST_KEY}
    collisions = sorted(set(ENTITLEMENT_KEYS) & meters)
    if collisions:
        raise RuntimeError(
            "Entitlement keys collide with the usage meter vocabulary: "
            f"{', '.join(collisions)}. A key must be either a meter "
            "(app/core/usage_events.py) or an entitlement "
            "(app/core/entitlements.py), never both."
        )


_assert_disjoint_from_meters()