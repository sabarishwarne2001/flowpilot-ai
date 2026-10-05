"""Payment risk on invoices, live: a vendor's bank account changed; a round total.

Three invoices from one vendor arrive in order:
1. pays into account ...5432, total 1,234.56 - nothing to flag;
2. pays into account ...5555, total 50,000.00 - BANK_ACCOUNT_CHANGED (HIGH,
   pointing at invoice 1, accounts masked to their last four) and
   ROUND_AMOUNT (LOW);
3. pays into ...5555 again - the account is now the known one: no new flag.

Then the review rules: a VIEWER reads but may not decide; a dismissal needs a
reason of 10+ characters; a decided flag is not decided twice; reprocessing a
document never duplicates or reopens a flag; another workspace sees nothing;
a plan without the radar is refused (402). No full account number is ever
returned.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain
from tests.engines.isolation import assert_workspace_isolated

OLD_IBAN, NEW_IBAN = "GB82 WEST 1234 5698 7654 32", "GB33 BUKB 2020 1555 5555 55"


def _invoice(engines: Engines, n: int, iban: str, total: str) -> str:
    number = f"INV-PR-{n}"
    lines = ["ACME SUPPLIES LTD", "TAX INVOICE", f"Invoice No: {number}", f"Pay to IBAN: {iban}", f"Total: {total} GBP"]
    return str(engines.process(f"{number}.pdf", [lines], marker=number, classification="Invoice",
                               entities={"vendor_name": "Acme Supplies Ltd", "invoice_number": number,
                                         "iban": iban, "total_amount": total, "currency": "GBP"}))


def test_a_changed_bank_account_and_a_round_total_are_flagged(engines: Engines) -> None:
    engines.plan("business")
    first = _invoice(engines, 1, OLD_IBAN, "1234.56")
    second = _invoice(engines, 2, NEW_IBAN, "50000.00")
    _invoice(engines, 3, NEW_IBAN, "640.10")

    listed = engines.get("/payment-risk", as_user=engines.tenant.viewer)
    assert listed.status_code == 200, listed.text
    flags = {(f["work_item_id"], f["kind"]): f for f in listed.json()["items"]}
    assert set(flags) == {(second, "BANK_ACCOUNT_CHANGED"), (second, "ROUND_AMOUNT")}, sorted(flags)
    changed = flags[(second, "BANK_ACCOUNT_CHANGED")]
    assert changed["severity"] == "HIGH" and changed["counterpart_work_item_id"] == first
    assert changed["details"]["previous_account"]["last4"] == "5432"
    assert changed["details"]["new_account"]["last4"] == "5555"
    assert "5555" in changed["summary"] and "5432" in changed["summary"]
    compact = [OLD_IBAN.replace(" ", ""), NEW_IBAN.replace(" ", "")]
    assert not any(account in listed.text for account in compact + [OLD_IBAN, NEW_IBAN]), "an account leaked"
    assert flags[(second, "ROUND_AMOUNT")]["severity"] == "LOW"

    flag_id = changed["id"]
    assert engines.post(f"/payment-risk/{flag_id}/confirm", as_user=engines.tenant.viewer).status_code == 403
    short = engines.post(f"/payment-risk/{flag_id}/dismiss", {"reason": "fine"}, as_user=engines.tenant.contributor)
    assert short.status_code == 422, short.text
    confirmed = engines.post(f"/payment-risk/{flag_id}/confirm", as_user=engines.tenant.contributor)
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "CONFIRMED", confirmed.text
    again = engines.post(f"/payment-risk/{flag_id}/dismiss", {"reason": "the vendor confirmed by phone"},
                         as_user=engines.tenant.contributor)
    assert again.status_code == 409, again.text
    round_id = flags[(second, "ROUND_AMOUNT")]["id"]
    dismissed = engines.post(f"/payment-risk/{round_id}/dismiss", {"reason": "annual retainer, fixed fee"},
                             as_user=engines.tenant.contributor)
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "DISMISSED"

    assert engines.post(f"/work-items/{second}/reprocess").status_code == 202
    drain()
    after = engines.get("/payment-risk").json()
    assert after["counts_by_status"] == {"CONFIRMED": 1, "DISMISSED": 1}, after["counts_by_status"]

    assert_workspace_isolated(engines, collections=("payment-risk",))

    engines.plan("free")
    assert engines.get("/payment-risk").status_code == 402
