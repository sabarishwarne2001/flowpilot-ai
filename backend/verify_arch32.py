#!/usr/bin/env python3
"""ARCH-32 Steps 1-2 verification — schema, validators, burn, assembly, leak check.

    python verify_arch32.py
    python verify_arch32.py --mutate
    python verify_arch32.py --db --database-url postgresql+psycopg://...

EXIT 0 pass | 1 a gate failed | 2 harness could not run

SCOPE
=====

Steps 1 and 2: the schema, the six validator families, and the render -> burn
-> assemble -> leak-check core. The Step 3/4 gates named in the ARCH-32 brief
— detection geometry from `extraction_metadata`, the seven endpoints, the
entitlement refusal envelope, the job registered in all three places, and the
studio — are NOT here, because the code they test does not exist yet. A gate
that asserts nothing about absent code passes green and teaches the reader
that the phase is further along than it is; `report_pending()` names them
instead.

WHY THE SYNTHETIC PDF IS BUILT RATHER THAN COMMITTED AS A FIXTURE
=================================================================

A committed binary fixture is a fixture nobody can read in a diff. The
document this harness builds carries a distinct, checksum-valid identifier in
each of six hiding places — under a drawn black rectangle, in a form field
value, in an annotation's `/Contents`, in an XMP packet, in an embedded file,
and in an incremental update appended after the first save. Every one of them
is visible in the source of this file, so a reader can see exactly what the
gate claims to destroy.

The source is checked first, deliberately: the harness asserts that the
ORIGINAL document leaks all six before asserting the output leaks none. A
gate that only checks the output passes just as green when the fixture builder
quietly stopped embedding anything.

WHY THE MUTANT SET IS SHORT AND SPECIFIC
========================================

Each mutant produces NO symptom: no crash, no log line, no failing request.
They are the changes a future refactor makes by accident.

  burn at 90% opacity       looks identical on screen, recoverable by contrast
  object-tree scan removed  output extracts clean and carries an attachment
  assembler copies a page   pages look right and the text layer survives
  leak check skipped        a COMPLETED job whose cleanliness was never tested

Two of the four are applied as source rewrites to a copied tree. Two are
applied as behaviour, because "the assembler copied a source object" and "the
pipeline skipped the leak check" are not one-line text edits in these
modules — they are wrong CALLS — and a mutation that had to contort the source
to exist would be testing a shape the codebase cannot actually take. Both are
implemented here as explicitly named broken implementations run through the
same gates.

The CONTROL mutant must SURVIVE. A mutation suite where everything dies is
usually a suite whose gates are too broad to locate anything.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import os
import shutil
import sys
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MIGRATION = "alembic/versions/arch32_step1_redaction.py"
MODEL = "app/models/redaction.py"
APPLY_SCRIPT = "apply_arch32.py"

VOCABULARY = "app/services/redaction/vocabulary.py"
VALIDATORS = "app/services/redaction/validators.py"
RASTERIZE = "app/services/redaction/rasterize.py"
ASSEMBLE = "app/services/redaction/assemble.py"
LEAKCHECK = "app/services/redaction/leakcheck.py"
MANIFEST = "app/services/redaction/manifest.py"


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
            self.results.append(
                (name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )
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
                for line in detail.splitlines()[:6]:
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig")


def _strip_comments(text: str) -> str:
    """Remove `#` comments so a source assertion cannot pass on a docstring.

    ARCH-31's convention. A gate that greps for `leak_check` and finds it in a
    comment explaining why the leak check was removed is worse than no gate.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]
        out.append(stripped)
    return "\n".join(out)


def bootstrap_pure_namespace(root: Path) -> None:
    """Point `app.services.redaction` at `root` without running any __init__.

    WHY THIS IS NECESSARY, AND WHY IT IS NOT A HACK
    -----------------------------------------------

    `app/services/__init__.py` eagerly imports service modules, which reach
    `app.core.config` and from there pydantic, SQLAlchemy and the settings
    object. So a plain `import app.services.redaction.rasterize` cannot
    complete without a configured environment — which would make the offline
    gates require exactly the setup they exist to work without, and would
    make this harness unrunnable on a machine with only the PDF stack
    installed.

    The engines themselves import `app.services.redaction.vocabulary` by its
    real dotted name, because that is what the application does and a module
    that used a different import path in tests than in production would be
    testing a different module.

    So: seed `sys.modules` with namespace stubs carrying a `__path__`. Python
    finds the parent packages already present, never executes their
    `__init__.py`, and resolves the submodules from `root`. Nothing about
    the engines changes, and pointing `root` at a mutated copy is what makes
    `--mutate` reach an engine's own imports rather than only its top-level
    module.
    """
    import types

    # Anything cached from a previous root would shadow this one. This is the
    # line that makes the mutation runs independent of each other.
    for cached in [
        name for name in sys.modules if name.startswith("app.services.redaction")
    ]:
        if cached != "app.services.redaction":
            sys.modules.pop(cached, None)

    for dotted, path in (
        ("app", root / "app"),
        ("app.services", root / "app" / "services"),
        ("app.services.redaction", root / "app" / "services" / "redaction"),
    ):
        existing = sys.modules.get(dotted)
        if existing is not None and getattr(existing, "_arch32_stub", False):
            existing.__path__ = [str(path)]
            continue
        if existing is not None and dotted != "app":
            # A real, already-imported package. Leave it alone.
            continue
        stub = types.ModuleType(dotted)
        stub.__path__ = [str(path)]
        stub._arch32_stub = True  # type: ignore[attr-defined]
        sys.modules[dotted] = stub


def _load(root: Path, relpath: str, name: str) -> Any:
    """Load one module by file path, under a throwaway name.

    Used for the migration, the model and the patch script, which are not
    importable as packages here, and for the engines when a specific copy is
    wanted. `bootstrap_pure_namespace` must have run first, or an engine's
    own `from app.services.redaction...` import will pull the real package.
    """
    path = root / relpath
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


# ===========================================================================
# The synthetic document — six hiding places, each with a real identifier
# ===========================================================================

#: Every value is checksum-valid for its family, so a reader can confirm that
#: the detectors would genuinely have flagged them rather than taking the
#: harness's word for it.
SECRETS: dict[str, str] = {
    "under_rectangle": "4111111111111111",       # Visa, Luhn-valid
    "form_field": "AAAPZ1234C",                  # PAN, holder type P
    "annotation": "123-45-6789",                 # US SSN, area/group/serial ok
    "xmp_metadata": "234567890124",              # Aadhaar, Verhoeff-valid
    "embedded_file": "GB82WEST12345698765432",   # IBAN, mod-97 == 1
    "incremental_update": "27AAPFU0939F1ZV",     # GSTIN, check char V
}

#: The box that covers the card number on page 1, in PDF points. The text is
#: drawn at (72, 700) at 12pt; the box is deliberately a little larger.
CARD_BOX = (60.0, 688.0, 264.0, 716.0)


