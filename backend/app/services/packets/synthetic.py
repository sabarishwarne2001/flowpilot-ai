"""ARCH43-S1:synthetic — labelled synthetic scanned packets. Pure; seeded.

Used by scripts/fit_packet_boundaries.py (training seeds) and by
verify_arch43 T4 (held-out seeds). Each packet is a list of page texts and
the set of pages (1-based) on which a new document starts.

The generator is built to be HARD in the ways real bundles are:
  * runs of documents of the same type from the same issuer (same letterhead,
    same title on every page) where only a page-number reset or a changed
    document number marks the boundary;
  * documents with no page numbers at all;
  * duplex blank back sides inside documents and separator sheets between
    them (neither may start a document);
  * repeated letterheads on continuation pages, and OCR noise.
"""

from __future__ import annotations

import random
from typing import Optional

COMPANIES = ["Acme Supplies Pvt Ltd", "Globex Logistics", "Initech Components", "Contoso Traders", "Umbrella Pharma",
             "Northwind Foods", "Hooli Networks", "Stark Metals", "Wayne Freight", "Soylent Agro", "Tyrell Textiles",
             "Cyberdyne Systems", "Vandelay Imports", "Wonka Confectionery", "Oscorp Chemicals"]
PEOPLE = ["Ravi Kumar", "Priya Sharma", "Arjun Nair", "Meera Iyer", "Sanjay Rao", "Anita Desai", "John Smith",
          "Fatima Khan", "Wei Chen", "Divya Menon"]
CITIES = ["Chennai", "Mumbai", "Madurai", "Pune", "Bengaluru", "Rotterdam", "Singapore", "Dubai"]

# type -> (title, number label or None, body line makers, issuer kind, page range)
TYPES: dict[str, tuple] = {
    "invoice": ("TAX INVOICE", "Invoice No", "items", "org", (1, 3)),
    "purchase_order": ("PURCHASE ORDER", "PO No", "items", "org", (1, 3)),
    "goods_receipt": ("GOODS RECEIPT NOTE", "GRN No", "items", "org", (1, 2)),
    "delivery_note": ("DELIVERY CHALLAN", "Challan No", "items", "org", (1, 2)),
    "credit_note": ("CREDIT NOTE", "Credit Note No", "items", "org", (1, 1)),
    "statement": ("STATEMENT OF ACCOUNT", "Account No", "ledger", "org", (1, 3)),
    "msa": ("MASTER SERVICES AGREEMENT", "Contract No", "clauses", "org", (3, 8)),
    "nda": ("MUTUAL NON-DISCLOSURE AGREEMENT", None, "clauses", "org", (2, 4)),
    "lease": ("LEASE DEED", None, "lease", "person", (2, 5)),
    "resume": ("CURRICULUM VITAE", None, "cv", "person", (1, 3)),
    "offer_letter": ("OFFER OF EMPLOYMENT", "Ref No", "letter", "org", (1, 2)),
    "discharge_summary": ("DISCHARGE SUMMARY", "IP No", "clinical", "person", (1, 3)),
    "bill_of_lading": ("BILL OF LADING", "B/L No", "shipping", "org", (1, 2)),
    "customs_manifest": ("CUSTOMS MANIFEST", "Reference No", "shipping", "org", (1, 2)),
    "passport": ("PASSPORT", None, "id", "person", (1, 1)),
    "india_id_card": ("AADHAAR - UNIQUE IDENTIFICATION AUTHORITY OF INDIA", None, "id", "person", (1, 1)),
    "letter": ("", None, "letter", "org", (1, 2)),
}

_WORDS = ("the parties shall comply with the obligations set out herein and any amendment agreed in writing "
          "payment shall be made within thirty days of receipt of a valid invoice subject to deduction of tax "
          "confidential information shall not be disclosed to any third party without prior written consent "
          "the tenant shall pay the monthly rent on or before the fifth day of each calendar month "
          "goods were inspected on arrival and found in good order except as noted below").split()


def _sentence(r: random.Random, n: int = 12) -> str:
    return " ".join(r.choice(_WORDS) for _ in range(n)).capitalize() + "."


