"""The Time machine replay as a PDF report (`helm/backtest_report.py`), read back with pypdf.

Honest by construction: it says it is a replay of what Helm would have done, labels the sample
company, shows each confidence level on its own and never a sum across them, names the documents
each finding rests on, says what Helm could not see, and prints the stored result's SHA-256 on every
page. Deterministic (the same replay gives the same bytes) and quick even at the most findings a
stored result can list. Pure: no database.
"""

from __future__ import annotations

import copy
import io
import time

import pytest
from pypdf import PdfReader

from app.services.helm import backtest, backtest_report as report
from app.services.helm import vocabulary as v

pytestmark = pytest.mark.no_db

M = 1_000_000


def finding(n: int, honesty: str, rupees: int, *, mandate: str = "never_pay_above_agreed_price",
            on: str = "2026-06-03", evidence: list | None = None) -> dict:
    return {"mandate": mandate, "on": on, "action": "hold_payment", "honesty": honesty,
            "summary": f"Hold KP-{n:04d} from Konkan Packaging: billed ₹{rupees:,} above the agreed price.",
            "category": "OVERCHARGE_RECOVERED", "currency": "INR", "value_micros": rupees * M,
            "evidence": evidence if evidence is not None else [
                {"work_item_id": f"00000000-0000-4000-8000-{n:012d}", "document": f"kp-{n}.pdf",
                 "document_number": f"KP-{n:04d}"}, {"po_number": f"PO-{n:03d}", "line": "Boxes"}]}


def a_result() -> dict:
    """Confirmed 1,00,000 (60,000 + 40,000), likely 20,000, potential 3,000: no amount anywhere equals
    a sum across levels (1,23,000; 1,20,000; 1,03,000; 23,000)."""
    price = [finding(1, v.HONESTY_CONFIRMED, 60_000), finding(2, v.HONESTY_CONFIRMED, 40_000),
             finding(3, v.HONESTY_LIKELY, 20_000)]
    renewal = [finding(4, v.HONESTY_POTENTIAL, 3_000, mandate="decide_renewals_before_notice", on="2026-08-11",
                       evidence=[{"obligation_id": "o-1", "work_item_id": "11111111-2222-4333-8444-555555555555"}])]
    mandates = [
        {"template": "never_pay_above_agreed_price", "title": "Never pay above the agreed price", "count": 3,
         "truncated": False, "findings": price,
         "totals": {"confirmed": {"INR": 100_000 * M}, "likely": {"INR": 20_000 * M}, "potential": {}}},
        {"template": "decide_renewals_before_notice", "title": "Decide renewals before the notice date", "count": 1,
         "truncated": False, "findings": renewal,
         "totals": {"confirmed": {}, "likely": {}, "potential": {"INR": 3_000 * M}}},
        {"template": "pay_on_time", "title": "Pay on time", "count": 0, "truncated": False, "findings": [],
         "totals": {"confirmed": {}, "likely": {}, "potential": {}}},
    ]
    return {"period": {"start": "2026-04-16", "end": "2026-10-16"}, "mandates": mandates,
            "totals": {"confirmed": {"INR": 100_000 * M}, "likely": {"INR": 20_000 * M},
                       "potential": {"INR": 3_000 * M}},
            "by_category": {}, "counts": {"OVERCHARGE_RECOVERED": 3, "RENEWAL_MISSED": 1}, "timeline": [],
            "cash_history": {},
            "visibility": {"payables": {"seen": 52, "with_key_facts": 40, "share": 0.769, "verified_share": 0.5},
                           "suppliers": {"seen": 14, "with_terms": 10, "share": 0.714},
                           "contracts": {"seen": 0, "dated": 0, "share": None}, "overall": 0.742},
            "honesty_rule": "never added", "duration_ms": 812}