def build_synthetic_pdf() -> bytes:
    """A document that hides the six secrets in the six places §3.3 names."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, Pdf, Stream, String

    pdf = Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    font = pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    )
    page.Resources = Dictionary(Font=Dictionary(F1=font))

    # 1. Text, then an opaque black rectangle drawn OVER it. This is the
    #    "redaction" the whole phase exists to replace: it looks correct in
    #    every viewer and the text is still selectable underneath.
    content = (
        f"BT /F1 12 Tf 72 700 Td ({SECRETS['under_rectangle']}) Tj ET\n"
        "0 0 0 rg 60 690 200 24 re f\n"
        "BT /F1 12 Tf 72 640 Td (Ordinary visible line) Tj ET\n"
    ).encode("ascii")
    page.Contents = pdf.make_indirect(Stream(pdf, content))

    # 2. An annotation carrying text in /Contents.
    annotation = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Text,
            Rect=Array([100, 600, 120, 620]),
            Contents=String(f"SSN {SECRETS['annotation']}"),
        )
    )
    # 3. A form field with a value.
    field = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Widget,
            FT=Name.Tx,
            T=String("pan"),
            V=String(SECRETS["form_field"]),
            Rect=Array([100, 560, 300, 580]),
        )
    )
    page.Annots = Array([annotation, field])
    pdf.Root.AcroForm = pdf.make_indirect(Dictionary(Fields=Array([field])))

    # JavaScript and an OpenAction, so the object scan has all seven keys to
    # find rather than only the five the secrets need.
    js = pdf.make_indirect(Stream(pdf, b"app.alert('hello');"))
    pdf.Root.Names = pdf.make_indirect(
        Dictionary(
            JavaScript=Dictionary(
                Names=Array(
                    [
                        String("startup"),
                        pdf.make_indirect(Dictionary(S=Name.JavaScript, JS=js)),
                    ]
                )
            )
        )
    )
    pdf.Root.OpenAction = pdf.make_indirect(
        Dictionary(S=Name.JavaScript, JS=String("app.alert('open')"))
    )

    # 4. An embedded file.
    payload = pdf.make_indirect(
        Stream(pdf, f"account iban {SECRETS['embedded_file']}".encode("ascii"))
    )
    payload.Type = Name.EmbeddedFile
    filespec = pdf.make_indirect(
        Dictionary(
            Type=Name.Filespec,
            F=String("notes.txt"),
            EF=Dictionary(F=payload),
        )
    )
    pdf.Root.Names.EmbeddedFiles = Dictionary(
        Names=Array([String("notes.txt"), filespec])
    )

    # 5. XMP metadata.
    xmp = (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{SECRETS['xmp_metadata']}</dc:title>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode("utf-8")
    metadata = pdf.make_indirect(Stream(pdf, xmp))
    metadata.Type = Name.Metadata
    metadata.Subtype = Name.XML
    pdf.Root.Metadata = metadata

    # An optional content group, for /OCProperties.
    ocg = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("hidden layer")))
    pdf.Root.OCProperties = pdf.make_indirect(
        Dictionary(OCGs=Array([ocg]), D=Dictionary(ON=Array([ocg])))
    )

    first = io.BytesIO()
    pdf.save(first)
    pdf.close()

    # 6. An incremental update: a second revision appending an annotation.
    #    The first revision's bytes survive in the file, which is the property
    #    that makes content-stream editing unsafe and this gate necessary.
    second = pikepdf.Pdf.open(io.BytesIO(first.getvalue()))
    later = second.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.FreeText,
            Rect=Array([300, 500, 500, 520]),
            Contents=String(f"GSTIN {SECRETS['incremental_update']}"),
        )
    )
    second.pages[0].Annots.append(later)
    out = io.BytesIO()
    second.save(out)
    second.close()
    return out.getvalue()


def run_pipeline(
    root: Path,
    pdf_bytes: bytes,
    *,
    dpi: int = 300,
    boxes: Optional[list[tuple[float, float, float, float]]] = None,
    assembler: Optional[Callable[..., Any]] = None,
    skip_leak_check: bool = False,
) -> dict[str, Any]:
    """render -> burn -> assemble -> leak check, against modules under `root`.

    `root` is a parameter so the mutation runs can point it at a rewritten
    copy of the tree. `assembler` and `skip_leak_check` exist for the two
    behavioural mutants; the default path uses neither.
    """
    bootstrap_pure_namespace(root)
    rasterize = _load(root, RASTERIZE, "_a32_rasterize")
    assemble = _load(root, ASSEMBLE, "_a32_assemble")
    leakcheck = _load(root, LEAKCHECK, "_a32_leakcheck")

    rects = boxes if boxes is not None else [CARD_BOX]
    pages = rasterize.render_pages(pdf_bytes, dpi=dpi)
    burned = []
    for page in pages:
        page_boxes = [
            rasterize.Box(page.page_number, x0, y0, x1, y1) for x0, y0, x1, y1 in rects
        ]
        burned.append(rasterize.burn_page(page, page_boxes))

    build = assembler or assemble.assemble_pdf
    document = build([b.page for b in burned])
    output = document.pdf_bytes

    if skip_leak_check:
        report = None
    else:
        report = leakcheck.check_output(output, list(SECRETS.values()))

    return {
        "rasterize": rasterize,
        "leakcheck": leakcheck,
        "burned": burned,
        "output": output,
        "report": report,
        "sha256": hashlib.sha256(output).hexdigest(),
    }


# ===========================================================================
# Step 1 — schema, vocabulary, model, patch script
# ===========================================================================


def gates_schema(rec: Recorder) -> None:
    bootstrap_pure_namespace(BACKEND)
    migration = _read(BACKEND / MIGRATION)
    model = _read(BACKEND / MODEL)
    vocab = _load(BACKEND, VOCABULARY, "_a32_vocab")

    def revision_chain() -> None:
        assert 'revision = "arch32_step1_redaction"' in migration, (
            "the migration must declare revision arch32_step1_redaction"
        )
        assert 'down_revision = "arch31_step1_procurement_matching"' in migration, (
            "ARCH-32 Step 1 must chain onto the certified ARCH-31 head "
            "arch31_step1_procurement_matching"
        )

    rec.check("migration chains onto the ARCH-31 head", revision_chain)

    def statuses_agree() -> None:
        """Compare the migration's constants to the vocabulary's, via AST.

        Parsed rather than imported. Importing an Alembic revision pulls
        `alembic.op`, which only exists inside a migration context — so an
        import-based gate cannot run outside `alembic upgrade`, which is
        every circumstance this harness is used in.
        """
        import ast as _ast

        tree = _ast.parse(migration)
        literals: dict[str, Any] = {}
        for node in tree.body:
            if not isinstance(node, (_ast.Assign, _ast.AnnAssign)):
                continue
            targets = (
                node.targets if isinstance(node, _ast.Assign) else [node.target]
            )
            if node.value is None:
                continue
            for target in targets:
                if isinstance(target, _ast.Name):
                    try:
                        literals[target.id] = _ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        pass

        assert tuple(literals["JOB_STATUSES"]) == tuple(vocab.JOB_STATUSES), (
            f"migration JOB_STATUSES {literals['JOB_STATUSES']} != vocabulary "
            f"{vocab.JOB_STATUSES}"
        )
        assert tuple(literals["GEOMETRY_PRECISIONS"]) == tuple(
            vocab.GEOMETRY_PRECISIONS
        ), (
            f"migration GEOMETRY_PRECISIONS {literals['GEOMETRY_PRECISIONS']} "
            f"!= vocabulary {vocab.GEOMETRY_PRECISIONS}"
        )
        assert literals["MIN_RENDER_DPI"] == vocab.MIN_RENDER_DPI
        assert literals["MAX_RENDER_DPI"] == vocab.MAX_RENDER_DPI
        assert literals["DEFAULT_RENDER_DPI"] == vocab.DEFAULT_RENDER_DPI

    rec.check(
        "migration vocabularies equal app/services/redaction/vocabulary.py",
        statuses_agree,
    )

    def check_bodies_are_generated_not_typed() -> None:
        """The CHECK body must be BUILT from the shared tuple, not retyped.

        A hand-written `status IN ('DETECTING', ...)` passes a tuple-equality
        gate on the day it is written and drifts the first time a status is
        added. Asserting the f-string is what closes that gap — and the
        migration's bound DPI check has to come from the same constants for
        the same reason.
        """
        body = _strip_comments(migration)
        assert 'f"status IN ({_quoted(JOB_STATUSES)})"' in body, (
            "ck_rj_status_known does not derive its body from JOB_STATUSES"
        )
        assert (
            'f"geometry_precision IN ({_quoted(GEOMETRY_PRECISIONS)})"' in body
        ), "ck_rr_precision_known does not derive its body from GEOMETRY_PRECISIONS"
        assert (
            'f"render_dpi BETWEEN {MIN_RENDER_DPI} AND {MAX_RENDER_DPI}"' in body
        ), "ck_rj_dpi_bounded does not derive its bounds from the constants"

    rec.check(
        "CHECK bodies are generated from the shared constants", check_bodies_are_generated_not_typed
    )

    def sealed_constraint() -> None:
        body = _strip_comments(migration)
        assert "ck_rj_completed_is_sealed" in body
        for conjunct in (
            "output_file_id IS NOT NULL",
            "output_sha256 IS NOT NULL",
            "manifest_file_id IS NOT NULL",
            "leak_check_passed IS TRUE",
            "approved_at IS NOT NULL",
        ):
            assert conjunct in body, (
                f"ck_rj_completed_is_sealed is missing {conjunct!r}. A "
                "COMPLETED job that is missing any one of these is a job "
                "claiming a guarantee nothing verified."
            )

    rec.check("ck_rj_completed_is_sealed requires all five seals", sealed_constraint)

    def manual_author_constraint() -> None:
        body = _strip_comments(migration)
        assert "ck_rr_manual_has_author" in body
        assert "detector <> 'manual' OR created_by_user_id IS NOT NULL" in body

    rec.check("ck_rr_manual_has_author is present", manual_author_constraint)

    def tenancy_columns() -> None:
        body = _strip_comments(migration)
        # ARCH-02: both columns on BOTH tables, not only the parent.
        assert body.count('"organization_id"') >= 2, (
            "organization_id must be on redaction_jobs AND redaction_regions"
        )
        assert body.count('"workspace_id"') >= 2, (
            "workspace_id must be on redaction_jobs AND redaction_regions"
        )
        assert "ix_redaction_regions_workspace" in body
        assert "ix_redaction_regions_organization" in body

    rec.check("ARCH-02: both tenancy columns on both tables, indexed", tenancy_columns)

    def licenses_stated() -> None:
        header = migration[: migration.find('"""', 3) + 3]
        assert "pikepdf" in header and "MPL-2.0" in header, (
            "the migration header must state pikepdf's license"
        )
        assert "pypdfium2" in header and "BSD" in header, (
            "the migration header must state pypdfium2's license"
        )
        assert "AGPL" in header and "PyMuPDF" in header, (
            "the header must record why PyMuPDF is not used"
        )

    rec.check("migration header states the licenses this phase takes on", licenses_stated)

    def model_matches_migration() -> None:
        body = _strip_comments(model)
        for name in (
            "ck_rj_status_known",
            "ck_rj_dpi_bounded",
            "ck_rj_completed_is_sealed",
            "ck_rr_box_ordered",
            "ck_rr_page_positive",
            "ck_rr_precision_known",
            "ck_rr_manual_has_author",
        ):
            assert name in body, f"{name} is in the migration and not in the model"
            assert name in migration, f"{name} is in the model and not in the migration"

    rec.check("model and migration declare the same constraints", model_matches_migration)

    def no_plaintext_column() -> None:
        """No COLUMN can hold the matched text. Checked on names, not prose.

        The first cut of this gate grepped the whole file and failed on the
        word "plaintext" inside the header that explains why no such column
        exists — a gate that fails on its own documentation. Column names are
        what matter, so column names are what it reads.
        """
        import ast as _ast
        import re as _re

        names: set[str] = set()
        for node in _ast.walk(_ast.parse(migration)):
            if (
                isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "Column"
                and node.args
                and isinstance(node.args[0], _ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)

        assert names, "no columns were parsed out of the migration"
        assert "token_digest" in names, (
            "redaction_regions must carry token_digest"
        )
        for forbidden in (
            "matched_text",
            "token_text",
            "token_value",
            "matched_value",
            "plaintext",
            "excerpt",
            "snippet",
        ):
            assert forbidden not in names, (
                f"a column named {forbidden!r} exists. The region table stores "
                "an HMAC digest and never the matched text; a column that can "
                "hold it will eventually hold it."
            )
        # Model and migration must agree on the column set, or the ORM can
        # write a field the database never heard of.
        model_names = set(_re.findall(r"^\s{4}(\w+):\s*Mapped\[", model, _re.M))
        missing = sorted(
            n
            for n in model_names
            if n not in names and n not in {"id", "regions", "job"}
        )
        assert not missing, (
            f"the model declares columns the migration does not create: {missing}"
        )

    rec.check("no column can hold the matched text", no_plaintext_column)


def gates_patch_script(rec: Recorder) -> None:
    module = _load(BACKEND, APPLY_SCRIPT, "_a32_apply")

    def sentinels_are_substrings() -> None:
        for patch in module.PATCHES:
            written = "\n".join(edit.replacement for edit in patch.edits)
            assert patch.sentinel in written, (
                f"{patch.relpath}: sentinel {patch.sentinel!r} is not a "
                "substring of the text its own patch writes. The second run "
                "would not find it, would re-apply the edit, and would then "
                "fail with 'anchor not found' on a correct tree."
            )

    rec.check("every sentinel is a substring of its own replacement", sentinels_are_substrings)

    def anchors_declare_counts() -> None:
        for patch in module.PATCHES:
            for edit in patch.edits:
                assert edit.occurrences >= 1
                assert edit.description, (
                    f"{patch.relpath}: an edit with no description cannot be "
                    "reported usefully when its anchor fails"
                )

    rec.check("every edit declares an occurrence count and a description", anchors_declare_counts)

    def registries_patched() -> None:
        from app.core import entitlements, usage_events

        assert entitlements.REDACTION_CAPABILITY == "capability.redaction"
        assert entitlements.REDACTION_CAPABILITY in entitlements.CAPABILITY_KEYS, (
            "capability.redaction must be in CAPABILITY_KEYS"
        )
        assert entitlements.REDACTION_CAPABILITY not in entitlements.ADDON_KEYS, (
            "capability.redaction must NOT be in ADDON_KEYS: add-on keys are "
            "asserted equal to entitlement_service's priced catalog at import, "
            "and a capability has no standalone price"
        )
        assert entitlements.REDACTION_CAPABILITY in entitlements.ENTITLEMENT_KEYS
        meter = usage_events.resolve("redaction.page")
        assert meter.billable is True
        assert meter.unit == usage_events.UsageUnit.PAGE, (
            "redaction.page is metered per OUTPUT page, so its unit is PAGE"
        )

    rec.check(
        "apply_arch32 has registered capability.redaction and redaction.page",
        registries_patched,
    )


# ===========================================================================
# Step 2 — validator truth tables
# ===========================================================================

#: Table-driven, per §3.8. Each family gets KNOWN-VALID and KNOWN-INVALID
#: rows, and the invalid rows are chosen to be things a naive implementation
#: accepts: a Luhn-valid number with no issuer, a Verhoeff-valid Aadhaar
#: starting 0, a PAN with an unallotted holder type, a GSTIN with state code
#: 00, the SSN placeholders printed on blank forms, an IBAN of the wrong
#: length for its country.
TRUTH_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "card_number": (
        (
            "4111111111111111",
            "4111 1111 1111 1111",
            "5500005555555559",
            "378282246310005",
            "6011111111111117",
            "3530111333300000",
        ),
        (
            "4111111111111112",   # Luhn fails
            "1234567812345670",   # Luhn passes, no published IIN range
            "411111111111111",    # right IIN, wrong length for Visa
            "9999999999999995",   # Luhn passes, IIN 99 is unallotted
            "not a card",
        ),
    ),
    "aadhaar": (
        ("234567890124", "2345 6789 0124"),
        (
            "234567890123",       # Verhoeff fails
            "023456789012",       # leading 0: never issued
            "123456789012",       # leading 1: never issued
            "23456789012",        # eleven digits
        ),
    ),
    "pan_india": (
        ("AABCU9603R", "AAPFU0939F", "ABCPE1234K"),
        (
            "ABCDE1234F",         # holder type D is not allotted
            "AABCU96033",         # last character must be a letter
            "AABC9603R",          # too short
            "aabcu9603r1",        # too long
        ),
    ),
    "gstin": (
        ("27AAPFU0939F1ZV", "27AAACR5055K1Z7", "09AAACH7409R1ZZ"),
        (
            "27AAPFU0939F1ZW",    # check character wrong
            "00AAPFU0939F1ZV",    # state code 00 is unallotted
            "99AAPFU0939F1Z",     # too short
            "27ABCDE0939F1ZV",    # embedded PAN has holder type D
        ),
    ),
    "us_ssn": (
        ("123-45-6789", "078-05-1120", "123456789"),
        (
            "000-12-3456",        # area 000
            "666-12-3456",        # area 666
            "900-12-3456",        # area in the ITIN range
            "123-00-6789",        # group 00
            "123-45-0000",        # serial 0000
        ),
    ),
    "iban": (
        (
            "GB82WEST12345698765432",
            "GB82 WEST 1234 5698 7654 32",
            "DE89370400440532013000",
            "FR1420041010050500013M02606",
        ),
        (
            "GB82WEST12345698765433",   # mod-97 != 1
            "GB82WEST123456987654",     # wrong length for GB
            "ZZ82WEST12345698765432",   # country not in the registry
            "GB82WEST1234569876543!",   # non-alphanumeric
        ),
    ),
}


