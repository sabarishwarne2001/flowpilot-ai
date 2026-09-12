#!/usr/bin/env python3
"""ARCH-31 Step 0 verification — normalization (A2), document roles, capability gate.

Everything here is offline by design. Step 0 introduces no request path and no
job; it introduces pure functions, one table and one entitlement key. That is
deliberate sequencing: the matcher in the next milestone will hash these
functions' outputs into `input_digest`, so they have to be pinned by gates
before anything depends on them.

    python verify_arch31_step0.py
    python verify_arch31_step0.py --mutate
    python verify_arch31_step0.py --db --database-url postgresql+psycopg://...

EXIT 0 pass | 1 a gate failed | 2 harness could not run
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import re
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
# Normalization (ARCH-30 A2)
# ===========================================================================


def gates_normalize(rec: Recorder, nz: Any) -> None:
    def vendor_tax_id_precedence() -> None:
        # A tax id is a registration; a name is a rendering choice. Two
        # documents naming the supplier differently must still be one vendor
        # when both carry the same GSTIN, or every invoice from them opens as
        # NOT_ORDERED.
        assert nz.vendor_key("Acme Pvt Ltd", "29AABCU9603R1ZM") == \
               nz.vendor_key("ACME INDIA", "29aabcu9603r1zm")
        assert nz.vendor_key("x", "DE123456789") == "vat:DE123456789"
        assert nz.vendor_key("x", "12-3456789") == "ein:123456789"
        # Precedence is scheme-aware: a GSTIN is not filed under "taxid".
        assert nz.vendor_key("x", "29AABCU9603R1ZM").startswith("gstin:")

    rec.check("A2 vendor_key: tax id beats name, scheme recognised", vendor_tax_id_precedence)

    def vendor_name_fallback() -> None:
        for variant in ("Acme Pvt Ltd", "ACME PRIVATE LIMITED", "Acme, LLC.", "Acme Inc"):
            assert nz.vendor_key(variant) == "name:acme", variant
        assert nz.vendor_key("Café Foods Ltd") == "name:cafe foods"
        assert nz.vendor_key("Müller GmbH") == "name:muller"

    rec.check("A2 vendor_key: legal suffixes and accents folded", vendor_name_fallback)

    def vendor_never_empty() -> None:
        for bad in (None, "", "   ", "Ltd"):
            try:
                nz.vendor_key(bad)
            except nz.NormalizationError:
                continue
            raise AssertionError(
                f"{bad!r} produced a key. An empty vendor key collides with "
                f"every other empty key and merges unrelated suppliers."
            )

    rec.check("A2 vendor_key refuses to produce an empty key", vendor_never_empty)

    def document_numbers() -> None:
        cases = {
            "INV-0004512": "INV-4512",
            "inv 4512": "INV-4512",
            "# inv 4512": "INV-4512",
            "Invoice No. 4512": "4512",
            "INV-2026-0001": "INV-2026-1",
            "po 000123": "PO-123",
            "Ref: ABC/007": "ABC-7",
            "  INV/0042  ": "INV-42",
        }
        for raw, want in cases.items():
            got = nz.document_number(raw)
            assert got == want, f"{raw!r} -> {got!r}, want {want!r}"

    rec.check("A2 document_number: labels stripped, supplier codes kept, zeros dropped", document_numbers)

    def codes_are_not_labels() -> None:
        # The one that must never regress: stripping INV/PO as labels would
        # collapse INV-4512 and PO-4512 onto the same key, which is the worst
        # outcome a matcher can produce.
        assert nz.document_number("INV-4512") != nz.document_number("PO-4512")

    rec.check("A2 document_number keeps INV and PO distinct", codes_are_not_labels)

    def sku_is_aggressive() -> None:
        assert nz.sku("abc-123") == nz.sku("ABC 123") == nz.sku("abc123") == "ABC123"

    rec.check("A2 sku collapses every separator", sku_is_aggressive)

    def money_groupings() -> None:
        cases = [
            ("1,23,456.78", None, 123456780000),      # Indian
            ("1.234.567,89", None, 1234567890000),    # EU
            ("1,234,567.89", None, 1234567890000),    # Western
            ("1234.56", None, 1234560000),
            ("(1,234.56)", None, -1234560000),        # accounting negative
            ("1,234.56-", None, -1234560000),         # SAP trailing sign
            ("\u20b9 1,23,456", None, 123456000000),
            ("Rs. 1,23,456", None, 123456000000),
            ("$1,234.56", None, 1234560000),
        ]
        for raw, hint, want in cases:
            got = nz.money_micros(raw, workspace_currency=hint).micros
            assert got == want, f"{raw!r} -> {got}, want {want}"

    rec.check("A2 money_micros parses Indian, EU and Western groupings", money_groupings)

    def money_currency_detection() -> None:
        assert nz.money_micros("\u20b9 100").currency == "INR"
        assert nz.money_micros("Rs. 100").currency == "INR"
        assert nz.money_micros("$100").currency == "USD"
        assert nz.money_micros("\u20ac100").currency == "EUR"
        # The document beats the workspace. The setting is a default, the
        # page is evidence.
        assert nz.money_micros("$1,234.50", workspace_currency="INR").currency == "USD"

    rec.check("A2 money currency comes from the page before the workspace", money_currency_detection)

    def ambiguous_money_refuses_then_resolves() -> None:
        try:
            nz.money_micros("1,234")
            raise AssertionError(
                "'1,234' is 1234 in India and 1.234 in Germany. Parsing it "
                "without a currency silently produces a 1000x error that "
                "surfaces downstream as a price variance a human is asked to "
                "approve."
            )
        except nz.AmbiguousValue:
            pass
        # ARCH-30 A2: workspaces.currency is the resolver, and its first reader.
        assert nz.money_micros("1,234", workspace_currency="INR").micros == 1234000000
        assert nz.money_micros("1,234", workspace_currency="EUR").micros == 1234000
        assert nz.money_micros("1.234", workspace_currency="EUR").micros == 1234000000
        assert nz.money_micros("1.234", workspace_currency="USD").micros == 1234000
        assert nz.money_micros("1,234", workspace_currency="INR").currency_from_workspace

    rec.check("A2 ambiguous amounts refuse, then resolve from workspaces.currency", ambiguous_money_refuses_then_resolves)

    def dates_resolve_from_workspace() -> None:
        assert nz.parse_document_date("03/04/2026", format_hint="DD/MM/YYYY") == dt.date(2026, 4, 3)
        assert nz.parse_document_date("03/04/2026", format_hint="MM/DD/YYYY") == dt.date(2026, 3, 4)
        # Evidence beats configuration: 25 cannot be a month.
        assert nz.parse_document_date("25/04/2026", format_hint="MM/DD/YYYY") == dt.date(2026, 4, 25)
        assert nz.parse_document_date("2026-04-03", format_hint="MM/DD/YYYY") == dt.date(2026, 4, 3)
        assert nz.parse_document_date("3 April 2026") == dt.date(2026, 4, 3)
        assert nz.parse_document_date("April 3, 2026") == dt.date(2026, 4, 3)

    rec.check("A2 parse_document_date resolves from workspaces.date_format", dates_resolve_from_workspace)

    def ambiguous_date_refuses() -> None:
        try:
            nz.parse_document_date("03/04/2026")
        except nz.AmbiguousValue:
            return
        raise AssertionError(
            "03/04/2026 must refuse without a hint. Defaulting to either "
            "convention is wrong for roughly half of all customers and "
            "silent for all of them; on a three-way match it decides whether "
            "a goods receipt precedes its invoice."
        )

    rec.check("A2 an ambiguous date refuses rather than guessing", ambiguous_date_refuses)

    def quantities() -> None:
        q = nz.quantity("12 pcs")
        assert (q.value, q.unit) == (Decimal("12"), "PCS")
        q = nz.quantity("3.5 kg")
        assert (q.value, q.unit) == (Decimal("3.5"), "KG")
        q = nz.quantity("2 boxes of 10")
        assert (q.value, q.pack_size, q.total) == (Decimal(2), Decimal(10), Decimal(20))
        assert nz.quantity("1,200").value == Decimal("1200")

    rec.check("A2 quantity extracts units and pack sizes", quantities)

    def durations() -> None:
        assert nz.duration_days("Net 30") == 30
        assert nz.duration_days("NET-45") == 45
        assert nz.duration_days("payable within 45 days") == 45
        assert nz.duration_days("2 weeks") == 14
        assert nz.duration_days("Due on receipt") == 0
        assert nz.duration_days("thank you for your business") is None

    rec.check("A2 duration_days reads payment terms, None when absent", durations)

    def determinism() -> None:
        samples = ["1,23,456.78", "\u20b9 99", "1.234.567,89"]
        for sample in samples:
            answers = {nz.money_micros(sample).micros for _ in range(30)}
            assert len(answers) == 1, f"{sample!r} non-deterministic: {answers}"
        keys = {nz.vendor_key("Acme Pvt Ltd") for _ in range(30)}
        assert len(keys) == 1

    rec.check("A2 normalization is deterministic (input_digest depends on it)", determinism)


# ===========================================================================
# Document roles
# ===========================================================================


def gates_roles(rec: Recorder, rc: Any) -> None:
    SAMPLES = {
        "INVOICE": "TAX INVOICE\nInvoice No. 4512\nAmount Due 1,200",
        "PURCHASE_ORDER": "PURCHASE ORDER\nPO Number 88\nShip to: Warehouse",
        "GOODS_RECEIPT": "GOODS RECEIPT NOTE\nGRN 771\nQuantity received: 40",
        "CREDIT_NOTE": "CREDIT NOTE\nAgainst Invoice 4512\nAmount due -200",
        "STATEMENT": "STATEMENT OF ACCOUNT\nOpening balance 10,000",
        "CONTRACT": "MASTER SERVICES AGREEMENT\nThis Agreement is made\nGoverning law",
    }

    def classifies_each_role() -> None:
        for want, text in SAMPLES.items():
            got = rc.classify(text)
            assert got.role == want, f"{want}: classified as {got.role} ({got.confidence})"

    rec.check("Step 0 classifier identifies all six document roles", classifies_each_role)

    def credit_note_beats_invoice() -> None:
        # A credit note says "invoice" too. Read as an invoice, it becomes a
        # payment made instead of a payment reversed.
        verdict = rc.classify(SAMPLES["CREDIT_NOTE"])
        assert verdict.role == "CREDIT_NOTE", verdict.role

    rec.check("Step 0 a credit note is never read as an invoice", credit_note_beats_invoice)

    def unknown_is_other_not_a_guess() -> None:
        for text in ("", "   ", "Hello, thanks for your business."):
            assert rc.classify(text).role == "OTHER", text

    rec.check("Step 0 unrecognised documents are OTHER, not a weak guess", unknown_is_other_not_a_guess)

    def user_role_is_never_overwritten() -> None:
        try:
            rc.assert_not_user_locked("USER", work_item_id="wi-1")
        except rc.UserRoleLocked:
            rc.assert_not_user_locked("CLASSIFIER", work_item_id="wi-1")
            rc.assert_not_user_locked(None, work_item_id="wi-1")
            rc.assert_not_user_locked("RULE", work_item_id="wi-1")
            return
        raise AssertionError(
            "a USER role must lock. A human correcting the classifier is the "
            "most valuable signal in the system; overwriting it teaches them "
            "that correcting it is pointless."
        )

    rec.check("Step 0 role_source=USER is never overwritten", user_role_is_never_overwritten)

    def classification_is_deterministic() -> None:
        for text in SAMPLES.values():
            verdicts = {rc.classify(text).role for _ in range(25)}
            assert len(verdicts) == 1, verdicts

    rec.check("Step 0 classification is deterministic", classification_is_deterministic)

    def evidence_spans_are_real() -> None:
        text = SAMPLES["GOODS_RECEIPT"]
        verdict = rc.classify(text)
        assert verdict.signals, "a verdict with no signals cannot be explained to a reviewer"
        for signal in verdict.signals:
            assert text[signal.char_start:signal.char_end].lower() == signal.phrase.lower()

    rec.check("Step 0 every signal points at the span that produced it", evidence_spans_are_real)

    def no_model_import() -> None:
        source = _read(BACKEND / "app/services/procurement_matching/role_classifier.py")
        for forbidden in ("sentence_transformers", "SentenceTransformer", "torch"):
            assert forbidden not in source, (
                f"role_classifier imports {forbidden}. Roles feed "
                f"input_digest; a role that depends on a model file is "
                f"reproducible only while that file is byte-identical, and "
                f"'the match changed because we upgraded the model' is not "
                f"an explanation a supplier dispute survives."
            )

    rec.check("Step 0 the classifier stays deterministic (no model import)", no_model_import)


# ===========================================================================
# Migration and capability
# ===========================================================================


def gates_schema_and_capability(rec: Recorder) -> None:
    migration = _read(BACKEND / "alembic/versions/arch31_step0_document_roles.py")
    entitlements_src = _read(BACKEND / "app/core/entitlements.py")
    errors = _read(BACKEND / "app/core/billing_errors.py")
    gate = _read(BACKEND / "app/api/capability_gate.py")

    def migration_chains_and_is_expand() -> None:
        assert 'revision = "arch31_step0_document_roles"' in migration
        assert 'down_revision = "arch30_step3_workspace_clock"' in migration
        for altering in ("op.alter_column", "op.drop_column", "op.drop_table"):
            assert altering not in migration.split("def downgrade")[0], (
                f"upgrade() calls {altering}; Step 0 must be EXPAND-shaped so "
                f"it is safe to run before the code that reads it ships"
            )

    rec.check("Step 0 migration chains onto Tranche 4 and only adds", migration_chains_and_is_expand)

    def single_alembic_head() -> None:
        versions = BACKEND / "alembic/versions"
        revs: dict[str, str] = {}
        downs: set[str] = set()
        for path in versions.glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            rev = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)", text, re.M)
            down = re.search(r"^down_revision(?::[^=]*)?\s*=\s*['\"]([^'\"]+)", text, re.M)
            if rev:
                revs[rev.group(1)] = path.name
            if down:
                downs.add(down.group(1))
        heads = sorted(r for r in revs if r not in downs)
        assert heads == ["arch31_step0_document_roles"], heads

    rec.check("Step 0 leaves exactly one Alembic head", single_alembic_head)

    def role_constraints_present() -> None:
        for role in ("INVOICE", "PURCHASE_ORDER", "GOODS_RECEIPT", "CREDIT_NOTE",
                     "CONTRACT", "STATEMENT", "OTHER"):
            assert role in migration, f"{role} missing from the role CHECK"
        for source in ("CLASSIFIER", "USER", "RULE"):
            assert source in migration
        assert "ck_document_roles_user_has_no_confidence" in migration, (
            "a USER row must carry no confidence, or a rule can write itself "
            "a USER row with a score and beat a human on comparison"
        )
        assert "uq_document_roles_work_item" in migration, (
            "one role row per work item; two rows make 'did a human decide?' "
            "stop having a single answer"
        )

    rec.check("Step 0 document_roles CHECKs and uniqueness are declared", role_constraints_present)

    def isolation_columns_present() -> None:
        assert '"organization_id"' in migration and '"workspace_id"' in migration
        head = migration[migration.index("def upgrade"):migration.index("def downgrade")]
        assert head.count("nullable=False") >= 5, (
            "organization_id and workspace_id must both be NOT NULL (ARCH-02)"
        )

    rec.check("Step 0 document_roles carries both isolation columns", isolation_columns_present)

    def capability_registered_and_disjoint() -> None:
        assert 'RECONCILIATION_CAPABILITY: str = "capability.reconciliation"' in entitlements_src
        assert "CAPABILITY_KEYS" in entitlements_src
        assert "name=RECONCILIATION_CAPABILITY" in entitlements_src, (
            "the key must be registered as an Entitlement, not just declared"
        )
        # The disjointness that keeps boot working: entitlement_service
        # asserts its add-on catalog equals ADDON_KEYS, so a capability in
        # ADDON_KEYS fails at import.
        addon_block = entitlements_src[entitlements_src.index("ADDON_KEYS: tuple"):]
        addon_block = addon_block[:addon_block.index("\n\n")]
        assert "RECONCILIATION_CAPABILITY" not in addon_block, (
            "capability.reconciliation is in ADDON_KEYS. entitlement_service "
            "asserts that set equals its priced add-on catalog at import; a "
            "capability with no price fails that assertion at boot."
        )

    rec.check("Step 0 capability.reconciliation registered and disjoint from ADDON_KEYS", capability_registered_and_disjoint)

    def refusal_envelope_matches_arch01() -> None:
        assert "class CapabilityRequiredError" in errors
        assert 'code = "CAPABILITY_REQUIRED"' in errors
        assert '"CapabilityRequiredError"' in errors.split("__all__")[-1]
        assert '"remedy": "PLAN_UPGRADE"' in gate, (
            "a capability is bundled, so the remedy is a plan change. "
            "Returning an add-on purchase flow sends the customer somewhere "
            "with nothing to sell them."
        )
        # Behavioural, not comment-sniffing: the first cut of this gate
        # grepped for the word "price" and failed on the comment explaining
        # why there is no price. Assert the details dict instead.
        details = gate[gate.index("details={", gate.index("raise CapabilityRequiredError")):]
        details = details[: details.index("},")]
        # Comments stripped too. The comment inside this dict EXPLAINS that
        # there is no price, so scanning it for "price" fails the gate on its
        # own rationale — the same trap the A5 leak gate fell into.
        details = "\n".join(
            line for line in details.splitlines() if not line.strip().startswith("#")
        )
        for money_key in ("price", "amount", "micros", "currency", "checkout"):
            assert money_key not in details.lower(), (
                f"the refusal details carry {money_key!r}. A capability has "
                f"no standalone price; putting a number here would be the "
                f"first place a figure appears that billing cannot honour."
            )

    rec.check("Step 0 CapabilityRequiredError uses the ARCH-01 envelope", refusal_envelope_matches_arch01)


# ===========================================================================
# Mutation
# ===========================================================================

MUTANTS = [
    (
        "ambiguous money defaults instead of refusing",
        "app/core/normalize.py",
        'raise AmbiguousValue(',
        'return Money(micros=0, currency="XXX"); raise AmbiguousValue(',
    ),
    (
        "workspace currency overrides the document",
        "app/core/normalize.py",
        "    if detected is not None:\n        currency = detected",
        "    if False:\n        currency = detected",
    ),
    (
        "document_number stops stripping leading zeros",
        "app/core/normalize.py",
        'text = re.sub(r"(?<![0-9])0+(?=[0-9])", "", text)',
        "pass",
    ),
    (
        "USER role lock removed",
        "app/services/procurement_matching/role_classifier.py",
        'if (existing_source or "").upper() == SOURCE_USER:',
        "if False:",
    ),
]


def run_mutants(rec: Recorder, tmp: Path) -> None:
    import shutil

    for name, relpath, old, new in MUTANTS:
        def make(name=name, relpath=relpath, old=old, new=new) -> None:
            target = tmp / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BACKEND / relpath, target)
            text = target.read_text(encoding="utf-8")
            assert old in text, f"mutant anchor missing; the gate proves nothing: {old!r}"
            target.write_text(text.replace(old, new, 1), encoding="utf-8")

            sub = Recorder()
            if relpath.endswith("normalize.py"):
                gates_normalize(sub, _load(target, f"_mut_nz_{abs(hash(name))}"))
            else:
                gates_roles(sub, _load(target, f"_mut_rc_{abs(hash(name))}"))
            assert sub.failed > 0, f"mutant survived every gate: {name}"

        rec.check(f"mutant dies: {name}", make)


# ===========================================================================
# --db
# ===========================================================================


def gates_db(rec: Recorder, database_url: str) -> None:
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(database_url, future=True)

    def table_and_constraints_exist() -> None:
        with engine.connect() as conn:
            exists = conn.execute(
                sa_text("SELECT to_regclass('public.document_roles')")
            ).scalar_one_or_none()
            assert exists, "document_roles does not exist; run alembic upgrade head"
            names = {
                row[0]
                for row in conn.execute(
                    sa_text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'document_roles'::regclass"
                    )
                )
            }
            for required in (
                "ck_document_roles_role",
                "ck_document_roles_role_source",
                "ck_document_roles_confidence_range",
                "ck_document_roles_user_has_no_confidence",
                "uq_document_roles_work_item",
            ):
                assert any(required in n for n in names), f"missing {required}; have {sorted(names)}"

    rec.check("DB: document_roles and its constraints exist", table_and_constraints_exist)

    def checks_actually_refuse() -> None:
        from sqlalchemy.exc import IntegrityError, DataError

        with engine.begin() as conn:
            org = conn.execute(sa_text("SELECT id FROM organizations LIMIT 1")).scalar_one_or_none()
            ws = conn.execute(sa_text("SELECT id FROM workspaces LIMIT 1")).scalar_one_or_none()
            wi = conn.execute(sa_text("SELECT id FROM work_items LIMIT 1")).scalar_one_or_none()
            if not (org and ws and wi):
                raise AssertionError(
                    "this gate needs one organization, one workspace and one "
                    "work item to exist; run the development seed"
                )
            bad_rows = [
                ("unknown role", "NOT_A_ROLE", "CLASSIFIER", "0.9"),
                ("unknown source", "INVOICE", "MAGIC", "0.9"),
                ("confidence above 1", "INVOICE", "CLASSIFIER", "1.5"),
                ("USER row with a confidence", "INVOICE", "USER", "1.0"),
            ]
            for label, role, source, confidence in bad_rows:
                savepoint = conn.begin_nested()
                try:
                    conn.execute(
                        sa_text(
                            "INSERT INTO document_roles (organization_id, workspace_id, "
                            "work_item_id, role, role_source, role_confidence) "
                            "VALUES (:org, :ws, :wi, :role, :src, :conf)"
                        ),
                        {"org": str(org), "ws": str(ws), "wi": str(wi),
                         "role": role, "src": source, "conf": confidence},
                    )
                except (IntegrityError, DataError):
                    savepoint.rollback()
                    continue
                savepoint.rollback()
                raise AssertionError(f"the database accepted a row it must refuse: {label}")
            conn.rollback()

    rec.check("DB: document_roles refuses bad roles, sources and USER confidence", checks_actually_refuse)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-31 Step 0 verification — normalization (A2), document roles, capability")
    print(f"  root: {ROOT}")

    overall = 0
    offline = Recorder()
    try:
        nz = _load(BACKEND / "app/core/normalize.py", "_s0_normalize")
        rc = _load(
            BACKEND / "app/services/procurement_matching/role_classifier.py",
            "_s0_role_classifier",
        )
        gates_normalize(offline, nz)
        gates_roles(offline, rc)
        gates_schema_and_capability(offline)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    offline.report("OFFLINE")
    overall = max(overall, 1 if offline.failed else 0)

    if args.mutate:
        import tempfile

        mutate = Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            run_mutants(mutate, Path(tmp))
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

    print("\n" + ("ALL SELECTED GATES PASSED" if overall == 0 else "GATES FAILED"))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())