def a_bundle(**changes) -> dict:
    result = changes.pop("result", None) or a_result()
    bundle = {"id": "6b1f0d8e-1d2c-4b7a-9f00-0c1d2e3f4a5b", "book_id": "b", "book_name": "Konkan Traders Pvt Ltd",
              "book_kind": v.BOOK_WORKSPACE, "workspace_name": "Konkan Traders Pvt Ltd", "sample": False,
              "preview": False, "months": 6, "period_start": "2026-04-16", "period_end": "2026-10-16",
              "created_at": "2026-10-16T09:29:58+00:00", "started_at": "2026-10-16T09:30:00+00:00",
              "finished_at": "2026-10-16T09:30:12.512+00:00",
              "summary": backtest.summarise(result, months=6), "readable": True,
              "mandates": [{"title": "Never pay above the agreed price", "version": 2},
                           {"title": "Decide renewals before the notice date", "version": None},
                           {"title": "Pay on time", "version": 1}],
              "result": result, "result_sha256": report.result_sha256(result),
              "document_names": {"11111111-2222-4333-8444-555555555555": "Warehouse lease 2025.pdf"}}
    bundle.update(changes)
    return bundle


def text_of(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_the_report_says_what_it_is_and_shows_each_confidence_level_on_its_own():
    bundle = a_bundle()
    data = report.to_pdf(bundle)
    assert data.startswith(b"%PDF-")
    text = text_of(data)
    # What it is: a replay of what Helm would have done, of this company, over this window.
    assert "A replay of what Helm WOULD have done, not a record of what happened." in text
    assert "Nothing was executed" in text
    assert "Konkan Traders Pvt Ltd" in text
    assert "16 Apr 2026 to 16 Oct 2026 (6 months)" in text
    assert "16 Oct 2026, 09:30 UTC" in text
    assert "Sample company" not in text and "SAMPLE COMPANY" not in text
    # Each level on its own, with its own amount right under its name.
    assert "CONFIRMED\nINR 1,00,000.00" in text
    assert "LIKELY\nINR 20,000.00" in text
    assert "POTENTIAL\nINR 3,000.00" in text
    assert "never added together" in text
    # ...and never a sum across them, anywhere.
    for added in ("1,23,000", "1,20,000", "1,03,000", "23,000"):
        assert added not in text, added
    # The mandates replayed, each with its version or as a draft.
    assert "Never pay above the agreed price\nversion 2" in text
    assert "Decide renewals before the notice date\ndraft, with the gallery's settings" in text
    assert "Pay on time\nversion 1" in text
    # Each finding: its amount, its level, its mandate (the section) and the documents it rests on.
    assert "Never pay above the agreed price: 3 findings" in text
    assert "KP-0001, PO-001" in text and "INR 60,000.00" in text and "INR 40,000.00" in text
    assert "Decide renewals before the notice date: 1 finding" in text
    assert "Warehouse lease 2025.pdf" in text  # a document without a number, by its name
    # What Helm could not see.
    assert "What Helm could not see" in text
    assert "74% of the business overall" in text and "40 of 52 open payables" in text
    assert "nothing seen yet" in text
    assert "No cash balance was recorded in this period" in text
    assert "Potential amounts rest on facts Helm did not have" in text
    # The rupee sign the font cannot draw is the currency code, never a black box.
    assert "₹" not in text and "■" not in text
    assert "billed INR 60,000 above the agreed price" in " ".join(text.split())  # wrapped in its cell
    # Every page carries its number and the stored result's fingerprint.
    pages = PdfReader(io.BytesIO(data)).pages
    for number, page in enumerate(pages, start=1):
        words = page.extract_text()
        assert f"Page {number} of {len(pages)}" in words
        assert f"SHA-256 of the stored result: {bundle['result_sha256']}" in words
        assert "what Helm would have done, not what happened" in words


def test_the_sample_company_and_helm_preview_are_labelled():
    text = text_of(report.to_pdf(a_bundle(sample=True, book_kind=v.BOOK_SAMPLE, preview=True,
                                          book_name="Meridian Wholesale (sample)", workspace_name=None)))
    assert "SAMPLE COMPANY" in text
    assert "Sample company: an invented business for exploring Helm." in text
    assert "Sample company replay 6b1f0d8e" in text
    assert "Helm preview: up to 3 months, once a week" in text


def test_a_reader_who_may_not_see_the_workspace_gets_the_totals_without_the_findings():
    full = a_result()
    masked = dict(full, mandates=[{**m, "findings": []} for m in full["mandates"]], timeline=[])
    text = text_of(report.to_pdf(a_bundle(readable=False, result=masked, document_names={},
                                          result_sha256=report.result_sha256(full))))
    assert "CONFIRMED\nINR 1,00,000.00" in text
    assert "KP-0001" not in text and "Konkan Packaging" not in text and "Warehouse lease" not in text
    assert "The findings are not listed" in text
    assert report.result_sha256(full) in text


def test_the_same_replay_gives_the_same_bytes():
    bundle = a_bundle()
    first = report.to_pdf(bundle)
    assert report.to_pdf(copy.deepcopy(bundle)) == first
    # The order a JSON column hands keys back in changes nothing.
    shuffled = copy.deepcopy(bundle)
    shuffled["result"] = {k: shuffled["result"][k] for k in reversed(list(shuffled["result"]))}
    totals = shuffled["result"]["totals"]
    shuffled["result"]["totals"] = {k: totals[k] for k in reversed(list(totals))}
    assert report.result_sha256(shuffled["result"]) == bundle["result_sha256"]
    assert report.to_pdf(shuffled) == first
    # The creation date is the moment the replay finished, not the time of the download.
    info = PdfReader(io.BytesIO(first)).metadata
    assert info["/CreationDate"] == "D:20261016093012+00'00'"
    assert "Konkan Traders Pvt Ltd" in info["/Title"]
    # A different replay is a different report.
    assert report.to_pdf(a_bundle(finished_at="2026-10-17T10:00:00+00:00")) != first


def test_hundreds_of_findings_render_within_the_time_budget():
    """The most a stored result lists: 200 findings for each of the 13 library mandates."""
    levels = v.HONESTY_CLASSES
    mandates = []
    for m in range(13):
        findings = [finding(m * 1000 + i, levels[i % 3], 1_000 + i, on=f"2026-{5 + i % 5:02d}-{1 + i % 28:02d}")
                    for i in range(backtest.MAX_FINDINGS_PER_MANDATE)]
        mandates.append({"template": f"t{m}", "title": f"Mandate {m}", "count": 260, "truncated": True,
                         "findings": findings, "totals": {"confirmed": {"INR": 9 * M}, "likely": {}, "potential": {}}})
    result = dict(a_result(), mandates=mandates)
    bundle = a_bundle(result=result, result_sha256=report.result_sha256(result), mandates=[])
    started = time.perf_counter()
    data = report.to_pdf(bundle)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"{elapsed:.2f} s for {13 * backtest.MAX_FINDINGS_PER_MANDATE} findings"
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) > 50
    last = reader.pages[-1].extract_text()
    assert f"Page {len(reader.pages)} of {len(reader.pages)}" in last
    assert "\"Mandate 3\" has 260 findings; the first 200 are listed below" in text_of(data)
    # A table cut by a page break carries its mandate and its column names onto the next page.
    assert "Mandate 0 (continued)" in reader.pages[3].extract_text()