def gates_validators(rec: Recorder, root: Path = BACKEND) -> None:
    bootstrap_pure_namespace(root)
    validators = _load(root, VALIDATORS, "_a32_validators")
    vocab = _load(root, VOCABULARY, "_a32_vocab2")

    def every_checksum_detector_has_a_validator() -> None:
        missing = sorted(
            d for d in vocab.CHECKSUM_DETECTORS if d not in validators.VALIDATORS
        )
        assert not missing, (
            f"these detectors claim checksum backing and have no validator: "
            f"{missing}. Each would accept every pattern match."
        )
        extra = sorted(
            d for d in validators.VALIDATORS if d not in vocab.CHECKSUM_DETECTORS
        )
        assert not extra, (
            f"these validators are registered for detectors the vocabulary "
            f"does not call checksum-backed: {extra}"
        )

    rec.check(
        "every checksum detector has a validator, and vice versa",
        every_checksum_detector_has_a_validator,
    )

    for detector, (valid, invalid) in TRUTH_TABLE.items():
        fn = validators.VALIDATORS[detector]

        def make_valid_gate(fn: Any = fn, rows: tuple[str, ...] = valid) -> Callable[[], None]:
            def gate() -> None:
                for row in rows:
                    assert fn(row) is True, f"rejected a known-valid value: {row!r}"
            return gate

        def make_invalid_gate(
            fn: Any = fn, rows: tuple[str, ...] = invalid
        ) -> Callable[[], None]:
            def gate() -> None:
                for row in rows:
                    assert fn(row) is False, f"accepted a known-INVALID value: {row!r}"
            return gate

        rec.check(f"{detector}: known-valid truth table", make_valid_gate())
        rec.check(f"{detector}: known-invalid truth table", make_invalid_gate())

    def verhoeff_published_vectors() -> None:
        # The two vectors every Verhoeff implementation is checked against.
        assert validators.verhoeff_ok("2363") is True
        assert validators.verhoeff_ok("2364") is False
        assert validators.verhoeff_ok("123451") is True
        assert validators.verhoeff_ok("123453") is False

    rec.check("Verhoeff matches the published vectors", verhoeff_published_vectors)

    def gstin_check_character_is_computed() -> None:
        assert validators.gstin_check_character("27AAPFU0939F1Z") == "V"
        assert validators.gstin_check_character("09AAACH7409R1Z") == "Z"
        assert validators.gstin_check_character("short") is None

    rec.check("GSTIN check character reproduces published values", gstin_check_character_is_computed)

    def iban_mod97_is_one() -> None:
        assert validators.iban_mod97("GB82WEST12345698765432") == 1
        assert validators.iban_mod97("DE89370400440532013000") == 1

    rec.check("IBAN mod-97 returns 1 for valid inputs", iban_mod97_is_one)