def _body(r: random.Random, kind: str, lines: int) -> list[str]:
    out: list[str] = []
    for i in range(lines):
        if kind == "items":
            out.append(f"{i + 1} {r.choice(['Steel bolts M8', 'Copper wire 2mm', 'Packing tape', 'Valve assembly', 'Resin 20kg', 'Bearing 6204'])} "
                       f"{r.randrange(1, 500)} {r.randrange(10, 9000)}.{r.randrange(10, 99)}")
        elif kind == "ledger":
            out.append(f"{r.randrange(1, 28):02d}/0{r.randrange(1, 9)}/2026 INV-{r.randrange(1000, 9999)} {r.randrange(100, 90000)}.00")
        elif kind == "clauses":
            out.append(f"{r.randrange(1, 30)}.{r.randrange(1, 9)} {_sentence(r, 16)}")
        elif kind == "lease":
            out.append(_sentence(r, 14))
        elif kind == "cv":
            out.append(r.choice(["Work experience", "Education", "Skills", "Projects"]) + ": " + _sentence(r, 8))
        elif kind == "clinical":
            out.append(r.choice(["Diagnosis", "Treatment", "Medication", "Advice", "Follow-up"]) + ": " + _sentence(r, 8))
        elif kind == "shipping":
            out.append(r.choice(["Shipper", "Consignee", "Port of loading", "Port of discharge", "Container", "HS code"]) + ": "
                       + r.choice(COMPANIES + CITIES))
        elif kind == "id":
            out.append(r.choice(["Name", "Date of birth", "Date of expiry", "Nationality", "Address"]) + ": " + r.choice(PEOPLE + CITIES))
        else:
            out.append(_sentence(r, 12))
    return out


def _noise(r: random.Random, text: str, rate: float) -> str:
    if rate <= 0:
        return text
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch.isalnum() and r.random() < rate:
            chars[i] = r.choice("0O1lI5S8B ")
    return "".join(chars)


def _document(r: random.Random, doc_type: str, issuer: str, number: str) -> list[str]:
    title, label, kind, _, (lo, hi) = TYPES[doc_type]
    pages = r.randint(lo, hi)
    style = r.choices(["none", "page_of", "slash", "page"], weights=[40, 30, 15, 15])[0]
    at_top = r.random() < 0.3
    repeat_header = r.random() < 0.6
    repeat_title = r.random() < 0.45
    out: list[str] = []
    for k in range(1, pages + 1):
        lines: list[str] = []
        if k == 1 or repeat_header:
            lines += [issuer.upper(), f"{r.randrange(1, 300)} Industrial Estate, {r.choice(CITIES)}"]
        if title and (k == 1 or repeat_title):
            lines.append(title if k == 1 else f"{title} (continued)" if r.random() < 0.5 else title)
        if label and (k == 1 or repeat_header):
            lines.append(f"{label}: {number}")
        if k == 1 and doc_type == "letter":
            lines.append(f"Dear {r.choice(PEOPLE)},")
        lines += _body(r, kind, r.randint(6, 18))
        marker = {"none": None, "page_of": f"Page {k} of {pages}", "slash": f"{k}/{pages}", "page": f"Page {k}"}[style]
        if marker:
            lines = [marker] + lines if at_top else lines + [marker]
        out.append("\n".join(lines))
    return out


def packet(seed: int, documents: Optional[int] = None, noise: float = 0.015) -> tuple[list[str], set[int]]:
    r = random.Random(seed)
    count = documents or r.randint(3, 14)
    texts: list[str] = []
    starts: set[int] = set()
    prev: Optional[tuple[str, str]] = None
    for d in range(count):
        if prev is not None and r.random() < 0.3:
            doc_type, issuer = prev  # a run: same type, same issuer, same letterhead
        else:
            doc_type = r.choice(list(TYPES))
            issuer = r.choice(PEOPLE) if TYPES[doc_type][3] == "person" else r.choice(COMPANIES)
        number = f"{doc_type[:3].upper()}-{r.randrange(2024, 2027)}-{r.randrange(100, 99999)}"
        prev = (doc_type, issuer)
        if d > 0 and r.random() < 0.06:
            texts.append("DOCUMENT SEPARATOR SHEET\nPATCH T")
        starts.add(len(texts) + 1)
        for page in _document(r, doc_type, issuer, number):
            texts.append(_noise(r, page, noise))
            if r.random() < 0.08:
                texts.append(r.choice(["", " ", ". ,", "\n"]))  # a duplex back side
    return texts, starts


def corpus(seeds: range) -> list[tuple[list[str], set[int]]]:
    return [packet(s) for s in seeds]


TRAIN_SEEDS = range(1000, 1400)
HELD_OUT_SEEDS = range(90000, 90150)

__all__ = ["HELD_OUT_SEEDS", "TRAIN_SEEDS", "TYPES", "corpus", "packet"]