def test_money_is_written_like_the_screens_with_the_code_where_the_font_has_no_symbol():
    assert report.amount(123_456_780_000, "INR") == "INR 1,23,456.78"
    assert report.amount(12_345_678_900_000, "INR") == "INR 1,23,45,678.90"
    assert report.amount(999 * M, "INR") == "INR 999.00"
    assert report.amount(-1_500 * M, "INR") == "-INR 1,500.00"
    assert report.amount(1_234_500_000, "USD") == "$1,234.50"
    assert report.amount(1_234_500_000, "EUR") == "€1,234.50"
    assert report.amount(1_234_500_000, "AED") == "AED 1,234.50"
    assert report.amount(1_234_500_000, None) == "1,234.50"
    assert report.amount(None, "INR") == "-"
    assert report.amount(5, "INR") == "INR 0.00"  # half-even to the paisa, never a float
    assert not report.drawable("₹") and report.drawable("€")
    assert report.clean("₹2.4 lakh → held") == "INR 2.4 lakh -> held"
    assert report.clean("मुंबई") == "?????"


def test_the_file_name_is_plain_ascii():
    name = report.filename("Société Générale — मुंबई", "2026-04-16", "2026-10-16")
    assert name == "helm-replay-societe-generale-2026-04-16-to-2026-10-16.pdf"
    assert name.isascii() and '"' not in name
    assert report.filename("मुंबई", "2026-04-16", "2026-10-16") == "helm-replay-book-2026-04-16-to-2026-10-16.pdf"