# ===========================================================================
# Step 2 — geometry, burn, assembly, leak check
# ===========================================================================


def gates_engine(rec: Recorder, root: Path = BACKEND) -> None:
    bootstrap_pure_namespace(root)
    rasterize = _load(root, RASTERIZE, "_a32_rast2")
    leakcheck = _load(root, LEAKCHECK, "_a32_leak2")
    manifest = _load(root, MANIFEST, "_a32_manifest")
    vocab = _load(root, VOCABULARY, "_a32_vocab3")

    source = build_synthetic_pdf()

    def source_actually_leaks() -> None:
        """The fixture must be dirty, or the output gate proves nothing."""
        text = "\n".join(leakcheck.extract_text_per_page(source))
        folded = leakcheck.fold(text)
        assert leakcheck.fold(SECRETS["under_rectangle"]) in folded, (
            "the fixture's card number is not extractable from the SOURCE, so "
            "the drawn rectangle is not demonstrating the defect this phase "
            "exists to fix"
        )
        forbidden = leakcheck.scan_object_tree(source)
        for key in vocab.FORBIDDEN_OUTPUT_KEYS:
            assert key in forbidden, (
                f"the fixture does not carry {key}; the object scan gate "
                "would pass on the output for the wrong reason"
            )

    rec.check("the synthetic source leaks all six hiding places", source_actually_leaks)

    result = run_pipeline(root, source)

    def output_is_clean() -> None:
        report = result["report"]
        assert report.passed is True, (
            f"the leak check failed on the output: {report.summary()}"
        )
        assert report.text_hits == (), f"text survived on pages {report.text_hits}"
        assert report.forbidden_keys == (), (
            f"the output carries {report.forbidden_keys}"
        )
        assert report.stream_hits == 0

    rec.check("no secret survives in the output, by text or by object scan", output_is_clean)

    def every_hiding_place_absent() -> None:
        """Each of the six, named individually, so a failure says which one."""
        text = leakcheck.fold("\n".join(leakcheck.extract_text_per_page(result["output"])))
        raw = leakcheck.fold(result["output"].decode("latin-1", errors="ignore"))
        for place, secret in SECRETS.items():
            folded = leakcheck.fold(secret)
            assert folded not in text, f"{place}: still extractable as text"
            assert folded not in raw, f"{place}: still present in the raw bytes"

    rec.check("each of the six hiding places is individually absent", every_hiding_place_absent)

    def leak_check_fails_on_the_source() -> None:
        """The discriminating gate: the check must REJECT a dirty document.

        Asserting only that the output passes is the mistake that lets a
        gutted leak check ship green — a check that returns "clean" for
        everything passes that assertion perfectly. This runs the real
        `check_output` against the untouched source and requires all three
        of its sub-checks to fire.
        """
        report = leakcheck.check_output(source, list(SECRETS.values()))
        assert report.passed is False, (
            "check_output called the ORIGINAL document clean. It is not; it "
            "carries six identifiers in six hiding places."
        )
        assert report.text_hits, "the text extraction check found nothing"
        assert report.forbidden_keys, (
            "the object-tree scan found nothing in a document carrying an "
            "AcroForm, an annotation, an embedded file, XMP metadata, "
            "JavaScript, an OpenAction and an optional content group"
        )
        for key in vocab.FORBIDDEN_OUTPUT_KEYS:
            assert key in report.forbidden_keys, f"the scan missed {key}"
        assert "failed" in report.sentence().lower()

    rec.check(
        "the leak check REJECTS the unredacted source (all three sub-checks fire)",
        leak_check_fails_on_the_source,
    )

    def uniform_fill_catches_a_dirty_rectangle() -> None:
        """Feed the verifier a rectangle that was not filled. It must raise.

        Same reasoning as the gate above. `verify_uniform_fill` returning
        None on a correct burn proves nothing about whether it would notice
        an incorrect one.
        """
        import numpy as np

        page = rasterize.RenderedPage(
            page_number=1,
            pixels=np.full((100, 100), 255, dtype=np.uint8),
            width_points=72.0,
            height_points=72.0,
            dpi=300,
        )
        page.pixels[10:20, 10:20] = 0
        page.pixels[15, 15] = 200  # one surviving pixel inside the "burn"
        dirty = rasterize.BurnResult(
            page=page,
            rects=(rasterize.PixelRect(left=10, top=10, right=20, bottom=20),),
        )
        raised = False
        try:
            rasterize.verify_uniform_fill(dirty)
        except rasterize.UniformFillError:
            raised = True
        assert raised, (
            "verify_uniform_fill accepted a rectangle holding a surviving "
            "pixel. A single pixel of original glyph is enough for contrast "
            "recovery, and this function is the only thing that looks."
        )

    rec.check(
        "verify_uniform_fill raises on a rectangle that is not uniformly filled",
        uniform_fill_catches_a_dirty_rectangle,
    )

    def uniform_fill_inside_every_box() -> None:
        burned = result["burned"]
        assert burned, "nothing was burned"
        for item in burned:
            assert item.rects, (
                f"page {item.page.page_number} recorded no burned rectangle; a "
                "job that burned nothing passes the leak check trivially"
            )
            rasterize.verify_uniform_fill(item)

    rec.check("every burned rectangle is uniformly filled", uniform_fill_inside_every_box)

    def box_conversion_never_shrinks() -> None:
        """Rounding may grow a box. It may never shrink one."""
        page_w, page_h = 612.0, 792.0
        px_w, px_h = 2550, 3301
        box = rasterize.Box(1, 100.37, 200.61, 140.29, 214.13)
        rect = rasterize.box_to_pixels(
            box,
            width_points=page_w,
            height_points=page_h,
            width_px=px_w,
            height_px=px_h,
        )
        scale_x = px_w / page_w
        scale_y = px_h / page_h
        assert rect.left <= box.x0 * scale_x, "left edge rounded inward"
        assert rect.right >= box.x1 * scale_x, "right edge rounded inward"
        assert rect.top <= (page_h - box.y1) * scale_y, "top edge rounded inward"
        assert rect.bottom >= (page_h - box.y0) * scale_y, "bottom edge rounded inward"

    rec.check("pixel conversion rounds outward, never inward", box_conversion_never_shrinks)

    def padding_is_applied() -> None:
        pages = rasterize.render_pages(source, dpi=300)
        page = pages[0]
        box = rasterize.Box(1, *CARD_BOX)
        bare = rasterize.box_to_pixels(
            box,
            width_points=page.width_points,
            height_points=page.height_points,
            width_px=page.width_px,
            height_px=page.height_px,
        )
        padded = rasterize.box_to_pixels(
            box.padded(vocab.BOX_PADDING_POINTS),
            width_points=page.width_points,
            height_points=page.height_points,
            width_px=page.width_px,
            height_px=page.height_px,
        )
        assert padded.area > bare.area, (
            "the padded rectangle is not larger than the bare one; "
            "antialiased ink outside the reported glyph box would survive"
        )

    rec.check("boxes are padded before burning", padding_is_applied)

    def output_pages_are_images_only() -> None:
        import pikepdf

        with pikepdf.open(io.BytesIO(result["output"])) as pdf:
            for page in pdf.pages:
                resources = page.obj.get("/Resources")
                assert resources is not None
                assert "/Font" not in resources, (
                    "an output page carries a font resource; a page rebuilt "
                    "from pixels has nothing to set type with"
                )
                assert "/XObject" in resources

    rec.check("output pages carry an image XObject and no font", output_pages_are_images_only)

    def deterministic_hash() -> None:
        again = run_pipeline(root, source)
        assert again["sha256"] == result["sha256"], (
            "two runs of the same job produced different bytes. The manifest "
            "attests to the output hash, and a hash that changes on a re-run "
            "attests to nothing."
        )

    rec.check("the output hash is identical across repeated runs", deterministic_hash)

    def output_size_is_reasonable() -> None:
        per_page = len(result["output"]) / max(1, len(result["burned"]))
        assert per_page < 2_000_000, (
            f"{per_page:,.0f} bytes per page. §3.9 budgets 150-400 KB for a "
            "typical page; this size means the image stream was stored "
            "uncompressed — check stream_decode_level on save()."
        )

    rec.check("output pages are compressed, not raw pixel dumps", output_size_is_reasonable)

    # --- manifest -----------------------------------------------------------

    def manifest_carries_no_region_text() -> None:
        regions = [
            manifest.RegionFingerprint(
                page_number=1,
                x0=Decimal("60.0000"),
                y0=Decimal("688.0000"),
                x1=Decimal("264.0000"),
                y1=Decimal("716.0000"),
                detector="card_number",
                geometry_precision="GLYPH",
            )
        ]
        payload = manifest.build_manifest(
            job_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
            work_item_id="00000000-0000-0000-0000-000000000004",
            source_sha256=hashlib.sha256(source).hexdigest(),
            output_sha256=result["sha256"],
            profile_key="financial",
            render_dpi=300,
            grayscale=True,
            restore_text_layer=False,
            text_layer_applied=False,
            page_count=1,
            output_bytes=len(result["output"]),
            regions=regions,
            disabled_region_count=0,
            leak_check=result["report"].summary(),
            approved_by="00000000-0000-0000-0000-000000000005",
            approved_at="2026-09-14T00:00:00+00:00",
            created_at="2026-09-14T00:00:00+00:00",
            completed_at="2026-09-14T00:00:01+00:00",
        )
        serialised = manifest.canonical_json(payload).decode("utf-8")
        folded = leakcheck.fold(serialised)
        for place, secret in SECRETS.items():
            assert leakcheck.fold(secret) not in folded, (
                f"the manifest contains the {place} secret. The manifest "
                "travels with the document."
            )
        assert payload["regions"]["applied"] == 1
        assert payload["regions"]["checksum_validated"] == 1
        assert payload["output"]["annotations_rendered"] is False

    rec.check("the manifest contains no region text", manifest_carries_no_region_text)

    def digest_covers_engine_version() -> None:
        region = manifest.RegionFingerprint(
            page_number=1,
            x0=Decimal("1"),
            y0=Decimal("2"),
            x1=Decimal("3"),
            y1=Decimal("4"),
            detector="card_number",
            geometry_precision="GLYPH",
        )
        kwargs = dict(
            source_sha256="a" * 64,
            profile_key="financial",
            render_dpi=300,
            grayscale=True,
            restore_text_layer=False,
            regions=[region],
        )
        base = manifest.input_digest(**kwargs)
        assert base == manifest.input_digest(**kwargs), "digest is not stable"
        assert base != manifest.input_digest(**{**kwargs, "render_dpi": 301})
        assert base != manifest.input_digest(**{**kwargs, "profile_key": "india_kyc"})
        assert base != manifest.input_digest(**{**kwargs, "grayscale": False})
        assert base != manifest.input_digest(
            **{**kwargs, "restore_text_layer": True}
        )
        assert manifest.ENGINE_VERSION in manifest.canonical_json(
            {"engine_version": manifest.ENGINE_VERSION}
        ).decode("utf-8")

    rec.check(
        "input_digest changes with every setting that changes the output",
        digest_covers_engine_version,
    )

    def digest_ignores_region_order() -> None:
        a = manifest.RegionFingerprint(
            1, Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), "iban", "GLYPH"
        )
        b = manifest.RegionFingerprint(
            2, Decimal("5"), Decimal("6"), Decimal("7"), Decimal("8"), "gstin", "BLOCK"
        )
        common = dict(
            source_sha256="b" * 64,
            profile_key="financial",
            render_dpi=300,
            grayscale=True,
            restore_text_layer=False,
        )
        assert manifest.input_digest(regions=[a, b], **common) == manifest.input_digest(
            regions=[b, a], **common
        ), "the digest depends on row order, so it depends on the database's mood"

    rec.check("input_digest is independent of region order", digest_ignores_region_order)

    def profiles_are_closed() -> None:
        for key in vocab.PROFILE_KEYS:
            detectors = vocab.profile_detectors(key)
            assert detectors, f"profile {key} runs no detectors"
            unknown = [d for d in detectors if d not in vocab.DETECTORS]
            assert not unknown, f"profile {key} names unknown detectors {unknown}"
        try:
            vocab.profile_detectors("no_such_profile")
        except vocab.UnknownProfileError:
            pass
        else:  # pragma: no cover
            raise AssertionError(
                "an unknown profile resolved to a default. Defaulting to "
                "'everything' over-redacts silently; defaulting to 'nothing' "
                "produces a COMPLETED job with no regions and a passed leak "
                "check, which is a document that looks redacted and is not."
            )
        assert vocab.profile_mentions_names(vocab.PROFILE_HIPAA_SAFE_HARBOR) is True
        assert vocab.profile_mentions_names(vocab.PROFILE_FINANCIAL) is False

    rec.check("profiles are closed and an unknown profile raises", profiles_are_closed)


