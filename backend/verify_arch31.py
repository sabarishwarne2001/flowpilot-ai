#!/usr/bin/env python3
"""ARCH-31 Steps 1-4 verification — schema, matcher, digest, tolerances.

    python verify_arch31.py
    python verify_arch31.py --mutate
    python verify_arch31.py --db --database-url postgresql+psycopg://...

EXIT 0 pass | 1 a gate failed | 2 harness could not run

SCOPE
=====

Steps 1 and 2. The Step 3/4 gates named in the ARCH-31 brief — approve refused
without an override reason, the entitlement refusal envelope, and the job
registered in all three places — are NOT here, because the code they test does
not exist yet. A gate that asserts nothing about absent code passes green and
teaches the reader that the phase is further along than it is; this file names
them as pending in `report_pending()` instead.

WHY THE MUTANT SET IS SHORT AND SPECIFIC
========================================

Each mutant is a one-character or one-line change that produces NO symptom:
no crash, no log line, no failing request. They are the changes a future
refactor makes by accident and nobody notices for a quarter.

  `<=` -> `<`            a fuller review queue everyone blames on suppliers
  rejection removed      confident variances on invented pairings
  digest drops policy    tolerance changes that appear to do nothing

The CONTROL mutant must SURVIVE. A mutation suite where everything dies is
usually a suite whose gates are too broad to locate anything — it proves the
tests react, not that they discriminate.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import sys
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
ROOT = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MIGRATION_SCHEMA = "alembic/versions/arch31_step1_procurement_matching.py"
MIGRATION_VOCAB = "alembic/versions/arch31_step1_procurement_vocabulary.py"


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append((name, False, f"{type(exc).__name__}: {exc}"))
            return False
        self.results.append((name, True, ""))
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok and detail:
                for line in detail.splitlines():
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig")


# ===========================================================================
# Fixtures
# ===========================================================================


def _doc(LE: Any, lines: list[dict[str, Any]], **header: Any) -> Any:
    base: dict[str, Any] = {
        "vendor_name": "Acme Pvt Ltd",
        "vendor_tax_id": "29AABCU9603R1ZM",
        "currency": "INR",
        "date": "01/04/2026",
    }
    base.update(header)
    base["line_items"] = lines
    return LE.extract_lines(
        base, workspace_currency="INR", date_format="DD/MM/YYYY"
    )


# ===========================================================================
# Step 1 — schema
# ===========================================================================


def gates_schema(rec: Recorder) -> None:
    schema = _read(BACKEND / MIGRATION_SCHEMA)
    vocab = _read(BACKEND / MIGRATION_VOCAB)
    vocabulary = _load(
        BACKEND / "app/services/procurement_matching/vocabulary.py", "_a31_vocab"
    )

    def revision_chain() -> None:
        assert 'down_revision = "arch31_step0_document_roles"' in vocab, (
            "the vocabulary migration must follow Step 0"
        )
        assert 'down_revision = "arch31_step1_procurement_vocabulary"' in schema, (
            "the schema migration must follow the vocabulary migration; "
            "ALTER TYPE ADD VALUE cannot be used in the transaction that ran it"
        )
        assert "autocommit_block" in vocab, (
            "ALTER TYPE ... ADD VALUE runs outside a transaction block"
        )

    rec.check("Step 1 migrations chain from Step 0 in the right order", revision_chain)

    def isolation_columns() -> None:
        upgrade = schema[schema.index("def upgrade") : schema.index("def downgrade")]
        for table in (
            "procurement_cases",
            "procurement_case_lines",
            "procurement_tolerance_policies",
        ):
            assert f'"{table}"' in upgrade, f"{table} not created"
        # ARCH-02: every table carries both, both NOT NULL.
        assert upgrade.count('"organization_id"') >= 3, (
            "every procurement table must carry organization_id (ARCH-02)"
        )
        assert upgrade.count('"workspace_id"') >= 3, (
            "every procurement table must carry workspace_id (ARCH-02)"
        )

    rec.check("Step 1 all three tables carry both isolation columns", isolation_columns)

    def vocabulary_matches_checks() -> None:
        # The constants and the CHECK bodies are two statements of one fact.
        # A seventh outcome added to one and not the other is a production
        # insert failure; asserting the agreement here makes it a gate.
        def declared(value: str) -> bool:
            # Either quote style. The first cut of this gate checked only
            # single quotes and failed on NEEDS_REVIEW, which appears in the
            # migration's own CASE_STATUSES tuple in double quotes — a gate
            # asserting a formatting habit rather than a fact.
            return f"'{value}'" in schema or f'"{value}"' in schema

        for status in vocabulary.CASE_STATUSES:
            assert declared(status), f"case status {status} missing from the migration"
        for outcome in vocabulary.LINE_OUTCOMES:
            assert declared(outcome), f"line outcome {outcome} missing from the migration"
        for status in vocabulary.POLICY_STATUSES:
            assert declared(status), f"policy status {status} missing from the migration"
        assert vocabulary.RED_OUTCOMES == frozenset(vocabulary.LINE_OUTCOMES) - {
            vocabulary.OUTCOME_MATCHED
        }, "RED_OUTCOMES must be every outcome that is not a clean match"
        assert vocabulary.CASE_STATUS_SUPERSEDED not in vocabulary.LIVE_CASE_STATUSES, (
            "a SUPERSEDED case must not count as live, or the partial unique "
            "index and the model disagree about what 'live' means"
        )

    rec.check("Step 1 vocabulary constants equal the CHECK constraint bodies", vocabulary_matches_checks)

    def constraints_declared() -> None:
        required = (
            "ck_procurement_cases_two_sided",
            "ck_procurement_cases_status",
            "ck_procurement_cases_digest_shape",
            "uq_procurement_cases_live_invoice",
            "ck_procurement_case_lines_outcome",
            "ck_procurement_case_lines_outcome_matches_sides",
            "ck_procurement_tolerance_policies_status",
            "ck_procurement_tolerance_policies_pair_cost_range",
            "uq_procurement_tolerance_policies_version",
            "trg_procurement_tolerance_policies_immutable",
        )
        for name in required:
            assert name in schema, f"{name} is not declared in the migration"

    rec.check("Step 1 the load-bearing constraints are declared in the migration", constraints_declared)

    def live_index_excludes_superseded() -> None:
        assert "status <> 'SUPERSEDED'" in schema, (
            "the one-live-case index must exclude SUPERSEDED, or a re-score "
            "under a new policy cannot open its replacement"
        )
        assert "invoice_work_item_id IS NOT NULL" in schema, (
            "the index must also exclude NULL invoices, or two PO-vs-receipt "
            "cases in one workspace collide"
        )

    rec.check("Step 1 one-live-case index is partial on both axes", live_index_excludes_superseded)

    def immutability_is_at_the_database() -> None:
        assert "BEFORE UPDATE OR DELETE ON procurement_tolerance_policies" in schema, (
            "the published-policy trigger must cover DELETE as well as UPDATE"
        )
        assert "OLD.status = 'PUBLISHED'" in schema, (
            "the trigger must inspect OLD, so that DRAFT -> PUBLISHED is "
            "permitted and every write after it is refused"
        )
        assert "IF TG_OP = 'DELETE' THEN" in schema, (
            "a BEFORE DELETE trigger returning NEW returns NULL and CANCELS "
            "the delete; drafts must stay deletable"
        )

    rec.check("Step 1 published-policy immutability is a DB trigger, not a service rule", immutability_is_at_the_database)


# ===========================================================================
# Step 2 — matcher
# ===========================================================================


def gates_matcher(rec: Recorder, mods: dict[str, Any]) -> None:
    LE, P, M, D, S = (
        mods["line_extraction"],
        mods["policy"],
        mods["matcher"],
        mods["digest"],
        mods["similarity"],
    )

    def tolerance_boundary_is_inclusive() -> None:
        # 1.00 INR of absolute slack.
        pol = P.TolerancePolicy(
            policy_version="t:1", price_tolerance_micros=1_000_000, max_pair_cost=600_000
        )
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])

        for label, rate, expected in (
            ("exactly +tolerance", "101.00", M.OUTCOME_MATCHED),
            ("exactly -tolerance", "99.00", M.OUTCOME_MATCHED),
            ("one micro beyond +", "101.000001", "PRICE_VARIANCE"),
            ("one micro beyond -", "98.999999", "PRICE_VARIANCE"),
        ):
            invoice = _doc(
                LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": rate}]
            )
            got = M.match(po=po, invoice=invoice, policy=pol).lines[0].outcome
            assert got == expected, (
                f"{label}: rate {rate} gave {got}, want {expected}. A variance "
                f"of exactly the configured tolerance is WITHIN tolerance; "
                f"flipping <= to < makes the setting mean 'strictly less "
                f"than', which is not what the field says."
            )

    rec.check("tolerance boundary: exactly +/- passes, one micro beyond fails", tolerance_boundary_is_inclusive)

    def relative_tolerance_applies() -> None:
        pol = P.TolerancePolicy(
            policy_version="t:1", price_tolerance_bps=100, max_pair_cost=600_000
        )
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "1000.00"}])
        at = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "1010.00"}])
        beyond = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "1010.01"}])
        assert M.match(po=po, invoice=at, policy=pol).lines[0].outcome == M.OUTCOME_MATCHED
        assert M.match(po=po, invoice=beyond, policy=pol).lines[0].outcome == "PRICE_VARIANCE"
        # Absolute OR relative, never AND: a policy with only a bps figure
        # must still be able to pass a line.
        assert P.within_price_tolerance(1_000_000_000, 1_010_000_000, pol) is True

    rec.check("price tolerance is absolute OR relative, not AND", relative_tolerance_applies)

    def forced_pairing_is_rejected() -> None:
        po = _doc(LE, [{"description": "Steel Bar 40mm", "sku": "SB40", "qty": "10", "rate": "2345.60"}])
        invoice = _doc(LE, [{"description": "Consulting services, March", "sku": "CONS", "qty": "1", "rate": "50000.00"}])

        strict = M.match(
            po=po, invoice=invoice,
            policy=P.TolerancePolicy(policy_version="t:1", max_pair_cost=600_000),
        )
        outcomes = {line.outcome for line in strict.lines}
        assert outcomes == {"NOT_ORDERED", "NOT_INVOICED"}, (
            f"two unrelated lines were paired anyway: {outcomes}. A "
            "minimum-cost assignment pairs every row it can; the "
            "post-assignment ceiling is the only thing between the reviewer "
            "and an invented relationship."
        )
        assert all(line.pair_cost is None for line in strict.lines)

        # With the ceiling effectively disabled the solver DOES pair them,
        # which is what makes the gate above meaningful rather than vacuous.
        loose = M.match(
            po=po, invoice=invoice,
            policy=P.TolerancePolicy(policy_version="t:1", max_pair_cost=999_999),
        )
        assert len(loose.lines) == 1 and loose.lines[0].pair_cost is not None, (
            "the fixture does not actually exceed the threshold; the "
            "rejection gate would pass even with rejection removed"
        )

    rec.check("forced pairing above max_pair_cost is rejected", forced_pairing_is_rejected)

    def indian_parsing_end_to_end() -> None:
        po = LE.extract_lines(
            {
                "vendor_name": "Acme Pvt Ltd", "vendor_tax_id": "29AABCU9603R1ZM",
                "po_number": "PO-000123", "date": "03/04/2026", "currency": "INR",
                "total_amount": "1,23,456.00",
                "line_items": [{"description": "Hex Bolt M8", "sku": "HB-M8", "qty": "1,000", "rate": "123.456"}],
            },
            workspace_currency="INR", date_format="DD/MM/YYYY",
        )
        assert po.header.total_micros == 123_456_000_000, po.header.total_micros
        assert po.lines[0].quantity == Decimal("1000"), po.lines[0].quantity
        assert po.lines[0].unit_price_micros == 123_456_000, po.lines[0].unit_price_micros
        assert po.header.document_date.isoformat() == "2026-04-03"
        # A different rendering of the same vendor must be one vendor.
        invoice = LE.extract_lines(
            {"vendor_name": "ACME INDIA", "gstin": "29aabcu9603r1zm", "currency": "INR",
             "date": "03/04/2026", "line_items": []},
            workspace_currency="INR", date_format="DD/MM/YYYY",
        )
        assert po.header.vendor_key == invoice.header.vendor_key

    rec.check("Indian grouping and DD/MM dates survive end to end", indian_parsing_end_to_end)

    def eu_parsing_end_to_end() -> None:
        po = LE.extract_lines(
            {
                "vendor_name": "Müller GmbH", "vendor_tax_id": "DE123456789",
                "date": "03.04.2026", "currency": "EUR", "total_amount": "1.234.567,89",
                "line_items": [{"description": "Schrauben M8", "sku": "S-M8", "quantity": "1.000", "unit_price": "1.234,56"}],
            },
            workspace_currency="EUR", date_format="DD.MM.YYYY",
        )
        assert po.header.vendor_key == "vat:DE123456789"
        assert po.header.total_micros == 1_234_567_890_000, po.header.total_micros
        assert po.header.document_date.isoformat() == "2026-04-03"
        assert po.lines[0].unit_price_micros == 1_234_560_000, po.lines[0].unit_price_micros
        # The one that regressed: `1.000` units is a THOUSAND in Germany.
        # Reading it as 1 is a 1000x error on the axis that decides
        # QUANTITY_VARIANCE, and it points at the supplier.
        assert po.lines[0].quantity == Decimal("1000"), (
            f"EU quantity 1.000 read as {po.lines[0].quantity}; the currency "
            f"hint is not reaching nz.quantity()"
        )

    rec.check("EU grouping and DD.MM dates survive end to end, quantities included", eu_parsing_end_to_end)

    def self_inconsistency_precedes_comparison() -> None:
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00", "amount": "100.00"}], total_amount="100.00")
        bad = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00", "amount": "100.00"}], total_amount="9500.00")
        result = M.match(po=po, invoice=bad, policy=P.TolerancePolicy(policy_version="t:1"))
        codes = [(f.get("side"), f.get("code")) for f in result.header_findings]
        assert ("invoice", "SELF_INCONSISTENT_TOTAL") in codes, (
            f"an invoice whose lines do not sum to its own total was not "
            f"flagged: {codes}. Comparing either of two disagreeing numbers "
            f"against a purchase order yields a precise, confident, wrong "
            f"variance — worse than no answer, because a reviewer can act on it."
        )
        assert ("po", "SELF_INCONSISTENT_TOTAL") not in codes, (
            "the consistent document must not be flagged"
        )

    rec.check("a self-inconsistent invoice is flagged before cross-document comparison", self_inconsistency_precedes_comparison)

    def two_way_case_does_not_invent_receipts() -> None:
        # No goods receipt document at all. Marking every line NOT_RECEIVED
        # would turn "this tenant does not use goods receipts" into an
        # estate-wide exception backlog.
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])
        invoice = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])
        result = M.match(po=po, invoice=invoice, policy=P.TolerancePolicy(policy_version="t:1"))
        assert result.lines[0].outcome == M.OUTCOME_MATCHED, result.lines[0].outcome

    rec.check("a two-way case does not report NOT_RECEIVED for an absent receipt", two_way_case_does_not_invent_receipts)

    def quantity_prefers_the_receipt() -> None:
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "100", "rate": "10.00"}])
        grn = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "90", "rate": "10.00"}])
        invoice = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "100", "rate": "10.00"}])
        result = M.match(po=po, receipt=grn, invoice=invoice, policy=P.TolerancePolicy(policy_version="t:1"))
        line = result.lines[0]
        assert line.outcome == "QUANTITY_VARIANCE", line.outcome
        assert line.quantity_delta == Decimal("10"), line.quantity_delta
        finding = next(f for f in line.findings if f["code"] == "QUANTITY_VARIANCE")
        assert finding["compared_against"] == "receipt", (
            "ordering a hundred and being invoiced for a hundred is not "
            "evidence that a hundred arrived; the receipt must win"
        )

    rec.check("invoiced quantity is compared against the receipt, not the order", quantity_prefers_the_receipt)

    def outcome_precedence_is_total() -> None:
        import app.services.procurement_matching.vocabulary as V  # noqa: F401
        assert set(M.OUTCOME_PRECEDENCE) == set(mods["vocabulary"].LINE_OUTCOMES), (
            "every outcome must appear in the precedence order, or a line "
            "that is only that outcome falls through to MATCHED"
        )
        assert M.OUTCOME_PRECEDENCE[-1] == M.OUTCOME_MATCHED, (
            "MATCHED must be last: it is the fallthrough, not a competitor"
        )

    rec.check("outcome precedence covers every outcome and ends at MATCHED", outcome_precedence_is_total)

    def determinism_across_25_runs() -> None:
        pol = P.TolerancePolicy(policy_version="t:1", price_tolerance_bps=100, max_pair_cost=600_000)
        po = _doc(LE, [
            {"description": f"Item {i} assembly bracket", "sku": f"SKU{i}", "qty": str(i + 1), "rate": f"{100 + i}.00"}
            for i in range(12)
        ])
        # Emitted in the opposite order on purpose: a matcher whose answer
        # depends on extractor ordering is not deterministic in the way the
        # digest claims.
        invoice = _doc(LE, [
            {"description": f"item {i} assy bracket", "sku": f"SKU{i}", "qty": str(i + 1), "rate": f"{100 + i}.50"}
            for i in range(11, -1, -1)
        ])

        signatures, digests = set(), set()
        for _ in range(25):
            result = M.match(po=po, invoice=invoice, policy=pol)
            signatures.add(
                tuple(
                    (line.line_number, line.outcome, line.sku, line.pair_cost)
                    for line in result.lines
                )
            )
            digests.add(
                D.input_digest(po=po, invoice=invoice, policy=pol, backend_id="lexical-v1")
            )
        assert len(signatures) == 1, f"matcher produced {len(signatures)} distinct results over 25 runs"
        assert len(digests) == 1, f"digest produced {len(digests)} distinct values over 25 runs"

    rec.check("matcher output and digest are identical across 25 runs", determinism_across_25_runs)

    def digest_covers_everything_that_changes_the_answer() -> None:
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])
        invoice = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "101.00"}])
        base = P.TolerancePolicy(policy_version="a:1")

        digest_base = D.input_digest(po=po, invoice=invoice, policy=base, backend_id="lexical-v1")
        assert len(digest_base) == 64 and all(c in "0123456789abcdef" for c in digest_base), (
            "the digest must satisfy ck_procurement_cases_digest_shape"
        )

        variants = {
            "policy version": (P.TolerancePolicy(policy_version="a:2"), "lexical-v1"),
            "price tolerance": (P.TolerancePolicy(policy_version="a:1", price_tolerance_micros=5), "lexical-v1"),
            "quantity tolerance": (P.TolerancePolicy(policy_version="a:1", quantity_tolerance=Decimal("1")), "lexical-v1"),
            "max_pair_cost": (P.TolerancePolicy(policy_version="a:1", max_pair_cost=500_000), "lexical-v1"),
            "similarity backend": (base, "st-all-minilm-l6-v2"),
        }
        for label, (pol, backend_id) in variants.items():
            other = D.input_digest(po=po, invoice=invoice, policy=pol, backend_id=backend_id)
            assert other != digest_base, (
                f"changing the {label} did not change the digest. A digest "
                f"that omits it lets that change silently reuse a stale case, "
                f"so a tenant who tightens the setting sees no new exceptions "
                f"and concludes the setting does nothing."
            )

        # And the reflexive half: every field of the policy must be covered.
        fields = set(base.as_digest_input())
        declared = {f.name for f in __import__("dataclasses").fields(base)}
        assert declared <= fields, (
            f"policy fields missing from the digest input: {sorted(declared - fields)}"
        )

    rec.check("digest moves on policy version, every tolerance, and the backend", digest_covers_everything_that_changes_the_answer)

    def digest_ignores_what_cannot_change_the_answer() -> None:
        po = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])
        invoice = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])
        pol = P.TolerancePolicy(policy_version="a:1")
        first = D.input_digest(po=po, invoice=invoice, policy=pol, backend_id="lexical-v1")
        # Same documents, re-extracted, lines emitted in a different order.
        reordered = _doc(LE, [{"description": "Widget", "sku": "W1", "qty": "1", "rate": "100.00"}])
        second = D.input_digest(po=po, invoice=reordered, policy=pol, backend_id="lexical-v1")
        assert first == second, (
            "an identical re-extraction produced a different digest; every "
            "sweep would supersede the case it just wrote"
        )

    rec.check("digest is stable across an identical re-extraction", digest_ignores_what_cannot_change_the_answer)

    def similarity_is_quantised_and_bounded() -> None:
        backend = S.LexicalBackend()
        assert backend.similarity("Hex Bolt M8", "Hex Bolt M8") == Decimal("1.000000")
        assert backend.similarity("", "anything") == Decimal("0.000000")
        value = backend.similarity("Hex Bolt M8 Zinc Plated", "HEX BOLT M8 ZP")
        assert Decimal(0) <= value <= Decimal(1)
        assert -value.as_tuple().exponent == S.SIMILARITY_PLACES, (
            "an un-quantised float here is why a 25-run determinism gate "
            "would fail intermittently rather than never"
        )
        # Corpus independence: the same pair must score the same regardless
        # of what else was in the batch. An IDF term would break this.
        assert backend.similarity("a b c", "a b d") == backend.similarity("a b c", "a b d")

    rec.check("lexical similarity is bounded, quantised and corpus-independent", similarity_is_quantised_and_bounded)

    def heavy_backend_refuses_on_light() -> None:
        source = _read(BACKEND / "app/services/procurement_matching/similarity.py")
        assert "_assert_heavy_permitted()" in source, (
            "the SentenceTransformers backend must refuse to construct on a "
            "profile that forbids it. Importing it puts the module in "
            "sys.modules and the next assert_imports_match_profile() call "
            "takes the worker down."
        )
        constructor = source[source.index("def __init__(self, model_name"):]
        constructor = constructor[: constructor.index("def similarity")]
        assert constructor.index("_assert_heavy_permitted()") < constructor.index(
            "from sentence_transformers import"
        ), "the profile check must run BEFORE the import, not after"

    rec.check("SentenceTransformers backend checks the profile before importing", heavy_backend_refuses_on_light)

    def no_second_parser() -> None:
        source = _read(BACKEND / "app/services/procurement_matching/line_extraction.py")
        for banned in ("float(", "Decimal(text", "re.sub(r\"[,]\"", ".replace(\",\", \"\")"):
            assert banned not in source, (
                f"line_extraction.py contains {banned!r}, which looks like a "
                f"second number parser. Every number must reach the matcher "
                f"through app/core/normalize.py or a PO and an invoice can "
                f"read the same string differently."
            )
        assert "nz.money_micros" in source and "nz.quantity" in source

    rec.check("line_extraction parses no numbers of its own", no_second_parser)


# ===========================================================================
# Step 3 — wiring, registration and refusal shapes
# ===========================================================================


def gates_wiring(rec: Recorder) -> None:
    handlers = _read(BACKEND / "app/workers/handlers/__init__.py")
    profiles = _read(BACKEND / "app/workers/profiles.py")
    scheduler = _read(BACKEND / "app/workers/scheduler.py")
    routes = _read(BACKEND / "app/api/v1/procurement.py")
    router = _read(BACKEND / "app/api/v1/router.py")
    gate = _read(BACKEND / "app/api/capability_gate.py")
    service = _read(BACKEND / "app/services/procurement_matching/case_service.py")
    usage = _read(BACKEND / "app/core/usage_events.py")
    webhooks = _read(BACKEND / "app/core/webhook_events.py")

    def job_registered_in_all_three_places() -> None:
        assert '"procurement.score": _procurement_score' in handlers, (
            "procurement.score is not in the handler map"
        )
        assert "ARCH31_JOB_TYPES" in handlers and "| ARCH31_JOB_TYPES" in handlers, (
            "the phase constant must join ALL_PHASE_JOB_TYPES, which "
            "_assert_vocabulary_matches_registry() compares against the "
            "handler table at import"
        )
        assert '"procurement.score",' in profiles, (
            "procurement.score has a handler but no worker profile claims it. "
            "assert_imports_match_profile() raises ProfileError at EVERY "
            "worker's startup on an unclaimed handler, so this does not "
            "merely stall the queue -- it stops the entire fleet booting."
        )
        # And specifically on LIGHT, not OCR or ENRICH.
        light = profiles[profiles.index("LIGHT = WorkerProfile") : profiles.index("OCR = WorkerProfile")]
        assert '"procurement.score",' in light, "it must be on the LIGHT profile"
        assert 'job_type="procurement.score"' in scheduler, (
            "nothing schedules the re-score sweep; a goods receipt that "
            "arrives after its invoice would never re-open the case"
        )

    rec.check("procurement.score registered in handler map, LIGHT profile and scheduler", job_registered_in_all_three_places)

    def every_route_is_capability_gated() -> None:
        # Count route decorators and _gate calls. Reads as well as writes:
        # gating only the writes lets a tenant without the capability read
        # every variance the engine found, which is the product.
        decorators = routes.count("@router.")
        gated = routes.count("_gate(db, context,")
        assert decorators >= 9, f"expected at least 9 routes, found {decorators}"
        assert gated == decorators, (
            f"{decorators} routes but only {gated} capability checks. A read "
            f"route without the gate hands the product to a tenant who has "
            f"not bought it."
        )
        assert "capability_key=CAPABILITY" in routes
        assert "RECONCILIATION_CAPABILITY" in routes

    rec.check("every procurement route is capability-gated, reads included", every_route_is_capability_gated)

    def capability_gate_reads_the_right_attribute() -> None:
        # The defect this gate exists for: the first cut read `tier.limits`
        # and `tier.entitlement_rows`, neither of which exists on
        # quota_service._TierSnapshot. has_capability returned False for
        # every organization on every tier, and a 402 is the NORMAL response
        # for most customers -- so a paying Enterprise tenant being told to
        # upgrade looked exactly like correct behaviour.
        assert 'getattr(tier, "limits"' not in gate, (
            "_TierSnapshot has no `limits` attribute; reading it returns the "
            "default and refuses every tenant"
        )
        assert 'getattr(tier, "entitlement_rows"' not in gate, (
            "_TierSnapshot has no `entitlement_rows` attribute either"
        )
        assert 'limit_key' in gate and 'entries' in gate, (
            "the check must read tier.entries[].limit_key, the same reading "
            "entitlement_service.tier_grants uses"
        )

    rec.check("capability gate reads tier.entries, the attribute the snapshot has", capability_gate_reads_the_right_attribute)

    def approve_refuses_without_override_reason() -> None:
        assert "class OverrideReasonRequired" in service
        assert "if red_lines and not reason:" in service, (
            "the refusal must be conditional on red lines; requiring a "
            "reason on a clean case makes the reason meaningless"
        )
        assert "HTTP_422_UNPROCESSABLE_ENTITY" in routes
        assert '"code": "OVERRIDE_REASON_REQUIRED"' in routes, (
            "the console branches on this code to reopen the override dialog; "
            "a bare 422 with prose gives it nothing to branch on"
        )
        # The rule must be enforced in the service, not only the schema: a
        # Pydantic required field cannot know whether THIS case has red
        # lines, and a client driving the API directly would bypass the UI.
        schemas = _read(BACKEND / "app/schemas/procurement.py")
        assert "override_reason: Optional[str]" in schemas, (
            "override_reason must be optional in the schema -- whether it is "
            "required depends on database state the schema cannot see"
        )

    rec.check("approve refuses with a 422 OVERRIDE_REASON_REQUIRED when red lines exist", approve_refuses_without_override_reason)

    def dispute_reason_floor() -> None:
        assert "MINIMUM_DISPUTE_REASON = 10" in service
        schemas = _read(BACKEND / "app/schemas/procurement.py")
        assert "min_length=MINIMUM_DISPUTE_REASON" in schemas
        assert "_not_only_whitespace" in schemas, (
            "min_length alone accepts ten spaces"
        )

    rec.check("dispute requires ten characters of actual text", dispute_reason_floor)

    def metered_once_per_digest() -> None:
        assert 'USAGE_EVENT_PROCUREMENT_CASE = "procurement.case"' in service
        assert 'idempotency_key=f"{USAGE_EVENT_PROCUREMENT_CASE}:{digest}"' in service, (
            "metering must be keyed on the input_digest, not the case id. "
            "Keying on the case id bills once per row and therefore once per "
            "policy edit, which turns 'tighten your tolerance' into a charge."
        )
        assert 'name="procurement.case"' in usage, (
            "procurement.case is not in USAGE_EVENT_TYPES; record_usage "
            "raises UnknownUsageTypeError and every score fails"
        )

    rec.check("procurement.case is metered exactly once per input_digest", metered_once_per_digest)

    def events_registered_and_public() -> None:
        for event in ("procurement.completed", "procurement.approved", "procurement.disputed"):
            assert f'"{event}"' in webhooks, f"{event} is not a publishable event type"
        internal = _read(BACKEND / "app/core/automation_events.py")
        for event in ("procurement.completed", "procurement.approved", "procurement.disputed"):
            assert f'"{event}"' not in internal, (
                f"{event} is in BOTH vocabularies; _assert_vocabularies_disjoint "
                f"refuses to import"
            )

    rec.check("the three procurement events are PUBLIC and disjoint from the internal set", events_registered_and_public)

    def router_mounted() -> None:
        assert "procurement," in router, "the module is not imported"
        assert "api_router.include_router(procurement.router)" in router, (
            "the router is never mounted; every route 404s"
        )

    rec.check("the procurement router is imported and mounted", router_mounted)

    def rescore_never_edits_a_resolved_case() -> None:
        assert "CASE_STATUS_SUPERSEDED" in service
        assert "existing.status = CASE_STATUS_SUPERSEDED" in service, (
            "a re-score must supersede rather than update. Mutating an "
            "APPROVED case retro-fits a person's signature onto figures they "
            "never approved."
        )
        assert "if existing is not None and existing.input_digest == computed_digest:" in service, (
            "an unchanged digest must write nothing at all, or the "
            "five-minute sweep becomes a background write amplifier"
        )
        handler = _read(BACKEND / "app/workers/handlers/procurement.py")
        assert 'ProcurementCase.status.in_(["APPROVED", "DISPUTED"])' in handler, (
            "the sweep must exclude decided cases; a late goods receipt must "
            "not silently supersede a signature"
        )

    rec.check("a re-score supersedes and never edits, and the sweep skips decided cases", rescore_never_edits_a_resolved_case)


def _strip_ts_comments(source: str) -> str:
    """Remove // and /* */ comments before scanning source for banned text.

    Without this, both gates below fail on their own rationale: the
    ThreeWayComparison header explains that refusals are read "through
    ApiError's structured envelope, never error.response", and the lock card
    explains that an add-on "card opens checkout" while a capability's does
    not. A substring scan reads the explanation as the offence.

    verify_arch31_step0.py records the same trap from ARCH-30: a gate grepped
    for "price" and failed on the comment explaining why there is no price.
    Assert behaviour, and where only text is available, assert it against the
    text that actually executes.
    """
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            end = source.find("\n", index)
            index = length if end == -1 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end == -1 else end + 2
        else:
            out.append(source[index])
            index += 1
    return "".join(out)


def gates_frontend(rec: Recorder) -> None:
    root = ROOT / "frontend" / "src"

    def screens_exist_and_are_routed() -> None:
        for relpath in (
            "pages/procurement/CaseQueue.tsx",
            "pages/procurement/ThreeWayComparison.tsx",
            "pages/procurement/TolerancePolicyEditor.tsx",
            "components/procurement/CapabilityLockCard.tsx",
            "services/api/procurement.ts",
            "types/procurement.ts",
            "hooks/useCapabilityAccess.ts",
        ):
            assert (root / relpath).exists(), f"missing {relpath}"
        app = _read(root / "App.tsx")
        for element in ("ProcurementCaseQueue", "ThreeWayComparison", "TolerancePolicyEditor"):
            assert element in app, f"{element} is not routed in App.tsx"
        # Route order: "policies" must be declared before ":caseId" or
        # react-router matches it as a case id.
        assert app.index("workspaceProcurementPolicies") < app.index(
            "workspaceProcurementCase"
        ), (
            "the policies route must precede the :caseId route, or "
            "/procurement/policies renders the comparison grid against a "
            "case that does not exist"
        )

    rec.check("frontend: all three screens exist and are routed, policies before :caseId", screens_exist_and_are_routed)

    def errors_read_apierror_not_response() -> None:
        for relpath in (
            "pages/procurement/ThreeWayComparison.tsx",
            "pages/procurement/TolerancePolicyEditor.tsx",
        ):
            source = _strip_ts_comments(_read(root / relpath))
            assert "error.response" not in source and ".response?.data" not in source, (
                f"{relpath} reads error.response. The backend emits "
                f"{{code, message, details}} and ApiError parses it; reading "
                f"axios's own shape discards every domain message."
            )
            assert "ApiError" in source
        grid = _strip_ts_comments(_read(root / "pages/procurement/ThreeWayComparison.tsx"))
        assert 'error.is("OVERRIDE_REASON_REQUIRED")' in grid, (
            "the grid must branch on the refusal code to reopen the dialog"
        )

    rec.check("frontend: refusals read ApiError codes, never error.response", errors_read_apierror_not_response)

    def timestamps_and_keyboard() -> None:
        for relpath in ("pages/procurement/CaseQueue.tsx", "pages/procurement/ThreeWayComparison.tsx"):
            source = _read(root / relpath)
            assert "formatTimestamp" in source, f"{relpath} must format through displayTime"
            assert "toLocaleString()" not in source, (
                f"{relpath} formats a timestamp directly, bypassing the "
                f"workspace display preferences"
            )
        grid = _read(root / "pages/procurement/ThreeWayComparison.tsx")
        for key in ('case "j":', 'case "k":', 'case "e":', 'case "a":', 'case "d":'):
            assert key in grid, f"keyboard map is missing {key}"
        assert 'if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))' in grid, (
            "the keyboard handler must ignore typing in the reason box, or "
            "typing 'a' in an override reason approves the case"
        )

    rec.check("frontend: timestamps go through displayTime and the keyboard map is complete", timestamps_and_keyboard)

    def result_cells_carry_text_not_only_colour() -> None:
        types = _read(root / "types/procurement.ts")
        assert "outcomeLabel" in types and "outcomeTone" in types
        assert "const exhaustive: never = line.outcome;" in types, (
            "the label switch must be exhaustive, or a seventh outcome "
            "renders as a blank cell in production"
        )
        grid = _read(root / "pages/procurement/ThreeWayComparison.tsx")
        assert "outcomeLabel(line)" in grid, (
            "the result cell must render prose. This is a screen people "
            "authorise payments from; a reviewer with a colour-vision "
            "deficiency reading amber-vs-red cells has no information."
        )

    rec.check("frontend: result cells carry text as well as colour", result_cells_carry_text_not_only_colour)

    def lock_card_offers_no_purchase() -> None:
        card = _strip_ts_comments(_read(root / "components/procurement/CapabilityLockCard.tsx"))
        # Behavioural, not word-matching. The first cut banned the token
        # "purchase" and failed on the product copy "purchase order" -- the
        # card's own subject matter. What must be absent is the ACTION: a
        # checkout call and a button to fire it.
        for banned in ("createAddonCheckoutSession", "checkout", "Checkout"):
            assert banned not in card, (
                f"the lock card reaches for {banned!r}. A capability is "
                f"bundled into a tier and has no standalone price; a "
                f"checkout button sends the reader to a flow with nothing "
                f"to sell them."
            )
        assert "<button" not in card, (
            "the lock card renders a button. There is no self-serve action "
            "for a capability -- the remedy is a plan change, which is why "
            "capability_gate returns remedy=PLAN_UPGRADE with no price and "
            "no purchase URL."
        )
        assert "plan" in card.lower(), (
            "the card must name the remedy; 'you don't have this' with no "
            "next step is a dead end"
        )

    rec.check("frontend: the capability lock card offers a plan change, not a purchase", lock_card_offers_no_purchase)


def report_pending() -> None:
    """Nothing is pending. Kept as a named function so its absence is visible.

    Steps 3 and 4 activated all five gates this used to list. The function
    stays rather than being deleted: a reader diffing this file against the
    Steps 1-2 version should see the list emptied, not the reporting removed.
    """
    print("\n--- PENDING ---")
    print("  (none — ARCH-31 Steps 1-4 are all gated)")


# ===========================================================================
# Mutation
# ===========================================================================

MUTANTS: list[tuple[str, str, str, str, bool]] = [
    (
        "price tolerance boundary <= becomes <",
        "app/services/procurement_matching/policy.py",
        "    return delta <= price_allowance_micros(expected_micros, policy)",
        "    return delta < price_allowance_micros(expected_micros, policy)",
        True,
    ),
    (
        "quantity tolerance boundary <= becomes <",
        "app/services/procurement_matching/policy.py",
        "    return delta <= Decimal(policy.quantity_tolerance)",
        "    return delta < Decimal(policy.quantity_tolerance)",
        True,
    ),
    (
        "post-assignment rejection threshold removed",
        "app/services/procurement_matching/matcher.py",
        "        if cost > max_pair_cost:\n            continue",
        "        if False:\n            continue",
        True,
    ),
    (
        "digest omits the policy version",
        "app/services/procurement_matching/digest.py",
        '        "policy": policy.as_digest_input(),',
        '        "policy": {},',
        True,
    ),
    (
        "self-consistency check disabled",
        "app/services/procurement_matching/line_extraction.py",
        "    if abs(delta) > allowed:",
        "    if False:",
        True,
    ),
    (
        "EU quantity hint dropped",
        "app/services/procurement_matching/line_extraction.py",
        "        currency_hint=line_currency or workspace_currency,",
        "        currency_hint=None,",
        True,
    ),
    (
        # CONTROL. Prose inside a finding, which no gate asserts on and no
        # reader depends on. It MUST survive: a suite where everything dies
        # is usually a suite too broad to locate anything.
        "CONTROL: reword a finding's human-readable detail",
        "app/services/procurement_matching/matcher.py",
        '"detail": "ordered or received, but absent from the invoice",',
        '"detail": "on the order or the receipt, but not on the invoice",',
        False,
    ),
]


def run_mutants(rec: Recorder, tmp: Path, mods: dict[str, Any]) -> None:
    package = "app/services/procurement_matching"

    for name, relpath, old, new, must_die in MUTANTS:
        def make(name=name, relpath=relpath, old=old, new=new, must_die=must_die) -> None:
            safe_dir = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:40]
            workspace = tmp / safe_dir
            if workspace.exists():
                shutil.rmtree(workspace)
            shutil.copytree(BACKEND / package, workspace / package)

            target = workspace / relpath
            text = target.read_text(encoding="utf-8")
            assert old in text, f"mutant anchor missing; the gate proves nothing: {old!r}"
            target.write_text(text.replace(old, new, 1), encoding="utf-8")

            # Load the mutated package as a private module tree so the real
            # one is untouched for the next mutant.
            token = abs(hash(name))
            mutated: dict[str, Any] = {}
            for module_name in ("vocabulary", "similarity", "policy", "line_extraction", "digest", "matcher"):
                mutated[module_name] = _load(
                    workspace / package / f"{module_name}.py", f"_mut{token}_{module_name}"
                )
            # Rebind the sibling references the mutated matcher resolves at
            # import time, so a mutation in policy.py is actually exercised.
            mutated["matcher"].policy_module = mutated["policy"]
            mutated["matcher"].line_extraction = mutated["line_extraction"]

            sub = Recorder()
            gates_matcher(sub, mutated)

            if must_die:
                assert sub.failed > 0, f"mutant survived every gate: {name}"
            else:
                failures = [n for n, ok, _ in sub.results if not ok]
                assert sub.failed == 0, (
                    f"CONTROL mutant was killed by {failures}. The gates are "
                    f"reacting to a change no reader depends on, which means "
                    f"they are too broad to locate a real defect."
                )

        rec.check(
            f"mutant {'dies' if must_die else 'survives'}: {name}",
            make,
        )


# ===========================================================================
# --db
# ===========================================================================


def gates_db(rec: Recorder, database_url: str) -> None:
    from sqlalchemy import create_engine, text as sa_text
    from sqlalchemy.exc import DatabaseError, IntegrityError

    engine = create_engine(database_url, future=True)

    def tables_exist() -> None:
        with engine.connect() as conn:
            for table in (
                "procurement_cases",
                "procurement_case_lines",
                "procurement_tolerance_policies",
            ):
                found = conn.execute(
                    sa_text(f"SELECT to_regclass('public.{table}')")
                ).scalar_one_or_none()
                assert found, f"{table} does not exist; run alembic upgrade head"

    rec.check("DB: the three procurement tables exist", tables_exist)

    def single_alembic_head() -> None:
        with engine.connect() as conn:
            heads = [
                row[0]
                for row in conn.execute(sa_text("SELECT version_num FROM alembic_version"))
            ]
        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"]), heads

    rec.check("DB: exactly one Alembic head, at Step 1", single_alembic_head)

    def audit_vocabulary_present() -> None:
        with engine.connect() as conn:
            values = {
                row[0]
                for row in conn.execute(
                    sa_text(
                        "SELECT unnest(enum_range(NULL::audit_action))::text"
                    )
                )
            }
            for action in ("MATCH_APPROVED", "MATCH_DISPUTED", "MATCH_RESCORED", "TOLERANCE_PUBLISHED"):
                assert action in values, f"audit_action is missing {action}"
            resources = {
                row[0]
                for row in conn.execute(
                    sa_text("SELECT unnest(enum_range(NULL::audit_resource_type))::text")
                )
            }
            for resource in ("PROCUREMENT_CASE", "PROCUREMENT_TOLERANCE_POLICY"):
                assert resource in resources, f"audit_resource_type is missing {resource}"

    rec.check("DB: the ARCH-31 audit vocabulary was added", audit_vocabulary_present)

    def _seed(conn: Any) -> tuple[Any, Any, Any]:
        org = conn.execute(sa_text("SELECT id FROM organizations LIMIT 1")).scalar_one_or_none()
        ws = conn.execute(sa_text("SELECT id FROM workspaces LIMIT 1")).scalar_one_or_none()
        wi = list(conn.execute(sa_text("SELECT id FROM work_items LIMIT 2")).scalars().all())
        if org and ws and len(wi) < 2:
            import uuid
            while len(wi) < 2:
                new_id = uuid.uuid4()
                conn.execute(
                    sa_text(
                        "INSERT INTO work_items (id, workspace_id, original_filename, stored_filename, file_type, file_size, status, pipeline_stage, created_at, updated_at) "
                        "SELECT :new_id, workspace_id, 'second_item.pdf', 'second_item.pdf', file_type, file_size, status, pipeline_stage, now(), now() "
                        "FROM work_items LIMIT 1"
                    ),
                    {"new_id": str(new_id)},
                )
                wi.append(new_id)
        if not (org and ws and len(wi) >= 2):
            raise AssertionError(
                "this gate needs one organization, one workspace and two work "
                "items to exist; run the development seed"
            )
        return org, ws, wi

    def published_policy_is_immutable() -> None:
        with engine.begin() as conn:
            org, ws, _ = _seed(conn)
            savepoint = conn.begin_nested()
            policy_id = conn.execute(
                sa_text(
                    "INSERT INTO procurement_tolerance_policies "
                    "(organization_id, workspace_id, version, status, published_at) "
                    "VALUES (:org, :ws, 9001, 'PUBLISHED', now()) RETURNING id"
                ),
                {"org": str(org), "ws": str(ws)},
            ).scalar_one()

            for label, statement in (
                ("UPDATE", "UPDATE procurement_tolerance_policies SET price_tolerance_bps = 500 WHERE id = :id"),
                ("DELETE", "DELETE FROM procurement_tolerance_policies WHERE id = :id"),
            ):
                inner = conn.begin_nested()
                try:
                    conn.execute(sa_text(statement), {"id": str(policy_id)})
                except (DatabaseError, IntegrityError):
                    inner.rollback()
                    continue
                inner.rollback()
                raise AssertionError(
                    f"the database accepted an {label} on a PUBLISHED policy. "
                    f"Every case stamped with that policy_version was scored "
                    f"under those numbers; changing them silently "
                    f"re-interprets every one of them."
                )
            savepoint.rollback()

    rec.check("DB: a PUBLISHED tolerance policy refuses UPDATE and DELETE", published_policy_is_immutable)

    def draft_stays_editable() -> None:
        with engine.begin() as conn:
            org, ws, _ = _seed(conn)
            savepoint = conn.begin_nested()
            # The workspace may already hold a draft; the partial unique
            # index permits only one, so clear the field inside the savepoint.
            conn.execute(
                sa_text(
                    "DELETE FROM procurement_tolerance_policies "
                    "WHERE workspace_id = :ws AND status = 'DRAFT'"
                ),
                {"ws": str(ws)},
            )
            policy_id = conn.execute(
                sa_text(
                    "INSERT INTO procurement_tolerance_policies "
                    "(organization_id, workspace_id, version, status) "
                    "VALUES (:org, :ws, 9002, 'DRAFT') RETURNING id"
                ),
                {"org": str(org), "ws": str(ws)},
            ).scalar_one()
            conn.execute(
                sa_text(
                    "UPDATE procurement_tolerance_policies "
                    "SET price_tolerance_bps = 250 WHERE id = :id"
                ),
                {"id": str(policy_id)},
            )
            conn.execute(
                sa_text(
                    "UPDATE procurement_tolerance_policies "
                    "SET status = 'PUBLISHED', published_at = now() WHERE id = :id"
                ),
                {"id": str(policy_id)},
            )
            savepoint.rollback()

    rec.check("DB: a DRAFT policy is editable and can be published exactly once", draft_stays_editable)

    def case_refuses_bad_rows() -> None:
        digest = "a" * 64
        with engine.begin() as conn:
            org, ws, items = _seed(conn)
            bad_rows = (
                (
                    "only one document",
                    "INSERT INTO procurement_cases (organization_id, workspace_id, "
                    "invoice_work_item_id, input_digest, policy_version) "
                    "VALUES (:org, :ws, :a, :digest, 'default:1')",
                ),
                (
                    "digest that is not a sha256",
                    "INSERT INTO procurement_cases (organization_id, workspace_id, "
                    "po_work_item_id, invoice_work_item_id, input_digest, policy_version) "
                    "VALUES (:org, :ws, :b, :a, 'nope', 'default:1')",
                ),
                (
                    "unknown status",
                    "INSERT INTO procurement_cases (organization_id, workspace_id, "
                    "po_work_item_id, invoice_work_item_id, input_digest, policy_version, status) "
                    "VALUES (:org, :ws, :b, :a, :digest, 'default:1', 'WAT')",
                ),
                (
                    "APPROVED with no resolved_at",
                    "INSERT INTO procurement_cases (organization_id, workspace_id, "
                    "po_work_item_id, invoice_work_item_id, input_digest, policy_version, status) "
                    "VALUES (:org, :ws, :b, :a, :digest, 'default:1', 'APPROVED')",
                ),
            )
            params = {"org": str(org), "ws": str(ws), "a": str(items[0]), "b": str(items[1]), "digest": digest}
            for label, statement in bad_rows:
                inner = conn.begin_nested()
                try:
                    conn.execute(sa_text(statement), params)
                except (IntegrityError, DatabaseError):
                    inner.rollback()
                    continue
                inner.rollback()
                raise AssertionError(f"the database accepted a case it must refuse: {label}")

    rec.check("DB: procurement_cases refuses one-sided, mis-shaped and unresolved rows", case_refuses_bad_rows)

    def one_live_case_per_invoice() -> None:
        digest = "b" * 64
        with engine.begin() as conn:
            org, ws, items = _seed(conn)
            savepoint = conn.begin_nested()
            params = {
                "org": str(org), "ws": str(ws),
                "a": str(items[0]), "b": str(items[1]), "digest": digest,
            }
            insert = (
                "INSERT INTO procurement_cases (organization_id, workspace_id, "
                "po_work_item_id, invoice_work_item_id, input_digest, "
                "policy_version, status) VALUES (:org, :ws, :b, :a, :digest, "
                "'default:1', :status)"
            )
            conn.execute(sa_text(insert), {**params, "status": "OPEN"})

            inner = conn.begin_nested()
            try:
                conn.execute(sa_text(insert), {**params, "status": "OPEN"})
            except IntegrityError:
                inner.rollback()
            else:
                inner.rollback()
                savepoint.rollback()
                raise AssertionError(
                    "two live cases were accepted for one invoice; the queue "
                    "would show the same invoice twice with different verdicts"
                )

            # ...but superseding the first must let the replacement in, which
            # is how a re-score under a new policy retires the old case.
            conn.execute(
                sa_text(
                    "UPDATE procurement_cases SET status = 'SUPERSEDED' "
                    "WHERE workspace_id = :ws AND invoice_work_item_id = :a"
                ),
                {"ws": str(ws), "a": str(items[0])},
            )
            conn.execute(sa_text(insert), {**params, "status": "OPEN"})
            savepoint.rollback()

    rec.check("DB: one live case per invoice, and SUPERSEDED frees the slot", one_live_case_per_invoice)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-31 Steps 1-2 verification — schema, matcher, digest, tolerances")
    print(f"  root: {ROOT}")

    overall = 0
    offline = Recorder()
    package = BACKEND / "app/services/procurement_matching"
    try:
        mods = {
            name: _load(package / f"{name}.py", f"_a31_{name}")
            for name in ("vocabulary", "similarity", "policy", "line_extraction", "digest", "matcher")
        }
        gates_schema(offline)
        gates_matcher(offline, mods)
        gates_wiring(offline)
        gates_frontend(offline)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    offline.report("OFFLINE")
    overall = max(overall, 1 if offline.failed else 0)

    if args.mutate:
        import tempfile

        mutate = Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            run_mutants(mutate, Path(tmp), mods)
        mutate.report("MUTATION")
        overall = max(overall, 1 if mutate.failed else 0)

    if args.db:
        if not args.database_url:
            print("\n--db requires --database-url or DATABASE_URL.", file=sys.stderr)
            return 2
        db_rec = Recorder()
        try:
            gates_db(db_rec, args.database_url)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 2
        db_rec.report("DATABASE")
        overall = max(overall, 1 if db_rec.failed else 0)

    report_pending()
    print("\n" + ("ALL SELECTED GATES PASSED" if overall == 0 else "GATES FAILED"))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())