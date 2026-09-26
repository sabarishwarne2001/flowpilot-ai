"""ARCH47-S1:x12 — ASC X12 004010: 810 (invoice), 850 (purchase order), 856 (ship notice) and the 997.

The X12 standards are licensed by ASC X12 and no schema is freely published.
SPEC below encodes, element by element, the 004010 segments FlowPilot writes --
reference, data type (AN ID N0 N2 R DT TM), minimum and maximum length,
mandatory or optional, code values -- and GRAMMAR the order of the segments in
each transaction set, transcribed from public 004010 implementation guides.
`validate()` re-parses a file with an independent reader and checks all of it
plus the envelope arithmetic: ISA exactly 106 characters with its fixed-width
elements, GS06 = GE02, ST02 = SE02, SE01 = the segments from ST to SE, GE01 = the
transaction sets, IEA01 = the groups, IEA02 = ISA13, CTT01 = the line items (810,
850) or HL segments (856).

Control numbers come from the target's own sequence (erp_targets.control_sequence,
allocated under a row lock when a posting is planned), so a retry re-sends the
SAME interchange -- which is what lets a partner's 997 name the posting it
acknowledges, and lets a partner discard a duplicate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Optional

from app.services.erp import vocabulary as v
from app.services.erp.mapping import Contract, Record

MEDIA_TYPE = "application/edi-x12"
VERSION = "00401"
GS_VERSION = "004010"
TRANSACTION = {v.OBJECT_VENDOR_BILL: "810", v.OBJECT_PURCHASE_ORDER: "850", v.OBJECT_GOODS_RECEIPT: "856"}
FUNCTIONAL_ID = {"810": "IN", "850": "PO", "856": "SH", "997": "FA"}
SUPPORTED = tuple(TRANSACTION)

UNIT_CODES = frozenset({"EA", "CA", "BX", "PK", "PC", "KG", "LB", "GR", "LT", "GA", "FT", "MR", "M", "YD", "DZ",
                        "HR", "DA", "MO", "ST", "RL", "SH", "TN", "PR", "UN", "LO"})
QUALIFIERS_236 = frozenset({"VP", "BP", "VN", "UP", "IN", "SK", "EN", "MG", "CB"})

# (id, type, min, max, mandatory, codes)
E = tuple
SPEC: dict[str, tuple[E, ...]] = {
    "ISA": (("ISA01", "ID", 2, 2, True, ("00", "03")), ("ISA02", "AN", 10, 10, True, None),
            ("ISA03", "ID", 2, 2, True, ("00", "01")), ("ISA04", "AN", 10, 10, True, None),
            ("ISA05", "ID", 2, 2, True, ("01", "08", "12", "14", "16", "ZZ")), ("ISA06", "AN", 15, 15, True, None),
            ("ISA07", "ID", 2, 2, True, ("01", "08", "12", "14", "16", "ZZ")), ("ISA08", "AN", 15, 15, True, None),
            ("ISA09", "DT", 6, 6, True, None), ("ISA10", "TM", 4, 4, True, None), ("ISA11", "ID", 1, 1, True, ("U",)),
            ("ISA12", "ID", 5, 5, True, (VERSION,)), ("ISA13", "N0", 9, 9, True, None),
            ("ISA14", "ID", 1, 1, True, ("0", "1")), ("ISA15", "ID", 1, 1, True, ("P", "T")),
            ("ISA16", "AN", 1, 1, True, None)),
    "GS": (("GS01", "ID", 2, 2, True, ("IN", "PO", "SH", "FA")), ("GS02", "AN", 2, 15, True, None),
           ("GS03", "AN", 2, 15, True, None), ("GS04", "DT", 8, 8, True, None), ("GS05", "TM", 4, 8, True, None),
           ("GS06", "N0", 1, 9, True, None), ("GS07", "ID", 1, 2, True, ("X",)),
           ("GS08", "AN", 1, 12, True, (GS_VERSION,))),
    "ST": (("ST01", "ID", 3, 3, True, ("810", "850", "856", "997")), ("ST02", "AN", 4, 9, True, None)),
    "SE": (("SE01", "N0", 1, 10, True, None), ("SE02", "AN", 4, 9, True, None)),
    "GE": (("GE01", "N0", 1, 6, True, None), ("GE02", "N0", 1, 9, True, None)),
    "IEA": (("IEA01", "N0", 1, 5, True, None), ("IEA02", "N0", 9, 9, True, None)),
    "BIG": (("BIG01", "DT", 8, 8, True, None), ("BIG02", "AN", 1, 22, True, None), ("BIG03", "DT", 8, 8, False, None),
            ("BIG04", "AN", 1, 22, False, None)),
    "CUR": (("CUR01", "ID", 2, 3, True, ("SE", "BY", "BT")), ("CUR02", "ID", 3, 3, True, None)),
    "N1": (("N101", "ID", 2, 3, True, ("SE", "BT", "BY", "ST", "SF", "SU", "VN")), ("N102", "AN", 1, 60, False, None),
           ("N103", "ID", 1, 2, False, ("1", "9", "91", "92", "ZZ")), ("N104", "AN", 2, 80, False, None)),
    "ITD": (("ITD01", "ID", 2, 2, False, ("01", "05", "14")), ("ITD02", "ID", 1, 2, False, ("1", "2", "3", "15")),
            ("ITD03", "R", 1, 6, False, None), ("ITD04", "DT", 8, 8, False, None), ("ITD05", "N0", 1, 3, False, None),
            ("ITD06", "DT", 8, 8, False, None), ("ITD07", "N0", 1, 3, False, None)),
    "IT1": (("IT101", "AN", 1, 20, False, None), ("IT102", "R", 1, 10, True, None),
            ("IT103", "ID", 2, 2, True, tuple(sorted(UNIT_CODES))), ("IT104", "R", 1, 17, True, None),
            ("IT105", "ID", 2, 2, False, None), ("IT106", "ID", 2, 2, False, tuple(sorted(QUALIFIERS_236))),
            ("IT107", "AN", 1, 48, False, None)),
    "PID": (("PID01", "ID", 1, 1, True, ("F",)), ("PID02", "ID", 2, 3, False, None), ("PID03", "ID", 2, 2, False, None),
            ("PID04", "AN", 1, 12, False, None), ("PID05", "AN", 1, 80, True, None)),
    "TDS": (("TDS01", "N2", 1, 15, True, None),),
    "TXI": (("TXI01", "ID", 2, 2, True, ("TX", "GS", "VA", "ST")), ("TXI02", "R", 1, 18, True, None)),
    "CTT": (("CTT01", "N0", 1, 6, True, None),),
    "BEG": (("BEG01", "ID", 2, 2, True, ("00", "05", "07")), ("BEG02", "ID", 2, 2, True, ("SA", "NE", "KN", "RL")),
            ("BEG03", "AN", 1, 22, True, None), ("BEG04", "AN", 1, 30, False, None), ("BEG05", "DT", 8, 8, True, None)),
    "PO1": (("PO101", "AN", 1, 20, False, None), ("PO102", "R", 1, 15, True, None),
            ("PO103", "ID", 2, 2, True, tuple(sorted(UNIT_CODES))), ("PO104", "R", 1, 17, False, None),
            ("PO105", "ID", 2, 2, False, None), ("PO106", "ID", 2, 2, False, tuple(sorted(QUALIFIERS_236))),
            ("PO107", "AN", 1, 48, False, None)),
    "AMT": (("AMT01", "ID", 1, 3, True, ("TT", "1", "GV")), ("AMT02", "R", 1, 18, True, None)),
    "BSN": (("BSN01", "ID", 2, 2, True, ("00", "05")), ("BSN02", "AN", 2, 30, True, None),
            ("BSN03", "DT", 8, 8, True, None), ("BSN04", "TM", 4, 8, True, None)),
    "HL": (("HL01", "AN", 1, 12, True, None), ("HL02", "AN", 1, 12, False, None),
           ("HL03", "ID", 1, 2, True, ("S", "O", "P", "I")), ("HL04", "ID", 1, 1, False, ("0", "1"))),
    "DTM": (("DTM01", "ID", 3, 3, True, ("011", "017", "002")), ("DTM02", "DT", 8, 8, True, None)),
    "PRF": (("PRF01", "AN", 1, 22, True, None),),
    "LIN": (("LIN01", "AN", 1, 20, False, None), ("LIN02", "ID", 2, 2, True, tuple(sorted(QUALIFIERS_236))),
            ("LIN03", "AN", 1, 48, True, None)),
    "SN1": (("SN101", "AN", 1, 20, False, None), ("SN102", "R", 1, 10, True, None),
            ("SN103", "ID", 2, 2, True, tuple(sorted(UNIT_CODES)))),
    "AK1": (("AK101", "ID", 2, 2, True, ("IN", "PO", "SH")), ("AK102", "N0", 1, 9, True, None)),
    "AK2": (("AK201", "ID", 3, 3, True, ("810", "850", "856")), ("AK202", "AN", 4, 9, True, None)),
    "AK3": (("AK301", "ID", 2, 3, True, None), ("AK302", "N0", 1, 6, True, None), ("AK303", "AN", 1, 6, False, None),
            ("AK304", "ID", 1, 3, False, None)),
    "AK4": (("AK401", "AN", 1, 6, True, None), ("AK402", "N0", 1, 4, False, None), ("AK403", "ID", 1, 3, True, None),
            ("AK404", "AN", 1, 99, False, None)),
    "AK5": (("AK501", "ID", 1, 1, True, ("A", "E", "M", "R", "W", "X")), ("AK502", "ID", 1, 3, False, None)),
    "AK9": (("AK901", "ID", 1, 1, True, ("A", "E", "M", "P", "R", "W", "X")), ("AK902", "N0", 1, 6, True, None),
            ("AK903", "N0", 1, 6, True, None), ("AK904", "N0", 1, 6, True, None)),
}
#: Segment order inside each transaction set (ST .. SE), as a regular expression over segment ids.
GRAMMAR = {
    "810": r"ST BIG (CUR )?(N1 )+(ITD )?(DTM )?(IT1 (PID )?)+TDS (TXI )?(CTT )?SE ",
    "850": r"ST BEG (CUR )?(N1 )+(PO1 (PID )?)+(CTT )?(AMT )?SE ",
    "856": r"ST BSN HL (DTM )?(N1 )*HL PRF (HL LIN SN1 (PID )?)+CTT SE ",
    "997": r"ST AK1 (AK2 (AK3 (AK4 )*)*AK5 )*AK9 SE ",
}

_CONTRACT_810 = Contract(
    header={"invoice_number": True, "invoice_date": True, "po_number": False, "po_date": False, "currency": False,
            "seller_name": True, "seller_id": False, "buyer_name": True, "buyer_id": False, "terms_net_days": False,
            "terms_due_date": False, "total_amount": True, "tax_amount": False},
    lines={"line_number": False, "quantity": True, "unit": True, "unit_price": True, "product_id": False,
           "product_qualifier": False, "description": False},
    open=False, needs_lines=True)
_CONTRACT_850 = Contract(
    header={"po_number": True, "po_date": True, "currency": False, "buyer_name": True, "buyer_id": False,
            "seller_name": True, "seller_id": False, "total_amount": False},
    lines={"line_number": False, "quantity": True, "unit": True, "unit_price": False, "product_id": False,
           "product_qualifier": False, "description": False},
    open=False, needs_lines=True)
_CONTRACT_856 = Contract(
    header={"shipment_id": True, "ship_date": True, "po_number": True, "ship_from_name": False, "ship_to_name": False},
    lines={"line_number": False, "product_id": True, "product_qualifier": False, "quantity": True, "unit": True,
           "description": False},
    open=False, needs_lines=True)
CONTRACTS = {v.OBJECT_VENDOR_BILL: _CONTRACT_810, v.OBJECT_PURCHASE_ORDER: _CONTRACT_850,
             v.OBJECT_GOODS_RECEIPT: _CONTRACT_856}


class X12Error(ValueError):
    pass


@dataclass(frozen=True)
class Envelope:
    sender_qualifier: str = "ZZ"
    sender_id: str = "FLOWPILOT"
    receiver_qualifier: str = "ZZ"
    receiver_id: str = "PARTNER"
    usage: str = "P"                 # P production, T test
    ack_requested: str = "1"         # ask for a TA1 / 997
    element: str = "*"
    segment: str = "~"
    component: str = ">"
    newline: bool = True

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "Envelope":
        raw = dict((config or {}).get("x12") or {})
        env = cls(**{k: str(raw[k]) if k != "newline" else bool(raw[k]) for k in cls.__dataclass_fields__ if k in raw})
        seps = {env.element, env.segment, env.component}
        if len(seps) != 3 or any(len(s) != 1 or s.isalnum() or s == " " for s in seps):
            raise X12Error("the X12 separators must be three different single non-alphanumeric characters")
        for name, value, limit in (("sender_id", env.sender_id, 15), ("receiver_id", env.receiver_id, 15)):
            if not 2 <= len(value) <= limit:
                raise X12Error(f"x12.{name} is 2 to {limit} characters")
        return env


@dataclass(frozen=True)
class ControlNumbers:
    interchange: int
    group: int
    transaction: str = "0001"

    def as_json(self) -> dict:
        return {"isa13": f"{self.interchange:09d}", "gs06": str(self.group), "st02": self.transaction}


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _clean(value: Any, env: Envelope, limit: int) -> str:
    text = "" if value is None else str(value)
    for sep in (env.element, env.segment, env.component, "^", "\r", "\n"):
        text = text.replace(sep, " ")
    text = "".join(ch for ch in text if 32 <= ord(ch) < 127)
    return " ".join(text.split())[:limit]


def _r(value: Any) -> str:
    """X12 R: a decimal with an explicit point only when needed, no exponent, no trailing zeros."""
    d = Decimal(str(value))
    text = format(d.normalize(), "f")
    return text[:-2] if text.endswith(".0") else text


def _n2(value: Any) -> str:
    """X12 N2: implied two decimals (1250.50 -> 125050). ARCH47-S1:n2-exact. An amount N2 cannot carry exactly
    (a third decimal, as KWD / BHD amounts can have) is refused, never rounded."""
    scaled = Decimal(str(value)) * 100
    d = scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if d != scaled:
        raise X12Error(f"{value} has more than two decimals: an X12 N2 amount (TDS01) cannot carry it exactly")
    return str(int(d))


def _d8(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")[:8]


def render(record: Record, object_kind: str, *, control: ControlNumbers, at: datetime,
           config: Mapping[str, Any] | None = None) -> bytes:
    if object_kind not in TRANSACTION:
        raise X12Error(f"X12 810/850/856 carries vendor bills, purchase orders and goods receipts, not {object_kind}")
    env = Envelope.from_config(config)
    ts = TRANSACTION[object_kind]
    h = record.header_dict()
    lines = record.line_dicts()
    body: list[list[str]] = [["ST", ts, control.transaction]]
    c = lambda value, n: _clean(value, env, n)  # noqa: E731
    if ts == "810":
        body.append(["BIG", _d8(h["invoice_date"]), c(h["invoice_number"], 22),
                     _d8(h["po_date"]) if h.get("po_date") else "", c(h.get("po_number"), 22)])
        if h.get("currency"):
            body.append(["CUR", "SE", c(h["currency"], 3).upper()])
        body.append(["N1", "SE", c(h["seller_name"], 60)] + (["92", c(h["seller_id"], 80)] if h.get("seller_id") else []))
        body.append(["N1", "BT", c(h["buyer_name"], 60)] + (["92", c(h["buyer_id"], 80)] if h.get("buyer_id") else []))
        if h.get("terms_net_days") is not None or h.get("terms_due_date"):
            body.append(["ITD", "01", "3", "", "", "", _d8(h["terms_due_date"]) if h.get("terms_due_date") else "",
                         str(int(h["terms_net_days"])) if h.get("terms_net_days") is not None else ""])
        for n, ln in enumerate(lines, start=1):
            body.append(["IT1", c(ln.get("line_number") or n, 20), _r(ln["quantity"]), c(ln["unit"], 2).upper(),
                         _r(ln["unit_price"]), "",
                         c(ln.get("product_qualifier") or "VP", 2).upper() if ln.get("product_id") else "",
                         c(ln.get("product_id"), 48)])
            if ln.get("description"):
                body.append(["PID", "F", "", "", "", c(ln["description"], 80)])
        body.append(["TDS", _n2(h["total_amount"])])
        if h.get("tax_amount") is not None:
            body.append(["TXI", "TX", _r(h["tax_amount"])])
        body.append(["CTT", str(len(lines))])
    elif ts == "850":
        body.append(["BEG", "00", "SA", c(h["po_number"], 22), "", _d8(h["po_date"])])
        if h.get("currency"):
            body.append(["CUR", "BY", c(h["currency"], 3).upper()])
        body.append(["N1", "BY", c(h["buyer_name"], 60)] + (["92", c(h["buyer_id"], 80)] if h.get("buyer_id") else []))
        body.append(["N1", "SE", c(h["seller_name"], 60)] + (["92", c(h["seller_id"], 80)] if h.get("seller_id") else []))
        for n, ln in enumerate(lines, start=1):
            body.append(["PO1", c(ln.get("line_number") or n, 20), _r(ln["quantity"]), c(ln["unit"], 2).upper(),
                         _r(ln["unit_price"]) if ln.get("unit_price") is not None else "", "",
                         c(ln.get("product_qualifier") or "VP", 2).upper() if ln.get("product_id") else "",
                         c(ln.get("product_id"), 48)])
            if ln.get("description"):
                body.append(["PID", "F", "", "", "", c(ln["description"], 80)])
        body.append(["CTT", str(len(lines))])
        if h.get("total_amount") is not None:
            body.append(["AMT", "TT", _r(h["total_amount"])])
    else:
        ship = h["ship_date"]
        body.append(["BSN", "00", c(h["shipment_id"], 30), _d8(ship), at.strftime("%H%M")])
        body.append(["HL", "1", "", "S", "1"])
        body.append(["DTM", "011", _d8(ship)])
        if h.get("ship_from_name"):
            body.append(["N1", "SF", c(h["ship_from_name"], 60)])
        if h.get("ship_to_name"):
            body.append(["N1", "ST", c(h["ship_to_name"], 60)])
        body.append(["HL", "2", "1", "O", "1"])
        body.append(["PRF", c(h["po_number"], 22)])
        hl = 2
        for n, ln in enumerate(lines, start=1):
            hl += 1
            body.append(["HL", str(hl), "2", "I", "0"])
            body.append(["LIN", c(ln.get("line_number") or n, 20), c(ln.get("product_qualifier") or "VP", 2).upper(),
                         c(ln["product_id"], 48)])
            body.append(["SN1", c(ln.get("line_number") or n, 20), _r(ln["quantity"]), c(ln["unit"], 2).upper()])
            if ln.get("description"):
                body.append(["PID", "F", "", "", "", c(ln["description"], 80)])
        body.append(["CTT", str(hl)])
    body.append(["SE", str(len(body) + 1), control.transaction])
    return _envelope(body, ts, env, control, at)


def _envelope(body: list[list[str]], ts: str, env: Envelope, control: ControlNumbers, at: datetime) -> bytes:
    isa = ["ISA", "00", " " * 10, "00", " " * 10, env.sender_qualifier, env.sender_id.ljust(15)[:15],
           env.receiver_qualifier, env.receiver_id.ljust(15)[:15], at.strftime("%y%m%d"), at.strftime("%H%M"), "U",
           VERSION, f"{control.interchange:09d}", env.ack_requested, env.usage, env.component]
    gs = ["GS", FUNCTIONAL_ID[ts], env.sender_id.strip()[:15], env.receiver_id.strip()[:15], at.strftime("%Y%m%d"),
          at.strftime("%H%M"), str(control.group), "X", GS_VERSION]
    tail = [["GE", "1", str(control.group)], ["IEA", "1", f"{control.interchange:09d}"]]
    segments = [isa, gs, *body, *tail]
    end = env.segment + ("\n" if env.newline else "")
    text = end.join(env.element.join(_trim(seg)) for seg in segments) + end
    return text.encode("ascii")


def _trim(seg: list[str]) -> list[str]:
    """Trailing empty elements are dropped (X12: no trailing element separators). ISA keeps all 16."""
    if seg[0] == "ISA":
        return seg
    out = list(seg)
    while len(out) > 1 and out[-1] == "":
        out.pop()
    return out


# ---------------------------------------------------------------------------
# reading and validation (independent of the writer)
# ---------------------------------------------------------------------------


@dataclass
class Interchange:
    element: str
    segment: str
    component: str
    segments: list[list[str]]


def parse(data: bytes | str) -> Interchange:
    text = data.decode("ascii") if isinstance(data, (bytes, bytearray)) else data
    text = text.lstrip("﻿")
    if not text.startswith("ISA"):
        raise X12Error("an interchange starts with ISA")
    if len(text) < 106:
        raise X12Error("shorter than an ISA segment")
    element, component, segment = text[3], text[104], text[105]
    rows = []
    for raw in text.split(segment):
        raw = raw.strip("\r\n")
        if raw:
            rows.append(raw.split(element))
    return Interchange(element, segment, component, rows)


def _check_element(seg: str, spec: E, value: str, problems: list[str], where: str) -> None:
    ref, kind, lo, hi, mandatory, codes = spec
    if value == "":
        if mandatory:
            problems.append(f"{where} {ref} is mandatory")
        return
    if not lo <= len(value) <= hi:
        problems.append(f"{where} {ref} length {len(value)} not in {lo}-{hi}: {value!r}")
    if kind in ("AN", "ID") and any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
        problems.append(f"{where} {ref} has characters outside the X12 character set")
    if kind == "ID" and codes and value not in codes:
        problems.append(f"{where} {ref} {value!r} is not a valid code ({', '.join(codes[:12])})")
    if kind == "N0" and not re.fullmatch(r"-?\d+", value):
        problems.append(f"{where} {ref} {value!r} is not N0")
    if kind == "N2" and not re.fullmatch(r"-?\d+", value):
        problems.append(f"{where} {ref} {value!r} is not N2 (implied decimals)")
    if kind == "R" and not re.fullmatch(r"-?(\d+\.?\d*|\.\d+)", value):
        problems.append(f"{where} {ref} {value!r} is not R")
    if kind == "DT":
        fmt = "%y%m%d" if len(value) == 6 else "%Y%m%d"
        try:
            datetime.strptime(value, fmt)
        except ValueError:
            problems.append(f"{where} {ref} {value!r} is not a date")
    if kind == "TM" and not (re.fullmatch(r"\d{4}(\d{2})?", value) and int(value[:2]) < 24 and int(value[2:4]) < 60):
        problems.append(f"{where} {ref} {value!r} is not a time")


def validate(data: bytes | str) -> list[str]:
    """Every problem with an interchange (empty when it conforms)."""
    problems: list[str] = []
    try:
        ix = parse(data)
    except (X12Error, UnicodeDecodeError) as exc:
        return [str(exc)]
    text = data.decode("ascii") if isinstance(data, (bytes, bytearray)) else data
    isa_raw = text[: text.index(ix.segment) + 1]
    if len(isa_raw) != 106:
        problems.append(f"ISA is {len(isa_raw)} characters, not 106")
    segs = ix.segments
    for n, seg in enumerate(segs, start=1):
        sid = seg[0]
        spec = SPEC.get(sid)
        where = f"segment {n} {sid}"
        if spec is None:
            problems.append(f"{where}: not a segment this validator knows")
            continue
        values = seg[1:]
        if len(values) > len(spec):
            problems.append(f"{where}: {len(values)} elements, at most {len(spec)}")
        if sid != "ISA" and values and values[-1] == "":
            problems.append(f"{where}: trailing empty element")
        for i, el in enumerate(spec):
            _check_element(sid, el, values[i] if i < len(values) else "", problems, where)
        if sid == "ISA" and len(values) == 16 and values[15] != ix.component:
            problems.append("ISA16 is not the component separator")
    ids = [s[0] for s in segs]
    if not ids or ids[0] != "ISA" or ids[-1] != "IEA":
        problems.append("an interchange is ISA ... IEA")
        return problems
    isa, iea = segs[0], segs[-1]
    groups = 0
    i = 1
    while i < len(segs) - 1:
        if segs[i][0] != "GS":
            problems.append(f"segment {i + 1}: expected GS, found {segs[i][0]}")
            break
        gs = segs[i]
        groups += 1
        j = i + 1
        sets = 0
        while j < len(segs) and segs[j][0] == "ST":
            st = segs[j]
            k = j
            while k < len(segs) and segs[k][0] != "SE":
                k += 1
            if k >= len(segs):
                problems.append("ST without SE")
                return problems
            se = segs[k]
            sets += 1
            count = k - j + 1
            if se[1:2] != [str(count)]:
                problems.append(f"SE01 is {se[1] if len(se) > 1 else '?'}; the transaction set has {count} segments")
            if se[2:3] != st[2:3]:
                problems.append(f"SE02 {se[2:3]} does not match ST02 {st[2:3]}")
            ts = st[1]
            grammar = GRAMMAR.get(ts)
            order = " ".join(s[0] for s in segs[j:k + 1]) + " "
            if grammar and not re.fullmatch(grammar, order):
                problems.append(f"{ts} segment order not allowed: {order.strip()}")
            if FUNCTIONAL_ID.get(ts) != gs[1]:
                problems.append(f"GS01 {gs[1]} does not carry a {ts}")
            body = segs[j:k + 1]
            ctt = [s for s in body if s[0] == "CTT"]
            if ctt:
                expected = sum(1 for s in body if s[0] in ("IT1", "PO1")) if ts in ("810", "850") \
                    else sum(1 for s in body if s[0] == "HL")
                if ctt[0][1] != str(expected):
                    problems.append(f"CTT01 is {ctt[0][1]}; the set has {expected}")
            j = k + 1
        if j >= len(segs) or segs[j][0] != "GE":
            problems.append("GS without GE")
            return problems
        ge = segs[j]
        if ge[1:2] != [str(sets)]:
            problems.append(f"GE01 is {ge[1]}; the group has {sets} transaction set(s)")
        if ge[2:3] != gs[6:7]:
            problems.append(f"GE02 {ge[2:3]} does not match GS06 {gs[6:7]}")
        i = j + 1
    if iea[1:2] != [str(groups)]:
        problems.append(f"IEA01 is {iea[1]}; the interchange has {groups} group(s)")
    if iea[2:3] != isa[13:14]:
        problems.append(f"IEA02 {iea[2:3]} does not match ISA13 {isa[13:14]}")
    return problems


# ---------------------------------------------------------------------------
# 997 functional acknowledgement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ack997:
    group_control: str
    functional_id: str
    transactions: tuple[tuple[str, str, str], ...]   # (set id, control number, AK501)
    group_status: str                                  # AK901
    included: int
    received: int
    accepted: int

    @property
    def accepted_all(self) -> bool:
        return self.group_status == "A" and all(t[2] == "A" for t in self.transactions)

    def as_json(self) -> dict:
        return {"group_control": self.group_control, "functional_id": self.functional_id,
                "transactions": [list(t) for t in self.transactions], "group_status": self.group_status,
                "included": self.included, "received": self.received, "accepted": self.accepted}


def parse_997(data: bytes | str) -> Ack997:
    problems = validate(data)
    if problems:
        raise X12Error("not a valid 997: " + "; ".join(problems[:5]))
    ix = parse(data)
    sts = [s for s in ix.segments if s[0] == "ST"]
    if not sts or sts[0][1] != "997":
        raise X12Error("not a 997")
    ak1 = next(s for s in ix.segments if s[0] == "AK1")
    ak9 = next(s for s in ix.segments if s[0] == "AK9")
    txs = []
    current: Optional[list[str]] = None
    for s in ix.segments:
        if s[0] == "AK2":
            current = s
        elif s[0] == "AK5" and current is not None:
            txs.append((current[1], current[2], s[1]))
            current = None
    return Ack997(ak1[2], ak1[1], tuple(txs), ak9[1], int(ak9[2]), int(ak9[3]), int(ak9[4]))


def build_997(interchange: bytes | str, *, status: str = "A", control: int = 1, at: Optional[datetime] = None,
              sender: str = "PARTNER", receiver: str = "FLOWPILOT") -> bytes:
    """A 997 answering an interchange (used by the mock partner in the gates and by a person's manual ack)."""
    ix = parse(interchange)
    gs = next(s for s in ix.segments if s[0] == "GS")
    sts = [s for s in ix.segments if s[0] == "ST"]
    at = at or datetime(2026, 1, 1, 12, 0)
    env = Envelope(sender_id=sender, receiver_id=receiver)
    body = [["ST", "997", "0001"], ["AK1", gs[1], gs[6]]]
    for st in sts:
        body += [["AK2", st[1], st[2]], ["AK5", status]]
    accepted = len(sts) if status in ("A", "E") else 0
    body.append(["AK9", status if status != "E" else "E", str(len(sts)), str(len(sts)), str(accepted)])
    body.append(["SE", str(len(body) + 1), "0001"])
    return _envelope(body, "997", env, ControlNumbers(control, control), at)


def correlate(ack: Ack997, control_numbers: Mapping[str, Any], object_kind: str) -> tuple[str, str]:
    """(outcome, message): does this 997 acknowledge THIS posting, and how?"""
    ts = TRANSACTION[object_kind]
    if ack.group_control != str(control_numbers.get("gs06")) or ack.functional_id != FUNCTIONAL_ID[ts]:
        return v.OUTCOME_PENDING, "a 997 for another group"
    ours = [t for t in ack.transactions if t[0] == ts and t[1] == control_numbers.get("st02")]
    if ack.group_status in ("R",) or any(t[2] in ("R", "M", "W", "X") for t in ours):
        return v.OUTCOME_REJECTED, f"the partner rejected the {ts} (AK9 {ack.group_status}, AK5 {[t[2] for t in ours]})"
    if not ours and ack.transactions:
        return v.OUTCOME_MISMATCH, f"the 997 names group {ack.group_control} but not transaction set {ts} " \
                                   f"{control_numbers.get('st02')}"
    if ack.group_status in ("E", "P") or any(t[2] == "E" for t in ours):
        return v.OUTCOME_MISMATCH, f"accepted with errors (AK9 {ack.group_status}): check what the partner kept"
    if ack.accepted_all and ack.accepted >= 1:
        return v.OUTCOME_ACCEPTED, f"997 accepted (group {ack.group_control})"
    return v.OUTCOME_MISMATCH, f"a 997 that does not accept the set (AK9 {ack.group_status})"


__all__ = ["Ack997", "CONTRACTS", "ControlNumbers", "Envelope", "GRAMMAR", "MEDIA_TYPE", "SPEC", "SUPPORTED",
           "TRANSACTION", "X12Error", "build_997", "correlate", "parse", "parse_997", "render", "validate"]