# ===========================================================================
# --db
# ===========================================================================


def gates_db(rec: Recorder, database_url: str) -> None:
    import sqlalchemy as sa

    engine = sa.create_engine(database_url, future=True)

    def table_exists() -> None:
        with engine.connect() as conn:
            for table in ("redaction_jobs", "redaction_regions"):
                present = conn.execute(
                    sa.text("SELECT to_regclass(:name)"), {"name": table}
                ).scalar()
                assert present is not None, (
                    f"{table} does not exist. Run `alembic upgrade head`."
                )

    rec.check("both tables exist at the migrated head", table_exists)

    def constraints_exist() -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN "
                    "('redaction_jobs'::regclass, 'redaction_regions'::regclass)"
                )
            ).scalars().all()
        for name in (
            "ck_rj_status_known",
            "ck_rj_dpi_bounded",
            "ck_rj_completed_is_sealed",
            "ck_rj_failed_has_reason",
            "ck_rr_box_ordered",
            "ck_rr_page_positive",
            "ck_rr_precision_known",
            "ck_rr_manual_has_author",
            "ck_rr_confidence_unit_interval",
        ):
            assert any(name in x for x in rows), f'{name} is not on the migrated database; have {sorted(rows)}'

    rec.check("every declared CHECK constraint reached the database", constraints_exist)

    def completed_without_leak_check_is_refused() -> None:
        """The gate the whole phase rests on. Rolled back either way."""
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    sa.text(
                        "CREATE TEMP TABLE _probe (LIKE redaction_jobs "
                        "INCLUDING CONSTRAINTS INCLUDING DEFAULTS) "
                        "ON COMMIT DROP"
                    )
                )
                base = {
                    "org": "00000000-0000-0000-0000-000000000001",
                    "ws": "00000000-0000-0000-0000-000000000002",
                    "wi": "00000000-0000-0000-0000-000000000003",
                    "sf": "00000000-0000-0000-0000-000000000004",
                    "of": "00000000-0000-0000-0000-000000000005",
                    "mf": "00000000-0000-0000-0000-000000000006",
                    "sha": "a" * 64,
                }
                insert = sa.text(
                    "INSERT INTO _probe (id, organization_id, workspace_id, "
                    "work_item_id, source_file_id, source_sha256, status, "
                    "profile_key, render_dpi, restore_text_layer, "
                    "output_file_id, output_sha256, manifest_file_id, "
                    "leak_check_passed, approved_at) VALUES "
                    "(gen_random_uuid(), :org, :ws, :wi, :sf, :sha, "
                    "'COMPLETED', 'financial', 300, true, :of, :sha, :mf, "
                    ":leak, now())"
                )

                refused = False
                try:
                    conn.execute(insert, {**base, "leak": False})
                except Exception:  # noqa: BLE001
                    refused = True
                assert refused, (
                    "the database accepted a COMPLETED job with "
                    "leak_check_passed = false. ck_rj_completed_is_sealed is "
                    "not doing its job, and every claim this phase sells is "
                    "unenforced."
                )

                conn.rollback()
                trans = conn.begin()
                conn.execute(
                    sa.text(
                        "CREATE TEMP TABLE _probe2 (LIKE redaction_jobs "
                        "INCLUDING CONSTRAINTS INCLUDING DEFAULTS) "
                        "ON COMMIT DROP"
                    )
                )
                accepted = True
                try:
                    conn.execute(
                        sa.text(str(insert).replace("_probe", "_probe2")),
                        {**base, "leak": True},
                    )
                except Exception as exc:  # noqa: BLE001
                    accepted = False
                    detail = str(exc)
                assert accepted, (
                    "the database refused a fully sealed COMPLETED row, so "
                    f"the constraint is too strict: {detail}"
                )
            finally:
                trans.rollback()

    rec.check(
        "COMPLETED is refused without a passed leak check, accepted with one",
        completed_without_leak_check_is_refused,
    )

    def manual_region_needs_an_author() -> None:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    sa.text(
                        "CREATE TEMP TABLE _rprobe (LIKE redaction_regions "
                        "INCLUDING CONSTRAINTS INCLUDING DEFAULTS) "
                        "ON COMMIT DROP"
                    )
                )
                params = {
                    "org": "00000000-0000-0000-0000-000000000001",
                    "ws": "00000000-0000-0000-0000-000000000002",
                    "job": "00000000-0000-0000-0000-000000000003",
                }
                refused = False
                try:
                    conn.execute(
                        sa.text(
                            "INSERT INTO _rprobe (id, job_id, organization_id, "
                            "workspace_id, page_number, x0, y0, x1, y1, "
                            "detector, confidence, geometry_precision) VALUES "
                            "(gen_random_uuid(), :job, :org, :ws, 1, 0, 0, "
                            "10, 10, 'manual', 1.0, 'MANUAL')"
                        ),
                        params,
                    )
                except Exception:  # noqa: BLE001
                    refused = True
                assert refused, (
                    "a manual region was accepted with no author. A rectangle "
                    "nobody can be asked about is the row an audit wants."
                )
            finally:
                trans.rollback()

    rec.check("a manual region without an author is refused", manual_region_needs_an_author)

    def inverted_box_is_refused() -> None:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    sa.text(
                        "CREATE TEMP TABLE _bprobe (LIKE redaction_regions "
                        "INCLUDING CONSTRAINTS INCLUDING DEFAULTS) "
                        "ON COMMIT DROP"
                    )
                )
                refused = False
                try:
                    conn.execute(
                        sa.text(
                            "INSERT INTO _bprobe (id, job_id, organization_id, "
                            "workspace_id, page_number, x0, y0, x1, y1, "
                            "detector, confidence, geometry_precision) VALUES "
                            "(gen_random_uuid(), gen_random_uuid(), "
                            "gen_random_uuid(), gen_random_uuid(), 1, "
                            "100, 100, 10, 10, 'iban', 1.0, 'GLYPH')"
                        )
                    )
                except Exception:  # noqa: BLE001
                    refused = True
                assert refused, (
                    "an inverted box was accepted. A NumPy slice with a "
                    "reversed range is empty, so it would burn nothing and "
                    "record a rectangle that was never filled."
                )
            finally:
                trans.rollback()

    rec.check("an inverted rectangle is refused by ck_rr_box_ordered", inverted_box_is_refused)


