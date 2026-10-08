"""Words and numbers of TruthMesh, in one place.

Kinds are domain-agnostic: procurement (invoice, PO, goods receipt), legal (MSA, SOW, amendment),
insurance and health (policy, claim, clinical note), logistics (waybill, customs manifest). A kind
has a RANK in the authority hierarchy: an agreement authorises an order, an order authorises an
invoice. Links between kinds have a RELATION name; a directed relation runs from the dependent
document (the child) to the document it depends on (the parent).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

ENGINE_VERSION: Final = "truthmesh-1"

JOB_INDEX_DOCUMENT: Final = "truthmesh.index_document"
JOB_REBUILD_WORKSPACE: Final = "truthmesh.rebuild_workspace"
#: The index job waits this long after enrichment, so the role row and entity resolution land first.
INDEX_DELAY_SECONDS: Final = 5

# --------------------------------------------------------------------------- kinds
#: kind -> (label, rank). Higher rank = more authority.
KINDS: Final[dict[str, tuple[str, int]]] = {
    "MASTER_AGREEMENT": ("Master agreement", 6),
    "CONTRACT": ("Contract", 5),
    "POLICY": ("Insurance policy", 5),
    "AMENDMENT": ("Amendment", 5),
    "STATEMENT_OF_WORK": ("Statement of work", 4),
    "PURCHASE_ORDER": ("Purchase order", 4),
    "CUSTOMS_MANIFEST": ("Customs manifest", 4),
    "INSURANCE_CLAIM": ("Insurance claim", 3),
    "GOODS_RECEIPT": ("Goods receipt", 2),
    "WAYBILL": ("Waybill", 2),
    "CLINICAL_NOTE": ("Clinical note", 2),
    "INVOICE": ("Invoice", 1),
    "CREDIT_NOTE": ("Credit note", 1),
    "STATEMENT": ("Statement", 1),
    "OTHER": ("Document", 0),
}

#: Ordered keyword rules: the first whose words appear in the classification label (or, failing
#: that, the file name) decides the kind. Specific before general ("master services agreement"
#: before "agreement", "credit note" before "note").
KIND_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("MASTER_AGREEMENT", ("master services agreement", "master service agreement", "master agreement",
                          "framework agreement", "msa")),
    ("STATEMENT_OF_WORK", ("statement of work", "sow", "work order", "scope of work")),
    ("AMENDMENT", ("amendment", "addendum", "change order", "variation")),
    ("CREDIT_NOTE", ("credit note", "credit memo", "debit note")),
    ("PURCHASE_ORDER", ("purchase order", "po", "order form")),
    ("GOODS_RECEIPT", ("goods receipt", "receipt note", "grn", "delivery receipt", "receiving report")),
    ("CUSTOMS_MANIFEST", ("customs manifest", "manifest", "customs declaration", "bill of entry",
                          "shipping bill")),
    ("WAYBILL", ("waybill", "way bill", "bill of lading", "airway bill", "consignment note", "delivery note",
                 "packing list")),
    ("INSURANCE_CLAIM", ("insurance claim", "claim form", "claim")),
    ("POLICY", ("insurance policy", "policy schedule", "certificate of insurance")),
    ("CLINICAL_NOTE", ("clinical note", "discharge summary", "medical record", "medical report", "lab report",
                       "prescription", "clinical")),
    ("INVOICE", ("tax invoice", "invoice", "bill")),
    ("STATEMENT", ("statement of account", "statement")),
    ("CONTRACT", ("contract", "agreement", "lease", "terms and conditions")),
)

#: document_roles.role -> kind (the matcher's classifier, when it decided).
ROLE_KINDS: Final[dict[str, str]] = {
    "INVOICE": "INVOICE",
    "PURCHASE_ORDER": "PURCHASE_ORDER",
    "GOODS_RECEIPT": "GOODS_RECEIPT",
    "CREDIT_NOTE": "CREDIT_NOTE",
    "CONTRACT": "CONTRACT",
    "STATEMENT": "STATEMENT",
}

AGREEMENT_KINDS: Final = frozenset({"MASTER_AGREEMENT", "CONTRACT", "POLICY", "AMENDMENT", "STATEMENT_OF_WORK"})
SPEND_KINDS: Final = frozenset({"INVOICE", "INSURANCE_CLAIM"})
AUTHORITY_KINDS: Final = frozenset({"PURCHASE_ORDER", "MASTER_AGREEMENT", "CONTRACT", "STATEMENT_OF_WORK",
                                    "POLICY", "AMENDMENT"})

# --------------------------------------------------------------------------- relations
REL_BILLS_AGAINST: Final = "BILLS_AGAINST"
REL_RECEIVES_AGAINST: Final = "RECEIVES_AGAINST"
REL_ADJUSTS: Final = "ADJUSTS"
REL_GOVERNED_BY: Final = "GOVERNED_BY"
REL_AMENDS: Final = "AMENDS"
REL_ISSUED_UNDER: Final = "ISSUED_UNDER"
REL_BILLED_UNDER: Final = "BILLED_UNDER"
REL_CLAIMS_UNDER: Final = "CLAIMS_UNDER"
REL_SUPPORTS: Final = "SUPPORTS"
REL_DECLARED_IN: Final = "DECLARED_IN"
REL_SHIPS_AGAINST: Final = "SHIPS_AGAINST"
REL_DEPENDS_ON: Final = "DEPENDS_ON"
REL_VERSION_OF: Final = "VERSION_OF"
REL_RECONCILES_WITH: Final = "RECONCILES_WITH"
REL_RELATES_TO: Final = "RELATES_TO"
REL_SHARES_PARTY: Final = "SHARES_PARTY"

#: (child kind, parent kind) -> relation. Anything not listed falls back to DEPENDS_ON (directed,
#: by rank) or RELATES_TO (equal rank).
RELATION_CATALOG: Final[dict[tuple[str, str], str]] = {
    ("INVOICE", "PURCHASE_ORDER"): REL_BILLS_AGAINST,
    ("GOODS_RECEIPT", "PURCHASE_ORDER"): REL_RECEIVES_AGAINST,
    ("CREDIT_NOTE", "INVOICE"): REL_ADJUSTS,
    ("STATEMENT_OF_WORK", "MASTER_AGREEMENT"): REL_GOVERNED_BY,
    ("STATEMENT_OF_WORK", "CONTRACT"): REL_GOVERNED_BY,
    ("AMENDMENT", "CONTRACT"): REL_AMENDS,
    ("AMENDMENT", "MASTER_AGREEMENT"): REL_AMENDS,
    ("PURCHASE_ORDER", "MASTER_AGREEMENT"): REL_ISSUED_UNDER,
    ("PURCHASE_ORDER", "CONTRACT"): REL_ISSUED_UNDER,
    ("PURCHASE_ORDER", "STATEMENT_OF_WORK"): REL_ISSUED_UNDER,
    ("INVOICE", "MASTER_AGREEMENT"): REL_BILLED_UNDER,
    ("INVOICE", "CONTRACT"): REL_BILLED_UNDER,
    ("INVOICE", "STATEMENT_OF_WORK"): REL_BILLED_UNDER,
    ("GOODS_RECEIPT", "MASTER_AGREEMENT"): REL_DEPENDS_ON,
    ("INSURANCE_CLAIM", "POLICY"): REL_CLAIMS_UNDER,
    ("CLINICAL_NOTE", "INSURANCE_CLAIM"): REL_SUPPORTS,
    ("WAYBILL", "CUSTOMS_MANIFEST"): REL_DECLARED_IN,
    ("WAYBILL", "PURCHASE_ORDER"): REL_SHIPS_AGAINST,
}
#: Siblings under one parent that are compared with each other.
SIBLING_RELATIONS: Final[dict[frozenset[str], str]] = {
    frozenset({"INVOICE", "GOODS_RECEIPT"}): REL_RECONCILES_WITH,
}
#: Relations through which a spend document draws on its parent's authorised amount.
DRAWDOWN_RELATIONS: Final = frozenset({REL_BILLS_AGAINST, REL_BILLED_UNDER, REL_ISSUED_UNDER, REL_CLAIMS_UNDER})
DIRECTED_RELATIONS: Final = frozenset(set(RELATION_CATALOG.values()) | {REL_DEPENDS_ON})

RELATION_LABELS: Final[dict[str, str]] = {
    REL_BILLS_AGAINST: "bills against",
    REL_RECEIVES_AGAINST: "receives against",
    REL_ADJUSTS: "adjusts",
    REL_GOVERNED_BY: "is governed by",
    REL_AMENDS: "amends",
    REL_ISSUED_UNDER: "is issued under",
    REL_BILLED_UNDER: "is billed under",
    REL_CLAIMS_UNDER: "claims under",
    REL_SUPPORTS: "supports",
    REL_DECLARED_IN: "is declared in",
    REL_SHIPS_AGAINST: "ships against",
    REL_DEPENDS_ON: "depends on",
    REL_VERSION_OF: "is a version of",
    REL_RECONCILES_WITH: "reconciles with",
    REL_RELATES_TO: "relates to",
    REL_SHARES_PARTY: "shares a party with",
}

# --------------------------------------------------------------------------- signals and links
SIGNAL_IDENTIFIER: Final = "IDENTIFIER"
SIGNAL_TEXT_REFERENCE: Final = "TEXT_REFERENCE"
SIGNAL_SHARED_REFERENCE: Final = "SHARED_REFERENCE"
SIGNAL_SAME_NUMBER: Final = "SAME_NUMBER"
SIGNAL_PARTY: Final = "PARTY"
SIGNAL_SEMANTIC: Final = "SEMANTIC"

#: Weights of the deterministic signals (combined by noisy-OR: 1 - prod(1 - w)).
W_IDENTIFIER: Final = 0.95
W_TEXT_REFERENCE: Final = 0.85
W_SAME_NUMBER: Final = 0.90
W_SHARED_REFERENCE: Final = 0.70
#: A shared party is weighed by how rare it is in the workspace (inverse document frequency).
W_PARTY_MAX: Final = 0.55
W_PARTY_MIN: Final = 0.15
#: Semantic similarity of document centroids: below FLOOR no signal; weight rises to W_SEMANTIC_MAX.
SEMANTIC_FLOOR: Final = 0.80
W_SEMANTIC_MIN: Final = 0.20
W_SEMANTIC_MAX: Final = 0.60
SEMANTIC_NEIGHBOURS: Final = 8

#: A link is kept at or above this strength.
MIN_LINK_STRENGTH: Final = 0.40
#: Conflicts are looked for across links at or above this strength (a rejected link never).
CONFLICT_LINK_STRENGTH: Final = 0.55

# --------------------------------------------------------------------------- conflicts
K_UNAUTHORIZED_COMMITMENT: Final = "UNAUTHORIZED_COMMITMENT"
K_CUMULATIVE_OVERRUN: Final = "CUMULATIVE_OVERRUN"
K_DUPLICATE_BILLING: Final = "DUPLICATE_BILLING"
K_AMOUNT_MISMATCH: Final = "AMOUNT_MISMATCH"
K_DATE_CONTRADICTION: Final = "DATE_CONTRADICTION"
K_OUT_OF_TERM: Final = "OUT_OF_TERM"
K_PARTY_MISMATCH: Final = "PARTY_MISMATCH"
K_PAYEE_ACCOUNT_CHANGED: Final = "PAYEE_ACCOUNT_CHANGED"
K_CURRENCY_MISMATCH: Final = "CURRENCY_MISMATCH"
K_TERM_CONFLICT: Final = "TERM_CONFLICT"
K_UNSUPPORTED_COMMITMENT: Final = "UNSUPPORTED_COMMITMENT"

CONFLICT_LABELS: Final[dict[str, str]] = {
    K_UNAUTHORIZED_COMMITMENT: "Exceeds its authority",
    K_CUMULATIVE_OVERRUN: "Cumulative overrun",
    K_DUPLICATE_BILLING: "Duplicate billing",
    K_AMOUNT_MISMATCH: "Amounts disagree",
    K_DATE_CONTRADICTION: "Contradictory dates",
    K_OUT_OF_TERM: "Outside the agreement's term",
    K_PARTY_MISMATCH: "Party not on the agreement",
    K_PAYEE_ACCOUNT_CHANGED: "Payee account changed",
    K_CURRENCY_MISMATCH: "Currencies disagree",
    K_TERM_CONFLICT: "Conflicting terms",
    K_UNSUPPORTED_COMMITMENT: "No authorising document",
}
#: Kinds whose exposure is money at risk (summed, once per exposure key, in the cockpit).
EXPOSURE_KINDS: Final = frozenset({K_UNAUTHORIZED_COMMITMENT, K_CUMULATIVE_OVERRUN, K_DUPLICATE_BILLING,
                                   K_AMOUNT_MISMATCH, K_PAYEE_ACCOUNT_CHANGED})

SEVERITY_CRITICAL: Final = "CRITICAL"
SEVERITY_HIGH: Final = "HIGH"
SEVERITY_MEDIUM: Final = "MEDIUM"
SEVERITY_LOW: Final = "LOW"
SEVERITY_ORDER: Final[dict[str, int]] = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 3}
#: Each open conflict's pull on its documents' risk (combined by noisy-OR).
SEVERITY_RISK: Final[dict[str, float]] = {SEVERITY_CRITICAL: 0.60, SEVERITY_HIGH: 0.40, SEVERITY_MEDIUM: 0.20,
                                          SEVERITY_LOW: 0.08}

#: Money tolerance: an absolute amount (in the document's currency) and a relative share.
MONEY_TOLERANCE: Final = Decimal("0.01")
MONEY_RELATIVE_TOLERANCE: Final = Decimal("0.005")

# --------------------------------------------------------------------------- what-if
SCENARIO_DELAY: Final = "DELAY"
SCENARIO_CLAUSE_INVOKED: Final = "CLAUSE_INVOKED"
SCENARIO_AMOUNT_CHANGE: Final = "AMOUNT_CHANGE"
SCENARIO_TERMINATION: Final = "TERMINATION"
SCENARIO_PARTY_DEFAULT: Final = "PARTY_DEFAULT"
SCENARIOS: Final[dict[str, str]] = {
    SCENARIO_DELAY: "Delivery or performance delayed",
    SCENARIO_CLAUSE_INVOKED: "A clause is invoked",
    SCENARIO_AMOUNT_CHANGE: "Price or amount changes",
    SCENARIO_TERMINATION: "The agreement is terminated",
    SCENARIO_PARTY_DEFAULT: "The counterparty defaults",
}
RIPPLE_MAX_DEPTH: Final = 4
RIPPLE_MIN_IMPACT: Final = 0.15
RIPPLE_MAX_NODES: Final = 120
#: How strongly a change travels along a relation, by direction (parent -> child is "down").
PROPAGATION_DOWN: Final = 0.95
PROPAGATION_UP: Final = 0.80
PROPAGATION_PEER: Final = 0.60
PROPAGATION_PARTY: Final = 0.40

DIMENSION_FINANCIAL: Final = "FINANCIAL"
DIMENSION_OPERATIONAL: Final = "OPERATIONAL"
DIMENSION_LEGAL: Final = "LEGAL"

# --------------------------------------------------------------------------- limits
MAX_NODES_PER_BUILD: Final = 5_000
NEIGHBOURHOOD_HOPS: Final = 2
NEIGHBOURHOOD_MAX: Final = 400
GRAPH_MAX_NODES: Final = 400
TEXT_SCAN_CHARS: Final = 60_000

__all__ = [name for name in dir() if name.isupper()]
