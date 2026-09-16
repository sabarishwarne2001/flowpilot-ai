"""ARCH-34 — the closed vocabularies the schema and the pure engines both read.

WHY THIS IS NOT IN `app/models/radar.py`
========================================

Identical reasoning to `app/services/assertions/vocabulary.py`,
`app/services/redaction/vocabulary.py` and
`app/services/procurement_matching/vocabulary.py`, and deliberately the same
shape so there is one pattern in the tree rather than four.

  * `fingerprint.py`, every module under `layers/`, `price_surge.py` and
    `drift.py` are pure by contract — no Session, no clock, no network, and
    no third-party import. `input_digest` is only meaningful because of it.

  * `verify_arch34.py` loads these modules by file path to run its offline and
    mutation gates. A gate that needs a configured database to run is a gate
    that gets skipped in exactly the circumstances it is most needed.

So the vocabulary lives here, importing nothing but the standard library, and
both sides import it: `app/models/radar.py` for the CHECK constraints and the
engines for their own logic. `verify_arch34.py` asserts the tuples below equal
the CHECK constraint bodies in `arch34_step1_radar`, so a fifth layer added in
one place and not the other fails a gate rather than a production insert.

LAYER IS NOT KIND, AND BOTH COLUMNS EXIST
=========================================

`kind` answers "what is this finding about" — three values, and it is what the
console groups by and what ARCH-13's `anomaly.detected` trigger filters on.
`layer` answers "what evidence decided it" — six values, and it is what the
reader needs to know how much to trust it.

They are not derivable from one another in the direction that matters. Every
`L0..L3` layer maps to `DUPLICATE_DOCUMENT`, so kind can be derived from
layer; layer cannot be derived from kind, because `DUPLICATE_DOCUMENT` is four
very different strengths of claim. §5.3 is explicit that "same invoice number
from the same vendor" and "looks similar" must never be presented as the same
strength of evidence, and collapsing the two columns into one is precisely how
that happens six months later in a `GROUP BY`.

`KIND_FOR_LAYER` below is the single place the derivation is written down, and
`ck_af_kind_matches_layer` in the migration enforces it in SQL.

THE STRONGEST-LAYER RULE LIVES IN `LAYER_STRENGTH`
==================================================

A byte-identical pair is also, trivially, a near-duplicate pair and a
semantically similar pair. Reporting all three turns one clean signal into a
queue of three rows a human has to dismiss twice. `layers.strongest()` walks
`DUPLICATE_LAYER_ORDER` and returns the FIRST layer that fires, and the order
is declared here rather than in the walk so that a gate can assert it.

WHY L3 IS CAPPED AT MEDIUM
==========================

§5.9: embedding similarity is evidence, not proof. Two renewal quotes from the
same supplier in consecutive months are legitimately near-identical in
embedding space and are not duplicates of each other. A HIGH finding is a
claim strong enough that a tenant's ARCH-13 rule may hold a payment on it, and
cosine alone does not earn that. `severity_for()` refuses to return HIGH for
an uncorroborated L3 finding, and `verify_arch34.py` gates the refusal.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = [
    "ENGINE_VERSION",
    "KIND_DUPLICATE_DOCUMENT",
    "KIND_PRICE_SURGE",
    "KIND_CONTRACT_DRIFT",
    "KINDS",
    "LAYER_L0",
    "LAYER_L1",
    "LAYER_L2",
    "LAYER_L3",
    "LAYER_PRICE_SURGE",
    "LAYER_CONTRACT_DRIFT",
    "LAYERS",
    "DUPLICATE_LAYERS",
    "DUPLICATE_LAYER_ORDER",
    "PAIRWISE_LAYERS",
    "UNPAIRED_LAYERS",
    "LAYER_STRENGTH",
    "KIND_FOR_LAYER",
    "kind_for_layer",
    "is_stronger",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SEVERITY_HIGH",
    "SEVERITIES",
    "SEVERITY_RANK",
    "severity_for",
    "STATUS_OPEN",
    "STATUS_CONFIRMED",
    "STATUS_DISMISSED",
    "STATUSES",
    "MINHASH_PERMUTATIONS",
    "MINHASH_SEED",
    "MINHASH_PRIME",
    "MINHASH_MAX_HASH",
    "SHINGLE_WORDS",
    "L2_JACCARD_MIN",
    "L2_TOTAL_TOLERANCE",
    "L2_DATE_WINDOW_DAYS",
    "L3_COSINE_MIN",
    "L2_HIGH_JACCARD",
    "DRIFT_ALIGNMENT_MIN",
    "PRICE_MIN_OBSERVATIONS",
    "PRICE_TRAILING_MONTHS",
    "PRICE_Z_ABS_MIN",
    "PRICE_MIN_RELATIVE_CHANGE",
    "MAD_CONSISTENCY",
    "BASIS_ROBUST_Z",
    "BASIS_RELATIVE_ONLY",
    "SURGE_BASES",
    "DRIFT_SAME",
    "DRIFT_CHANGED",
    "DRIFT_UNDETERMINED",
    "DRIFT_STATUSES",
    "EVIDENCE_IDENTIFIERS",
    "EVIDENCE_SHINGLES",
    "EVIDENCE_CHUNK_PAIR",
    "EVIDENCE_PRICE_SERIES",
    "EVIDENCE_CLAUSE_PAIR",
    "EVIDENCE_KINDS",
    "MAX_EVIDENCE_ITEMS",
    "MAX_EVIDENCE_TEXT",
    "MAX_SHINGLE_SAMPLES",
    "CAPABILITY_ANOMALY_RADAR",
    "USAGE_EVENT_RADAR_SWEEP",
    "OUTBOX_EVENT_ANOMALY_DETECTED",
    "JOB_ANOMALY_SCAN_DOCUMENT",
    "JOB_ANOMALY_NIGHTLY",
    "EMBEDDING_DIMENSION",
]

#: Bumped whenever a change to the shingler, the permutation family, a layer
#: threshold or the surge statistics could produce a different answer for the
#: same pair of documents. It is part of every `input_digest`, so a re-sweep
#: under a new engine is legitimately new work rather than a cache hit on a
#: stale answer.
#:
#: NOTE THE ASYMMETRY WITH THE MINHASH SEED. Bumping this is cheap and
#: routine. Changing `MINHASH_SEED` is neither: see its own comment.
ENGINE_VERSION: str = "arch34.1"


# ===========================================================================
# Kinds and layers
# ===========================================================================

KIND_DUPLICATE_DOCUMENT: str = "DUPLICATE_DOCUMENT"
KIND_PRICE_SURGE: str = "PRICE_SURGE"
KIND_CONTRACT_DRIFT: str = "CONTRACT_DRIFT"

#: Order matters and is load-bearing: it is the order `verify_arch34.py`
#: asserts against the migration's `ck_af_kind_known` body.
KINDS: tuple[str, ...] = (
    KIND_DUPLICATE_DOCUMENT,
    KIND_PRICE_SURGE,
    KIND_CONTRACT_DRIFT,
)

LAYER_L0: str = "L0"
LAYER_L1: str = "L1"
LAYER_L2: str = "L2"
LAYER_L3: str = "L3"
LAYER_PRICE_SURGE: str = "PRICE_SURGE"
LAYER_CONTRACT_DRIFT: str = "CONTRACT_DRIFT"

LAYERS: tuple[str, ...] = (
    LAYER_L0,
    LAYER_L1,
    LAYER_L2,
    LAYER_L3,
    LAYER_PRICE_SURGE,
    LAYER_CONTRACT_DRIFT,
)

#: The four duplicate layers, STRONGEST FIRST. `layers.strongest()` walks this
#: and stops at the first hit. Reversing it would report every byte-identical
#: pair as a semantic near-match, which is the failure §5.3 exists to prevent,
#: and it is the mutant `verify_arch34.py` requires to die.
DUPLICATE_LAYER_ORDER: tuple[str, ...] = (LAYER_L0, LAYER_L1, LAYER_L2, LAYER_L3)

#: Same members, declared separately because the SQL CHECK cares about
#: membership and the engine cares about order. Two names make a reordering
#: visible in a diff instead of silently changing engine behaviour.
DUPLICATE_LAYERS: frozenset[str] = frozenset(DUPLICATE_LAYER_ORDER)

#: Layers whose finding MUST name a counterpart work item. This is the set the
#: migration's `ck_af_pairwise_has_counterpart` is written from, and it is the
#: constraint that carries the phase: a duplicate finding without the document
#: it duplicates is an accusation with nothing behind it.
#:
#: CONTRACT_DRIFT is here and PRICE_SURGE is not. Drift is by construction a
#: statement about TWO documents ("Net 30 in MSA-2025, Net 60 in SOW-7") and a
#: drift row with one side is unreadable. A price surge is a statement about
#: one document against a statistical baseline; there is no second document to
#: point at, and inventing one would mean picking an arbitrary historical
#: invoice and implying it is the comparison, which it is not.
PAIRWISE_LAYERS: frozenset[str] = frozenset(
    (LAYER_L0, LAYER_L1, LAYER_L2, LAYER_L3, LAYER_CONTRACT_DRIFT)
)

UNPAIRED_LAYERS: frozenset[str] = frozenset((LAYER_PRICE_SURGE,))

#: Lower is stronger. Used by `is_stronger()` and by the console's sort.
LAYER_STRENGTH: dict[str, int] = {
    LAYER_L0: 0,
    LAYER_L1: 1,
    LAYER_L2: 2,
    LAYER_L3: 3,
}

#: The one place `layer -> kind` is written down. `ck_af_kind_matches_layer`
#: in the migration is generated from it, so a row cannot claim
#: `kind='PRICE_SURGE', layer='L2'`.
KIND_FOR_LAYER: dict[str, str] = {
    LAYER_L0: KIND_DUPLICATE_DOCUMENT,
    LAYER_L1: KIND_DUPLICATE_DOCUMENT,
    LAYER_L2: KIND_DUPLICATE_DOCUMENT,
    LAYER_L3: KIND_DUPLICATE_DOCUMENT,
    LAYER_PRICE_SURGE: KIND_PRICE_SURGE,
    LAYER_CONTRACT_DRIFT: KIND_CONTRACT_DRIFT,
}


def kind_for_layer(layer: str) -> str:
    """The kind a layer belongs to, refusing an unknown layer loudly.

    `.get(layer, KIND_DUPLICATE_DOCUMENT)` would be the obvious body and is
    wrong: a typo would file a price surge as a duplicate, the console would
    render it in the duplicates tab with a side-by-side comparison of one
    document against itself, and nothing would raise.
    """
    if layer not in KIND_FOR_LAYER:
        raise ValueError(
            f"{layer!r} is not a known radar layer. Known: {', '.join(LAYERS)}."
        )
    return KIND_FOR_LAYER[layer]


def is_stronger(left: str, right: str) -> bool:
    """Whether duplicate layer `left` is a stronger claim than `right`."""
    if left not in LAYER_STRENGTH or right not in LAYER_STRENGTH:
        raise ValueError(
            "is_stronger compares DUPLICATE layers only. PRICE_SURGE and "
            "CONTRACT_DRIFT are not ranked against them: they answer a "
            "different question and a document can legitimately carry one of "
            f"each. Got {left!r} and {right!r}."
        )
    return LAYER_STRENGTH[left] < LAYER_STRENGTH[right]


# ===========================================================================
# Severity and status
# ===========================================================================

SEVERITY_LOW: str = "LOW"
SEVERITY_MEDIUM: str = "MEDIUM"
SEVERITY_HIGH: str = "HIGH"

SEVERITIES: tuple[str, ...] = (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH)

SEVERITY_RANK: dict[str, int] = {
    SEVERITY_LOW: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_HIGH: 2,
}

STATUS_OPEN: str = "OPEN"
STATUS_CONFIRMED: str = "CONFIRMED"
STATUS_DISMISSED: str = "DISMISSED"

STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_CONFIRMED, STATUS_DISMISSED)


def severity_for(layer: str, *, score: Decimal, corroborated: bool = False) -> str:
    """Severity policy, in one place, as a pure function of the evidence.

    `corroborated` means a SECOND layer independently fired on the same pair.
    It is the only thing that lifts an L3 finding to HIGH, per §5.9.

    Note what is deliberately absent: the amount of money involved. A ₹400
    duplicate and a ₹4,00,000 duplicate are the same accusation with the same
    evidential strength, and a severity that rose with the total would train
    reviewers to ignore small duplicates — which is exactly the population a
    fraudulent re-submission hides in.
    """
    if layer not in LAYERS:
        raise ValueError(
            f"{layer!r} is not a known radar layer. Known: {', '.join(LAYERS)}."
        )

    if layer in (LAYER_L0, LAYER_L1):
        # Byte equality and vendor+number equality are facts, not estimates.
        return SEVERITY_HIGH

    if layer == LAYER_L2:
        return SEVERITY_HIGH if score >= L2_HIGH_JACCARD else SEVERITY_MEDIUM

    if layer == LAYER_L3:
        # The cap. Cosine alone never earns HIGH, however close to 1 it is.
        return SEVERITY_HIGH if corroborated else SEVERITY_MEDIUM

    if layer == LAYER_PRICE_SURGE:
        # The score carried on a surge finding is |z| scaled into [0, 1] by
        # `price_surge.confidence()`; the band below matches §5.6's mock,
        # where a 7.1% rise on a tight series reads MEDIUM rather than HIGH.
        return SEVERITY_HIGH if score >= Decimal("0.90") else SEVERITY_MEDIUM

    # CONTRACT_DRIFT. MEDIUM by construction: two contract versions disagreeing
    # is a fact worth a person's attention and is almost never an incident.
    # §5.6's mock shows exactly this.
    return SEVERITY_MEDIUM


# ===========================================================================
# MinHash
# ===========================================================================

#: §5.3. Fixed forever at 128. The standard error of the Jaccard estimate is
#: 1/sqrt(k), so 128 gives roughly ±0.088 at one sigma — which is why the gate
#: tolerance is ±0.05 on CURATED pairs chosen to be far from the threshold
#: rather than a promise about arbitrary pairs.
MINHASH_PERMUTATIONS: int = 128

#: The seed for the permutation family. CHANGING THIS IS A DATA MIGRATION, NOT
#: A TUNING CHANGE.
#:
#: Signatures are stored on `document_fingerprints` and compared against
#: signatures computed months earlier. A different seed produces a different
#: permutation family, and two signatures from different families have no
#: meaningful Jaccard between them — they would not error, they would return
#: a plausible-looking number that means nothing. Every stored fingerprint
#: would silently become incomparable with every new one, and the only symptom
#: would be duplicate detection quietly getting worse.
#:
#: If it ever must change: bump `ENGINE_VERSION`, add a nullable
#: `signature_family` column, and recompute. Do not edit this line alone.
MINHASH_SEED: int = 0x464C4F57  # "FLOW"

#: 2**61 - 1. A Mersenne prime, so `(a*x + b) mod p` is exact in Python's
#: arbitrary-precision integers with no floating point anywhere in the hash
#: path. The same prime `datasketch` uses — chosen for the same reason, and
#: arrived at without taking the dependency.
MINHASH_PRIME: int = (1 << 61) - 1

#: Signature values are folded to 32 bits after permutation. This is the
#: standard construction and it bounds the stored row: 128 values that each
#: fit in four bytes of meaning.
#:
#: THEY ARE STILL STORED IN `bigint[]`, NOT `integer[]`. See the migration:
#: Postgres `integer` is SIGNED, so any permuted value above 2,147,483,647
#: overflows it, and roughly half of them are. §5.4's `minhash integer[]` is
#: the one line of the roadmap this phase does not implement literally.
MINHASH_MAX_HASH: int = (1 << 32) - 1

#: Words per shingle. Five is the usual choice for prose and it is the right
#: one here for a reason specific to invoices: a three-word shingle over line
#: descriptions collides constantly ("1 x A4" appears on everything), and a
#: seven-word shingle stops matching once a rescan reflows a line.
SHINGLE_WORDS: int = 5


# ===========================================================================
# Layer thresholds
# ===========================================================================

#: §5.3. Decimal, not float, because this value is serialised into every
#: `input_digest` and `0.85` is not representable in binary floating point —
#: two runs on two machines must digest the same string.
L2_JACCARD_MIN: Decimal = Decimal("0.85")

#: Above this an L2 finding is HIGH rather than MEDIUM. §5.6's mock headline
#: is "98% of line items match", which must read HIGH.
L2_HIGH_JACCARD: Decimal = Decimal("0.95")

#: §5.3's corroborating guards on L2. Totals within 0.5%, dates within 45
#: days. Both are applied ONLY when both sides carry the value — an absent
#: total is not evidence of difference, and disqualifying on it would make L2
#: silently depend on how well extraction did rather than on the text.
L2_TOTAL_TOLERANCE: Decimal = Decimal("0.005")
L2_DATE_WINDOW_DAYS: int = 45

#: §5.3.
L3_COSINE_MIN: Decimal = Decimal("0.97")

#: §5.3. Clause alignment for drift.
DRIFT_ALIGNMENT_MIN: Decimal = Decimal("0.80")


# ===========================================================================
# Price surge
# ===========================================================================

PRICE_MIN_OBSERVATIONS: int = 5
PRICE_TRAILING_MONTHS: int = 12
PRICE_Z_ABS_MIN: Decimal = Decimal("3.5")
PRICE_MIN_RELATIVE_CHANGE: Decimal = Decimal("0.10")

#: 0.6745 is the 75th percentile of the standard normal, which is what makes
#: MAD a consistent estimator of sigma for normally distributed data. It is
#: written as a Decimal for the digest-stability reason above.
MAD_CONSISTENCY: Decimal = Decimal("0.6745")

#: The two ways a surge can be decided, recorded ON the finding.
BASIS_ROBUST_Z: str = "ROBUST_Z"

#: MAD = 0. A supplier who billed exactly the same amount every time has a
#: median absolute deviation of zero, and the robust z of any different price
#: is a division by zero — mathematically "infinitely many deviations away",
#: which is true and useless. The engine falls back to the relative-change
#: test alone and SAYS SO on the finding, rather than emitting `inf`, adding
#: an epsilon to the denominator, or dropping the series.
#:
#: An epsilon would be the worst of the three: it produces a finite, enormous,
#: entirely arbitrary z that a reviewer would reasonably read as a measured
#: quantity.
BASIS_RELATIVE_ONLY: str = "RELATIVE_ONLY"

SURGE_BASES: tuple[str, ...] = (BASIS_ROBUST_Z, BASIS_RELATIVE_ONLY)


# ===========================================================================
# Drift
# ===========================================================================

DRIFT_SAME: str = "SAME"
DRIFT_CHANGED: str = "CHANGED"

#: A family could not read one side. §5.3: unparseable differences are NOT
#: findings — they are what ARCH-33 assertions are for. The value exists so
#: the engine can say why it produced nothing, which is the same posture
#: `money_bound` takes on a unit mismatch.
DRIFT_UNDETERMINED: str = "UNDETERMINED"

DRIFT_STATUSES: tuple[str, ...] = (DRIFT_SAME, DRIFT_CHANGED, DRIFT_UNDETERMINED)


# ===========================================================================
# Evidence
# ===========================================================================

EVIDENCE_IDENTIFIERS: str = "identifiers"
EVIDENCE_SHINGLES: str = "shingles"
EVIDENCE_CHUNK_PAIR: str = "chunk_pair"
EVIDENCE_PRICE_SERIES: str = "price_series"
EVIDENCE_CLAUSE_PAIR: str = "clause_pair"

EVIDENCE_KINDS: tuple[str, ...] = (
    EVIDENCE_IDENTIFIERS,
    EVIDENCE_SHINGLES,
    EVIDENCE_CHUNK_PAIR,
    EVIDENCE_PRICE_SERIES,
    EVIDENCE_CLAUSE_PAIR,
)

#: `anomaly_findings.evidence` is read by a human in a browser. A finding
#: carrying four hundred matched shingles is not more convincing than one
#: carrying twelve; it is just unreadable, and it puts a page of text in a
#: JSONB column on the feed query's hot path.
MAX_EVIDENCE_ITEMS: int = 12
MAX_EVIDENCE_TEXT: int = 400
MAX_SHINGLE_SAMPLES: int = 12


# ===========================================================================
# Commercial and infrastructure keys
# ===========================================================================

#: A CAPABILITY, never an ADDON. `app/core/entitlements.py` carries the same
#: literal and the reasoning; `verify_arch34.py` asserts the two agree.
#: `ADDON_KEYS` is asserted equal to `entitlement_service`'s priced catalog at
#: import, so registering this there would fail at boot.
#: ARCH34-S2:radar-vocabulary-names. The key is `capability.anomaly_radar`,
#: which is what §5.7 names and what `app/core/entitlements.py` registers.
#: Tranche 1 shipped `capability.forensic_radar` from the master prompt; the
#: spec wins, because the key appears in a published tier version and a
#: capability nobody's plan carries is a feature that is silently off.
CAPABILITY_ANOMALY_RADAR: str = "capability.anomaly_radar"

#: METERED ON THE SWEEP, NEVER ON THE FINDING.
#:
#: §5.7 says the radar is "not metered per finding, because charging per
#: anomaly found creates the wrong incentive", and it is right: a meter that
#: counts findings pays the vendor more the noisier its detector is, and the
#: person holding the bill is the one who has to dismiss each one. The master
#: prompt's §7 asks for the unit of value to be metered, keyed on the input
#: digest. Both are satisfied by metering the SWEEP.
#:
#: REQUEST, like `procurement.case` and unlike `redaction.page`, because the
#: cost here is the assignment rather than the paper. A sweep is one
#: fingerprint comparison pass over a workspace's candidate set; a forty-page
#: scan and a one-page letter cost the same MinHash comparison and the same
#: pgvector probe. Metering per page would price a long contract as forty
#: sweeps of work that was never done.
#:
#: Keyed on `input_digest`: a re-sweep that finds nothing changed writes no
#: row and emits no usage event, so an idle nightly job bills nothing forever.
USAGE_EVENT_RADAR_SWEEP: str = "radar.sweep"

#: ARCH-13 trigger. Filterable by kind and severity per §5.5.
OUTBOX_EVENT_ANOMALY_DETECTED: str = "anomaly.detected"

#: The two job types. Both LIGHT. Registering either in
#: `app/workers/handlers/__init__.py` without adding it to the LIGHT profile
#: in `app/workers/profiles.py` stops the entire fleet booting, because
#: `assert_imports_match_profile()` raises `ProfileError` at every worker's
#: startup on a handler no profile claims.
#: `radar.sweep` was BOTH a job type and the usage event name in Tranche 1.
#: Renaming the jobs leaves it unambiguously the meter — a job type and a
#: meter sharing one string is survivable and is exactly the collision that
#: costs an hour the first time somebody greps for it.
JOB_ANOMALY_SCAN_DOCUMENT: str = "anomaly.scan_document"
JOB_ANOMALY_NIGHTLY: str = "anomaly.nightly"

#: What `document_chunks.embedding` is declared with, copied from
#: `arch11_step2_chunks_expand`. `verify_arch34.py` asserts this equals
#: `app.core.embeddings.active_dimension()` AND the declared width of
#: `document_fingerprints.embedding`, so a model swap that changes the
#: dimension fails a gate rather than a production insert.
EMBEDDING_DIMENSION: int = 384