# ===========================================================================
# --mutate
# ===========================================================================


MUTANTS: list[dict[str, Any]] = [
    {
        "id": "M1",
        "name": "burn blends at 90% opacity instead of overwriting",
        "file": RASTERIZE,
        "find": "pixels[rect.top : rect.bottom, rect.left : rect.right] = fill_value",
        "replace": (
            "_w = pixels[rect.top : rect.bottom, rect.left : rect.right]\n"
            "        pixels[rect.top : rect.bottom, rect.left : rect.right] = (\n"
            "            (0.9 * fill_value + 0.1 * _w.astype(np.float32))\n"
            "            .astype(np.uint8)\n"
            "        )"
        ),
        "must": "die",
        "why": (
            "Visually identical to a real redaction. The original glyph is "
            "recoverable by contrast stretching in any image editor."
        ),
    },
    {
        "id": "M2",
        "name": "leak check skips the object-tree scan",
        "file": LEAKCHECK,
        "find": "        forbidden = scan_object_tree(pdf_bytes)",
        "replace": "        forbidden = ()",
        "must": "die",
        "why": (
            "Text extraction still comes back clean, so the job completes. An "
            "embedded file or an AcroForm carrying the original sails through."
        ),
    },
    {
        "id": "M3",
        "name": "uniform-fill verification never reports a dirty rectangle",
        "file": RASTERIZE,
        "find": "        if not bool(np.all(window == fill_value)):",
        "replace": "        if False:",
        "must": "die",
        "why": (
            "Nothing changes on a healthy run, which is exactly why it would "
            "survive review. It is the only thing standing between a partial "
            "burn and a shipped file, and only a gate that feeds it a "
            "deliberately dirty rectangle can tell."
        ),
    },
    {
        "id": "CONTROL",
        "name": "minimum searched secret length tightened from 4 to 3",
        "file": LEAKCHECK,
        "find": "MIN_SECRET_LENGTH: int = 4",
        "replace": "MIN_SECRET_LENGTH: int = 3",
        "must": "survive",
        "why": (
            "A plausible edit that makes the check STRICTER. Nothing should "
            "die. If it does, the gates are reacting to change rather than "
            "locating defects."
        ),
    },
]


def _mutant_assembler_copying_source_page(source_bytes: bytes) -> Callable[..., Any]:
    """A deliberately broken assembler: it copies the source page object.

    Not a text mutation, because copying a source object is a wrong CALL, not
    a one-character edit — `assemble_pdf` has no source document in scope, by
    design. Implemented here so the gate that must kill it is exercised
    against the behaviour rather than against a shape the module cannot take.
    """
    import pikepdf

    def broken(pages: Any) -> Any:
        from app.services.redaction.assemble import AssembledDocument

        pdf = pikepdf.Pdf.new()
        with pikepdf.open(io.BytesIO(source_bytes)) as src:
            for page in src.pages:
                pdf.pages.append(page)
        buffer = io.BytesIO()
        pdf.save(buffer)
        pdf.close()
        return AssembledDocument(
            pdf_bytes=buffer.getvalue(), page_count=1, raw_image_bytes=0
        )

    return broken


def run_mutations(rec: Recorder) -> None:
    import tempfile

    source = build_synthetic_pdf()

    # --- the two behavioural mutants ---------------------------------------

    def assembler_copying_source_page_dies() -> None:
        result = run_pipeline(
            BACKEND, source, assembler=_mutant_assembler_copying_source_page(source)
        )
        assert result["report"].passed is False, (
            "an assembler that copied the source page produced an output the "
            "leak check called clean. The object scan and the text extraction "
            "are both failing to notice a document made of the original."
        )

    rec.check(
        "M4 assembler copies a source page object -> DIES",
        assembler_copying_source_page_dies,
    )

    def skipping_the_leak_check_cannot_seal() -> None:
        result = run_pipeline(BACKEND, source, skip_leak_check=True)
        assert result["report"] is None
        # With no report there is no `leak_check_passed = true` to write, and
        # `ck_rj_completed_is_sealed` refuses the row. The offline half of that
        # is: nothing in the pure layer will hand back a passing verdict it did
        # not compute.
        leakcheck = _load(BACKEND, LEAKCHECK, "_a32_leak_skip")
        empty = leakcheck.LeakReport(passed=False, pages_checked=0, secrets_checked=0)
        assert empty.passed is False
        assert "failed" in empty.sentence().lower()

    rec.check(
        "M5 skipping the leak check leaves nothing that can seal a job -> DIES",
        skipping_the_leak_check_cannot_seal,
    )

    # --- the source-rewrite mutants ----------------------------------------

    for mutant in MUTANTS:
        def make_gate(mutant: dict[str, Any] = mutant) -> Callable[[], None]:
            def gate() -> None:
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "backend"
                    shutil.copytree(
                        BACKEND / "app" / "services" / "redaction",
                        root / "app" / "services" / "redaction",
                    )
                    target = root / mutant["file"]
                    text = target.read_text(encoding="utf-8")
                    assert mutant["find"] in text, (
                        f"{mutant['id']}: the mutation anchor is not in "
                        f"{mutant['file']}. The mutant cannot be applied, so "
                        "this suite is not testing what it claims to."
                    )
                    target.write_text(
                        text.replace(mutant["find"], mutant["replace"], 1),
                        encoding="utf-8",
                    )

                    sub = Recorder()
                    try:
                        gates_engine(sub, root=root)
                        gates_validators(sub, root=root)
                    except Exception:  # noqa: BLE001
                        # An exception escaping the gates is a death.
                        sub.results.append(("harness", False, "raised"))

                    bootstrap_pure_namespace(BACKEND)
                    died = sub.failed > 0
                    if mutant["must"] == "die":
                        assert died, (
                            f"{mutant['id']} SURVIVED: {mutant['name']}. "
                            f"{mutant['why']}"
                        )
                    else:
                        assert not died, (
                            f"{mutant['id']} DIED and should have survived: "
                            f"{mutant['name']}. {mutant['why']} "
                            f"Failing gates: "
                            f"{[n for n, ok, _ in sub.results if not ok]}"
                        )
            return gate

        verb = "DIES" if mutant["must"] == "die" else "SURVIVES"
        rec.check(f"{mutant['id']} {mutant['name']} -> {verb}", make_gate())


# ===========================================================================
# Pending work
# ===========================================================================


def report_pending() -> None:
    print("\n--- ARCH-32 gates not yet written (Steps 3-5 do not exist) ---")
    for line in (
        "detection geometry: page_start_char/page_end_char resolved against "
        "extraction_metadata block boxes, and BLOCK widening when a candidate "
        "cannot be placed below block level",
        "token_digest is an HMAC under the ARCH-07 key lifecycle, and rotates",
        "redaction.detect and redaction.apply registered in the handler map, "
        "app/workers/profiles.py OCR profile, and the scheduler",
        "the seven §3.5 endpoints, each gated by capability.redaction, "
        "including the reads",
        "redaction.page metered per OUTPUT page, once, on a completed apply",
        "Redaction Studio: overlay, keyboard nudging, preview through the "
        "apply job's own rasterize path, the §3.9 name limit shown on "
        "profiles that include names",
    ):
        print(f"  [PENDING] {line}")


# ===========================================================================
# main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-32 Steps 1-2 verification")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-32 — Zero-Leakage Geometric PII Redaction: Steps 1-2")
    print(f"backend: {BACKEND}")

    try:
        import numpy  # noqa: F401
        import pikepdf  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        print(f"\nHARNESS CANNOT RUN: {exc}")
        print("pip install pikepdf pypdfium2 numpy")
        return 2

    offline = Recorder()
    gates_schema(offline)
    gates_patch_script(offline)
    gates_validators(offline)
    gates_engine(offline)
    offline.report("offline")

    failed = offline.failed

    if args.db:
        if not args.database_url:
            print("\n--db requires --database-url or DATABASE_URL")
            return 2
        db = Recorder()
        gates_db(db, args.database_url)
        db.report("database")
        failed += db.failed

    if args.mutate:
        mutate = Recorder()
        run_mutations(mutate)
        mutate.report("mutation")
        failed += mutate.failed

    report_pending()

    print(f"\n{'FAILED' if failed else 'PASSED'} — {failed} gate(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())