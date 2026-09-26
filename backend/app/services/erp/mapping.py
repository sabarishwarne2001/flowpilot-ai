"""ARCH47-S1:mapping — the safe mapping language (fp-map/1). Pure; no I/O.

A mapping turns a canonical posting object into the fields one target wants.
It is DATA, never code: a JSON document whose every key is known, whose every
string is a literal, and whose only "operations" are the typed transforms
listed below. Nothing is evaluated -- no eval, no exec, no templates, no format
strings, no regular expressions, no attribute access. A mapping naming a path
the object does not have, a transform that does not exist, a key the grammar
does not define, or a string that looks like a template is REFUSED when it is
saved (`validate`), with the location of every problem.

    {
      "language": "fp-map/1",
      "object_kind": "VENDOR_BILL",
      "header": [
        {"to": "DocNumber", "value": {"path": "document_number"}, "required": true},
        {"to": "TxnDate", "value": {"path": "document_date", "transforms": [{"op": "date", "format": "YYYY-MM-DD"}]}},
        {"to": "VendorRef.value", "value": {"path": "vendor.entity_id",
                                            "transforms": [{"op": "lookup", "table": "vendor_ids"}]},
         "required": true},
        {"to": "PrivateNote", "value": {"concat": [{"const": "FlowPilot "}, {"path": "source.label"}]}}
      ],
      "lines": {"to": "Line", "fields": [
        {"to": "Amount", "value": {"path": "line.amount", "transforms": [{"op": "number", "decimals": 2}]}},
        {"to": "AccountBasedExpenseLineDetail.AccountRef.value",
         "value": {"path": "line.code", "transforms": [{"op": "lookup", "table": "gl_accounts",
                                                          "on_missing": "default", "default": "7"}]}}
      ]}
    }

EXPRESSIONS (exactly one of):
    {"path": "<object path>"}          header paths (canonical.HEADER_PATHS) anywhere;
                                        line paths ("line.*") only inside "lines"
    {"const": <string | number | true | false | null>}
    {"concat": [<expr>, ...], "sep": "<at most 8 characters>"}
    {"coalesce": [<expr>, ...]}         the first that is not empty
  each may carry "transforms": [<transform>, ...] (at most 12), applied in order.

TRANSFORMS ({"op": ..., parameters}):
    date        format: tokens YYYY YY MM M DD D MMM MMMM, separators - / . , : space T
    edm_date    an OData V2 Edm.DateTime literal, /Date(<ms since 1970-01-01, UTC midnight>)/
    number      decimals 0-6 (omitted: the currency's minor units -- JPY 0, INR 2, KWD 3),
                decimal_sep "." or ",", group_sep "" "," "." " " "'",
                rounding HALF_UP HALF_EVEN DOWN, output "string" or "number"
    minor_units the amount in the currency's minor units, as an integer (X12 N2)
    currency    case "upper" or "lower"; refuses anything but three letters
    lookup      table (a stored lookup table), on_missing error | default | keep | null, default
    map         values {literal: literal}, on_missing, default
    upper lower trim digits alnum negate abs
    truncate    max 1-2000
    pad         width 1-64, char (one character), side left | right
    replace     find (1-64 characters, a literal), with (0-64)
    prefix / suffix   text (0-64)
    multiply    by (a decimal literal)

FIELDS: {"to": <target field>, "value": <expr>, "required": bool, "default": <literal>}
LINES: {"to": <target key>, "from": "lines" | "lines_with_tax", "fields": [<field>, ...]}
"to" is a dotted path of identifiers (nested JSON for REST/OData targets; a
numeric segment is an array index, "LinkedTxn.0.TxnId"); for a
fixed format (X12, UBL, Tally) it must be one of that format's fields, and every
field the format requires must be mapped (`Contract`).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from app.services.erp import canonical as C
from app.services.erp import vocabulary as v

LANGUAGE = "fp-map/1"

#: a dotted path of identifiers; a numeric segment (0-99) is an array index ("LinkedTxn.0.TxnId")
_TARGET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}(\.([A-Za-z_][A-Za-z0-9_]{0,63}|[0-9]{1,2})){0,6}$")
LINE_SOURCES = ("lines", "lines_with_tax")
_LOOKUP_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: A mapping never evaluates a template; a string that looks like one is a mistake (or an attempt).
_TEMPLATE_MARKERS = ("{{", "{%", "${", "<%", "#{", "`", "%(", "{0", "{!")
#: Keys that signal someone expected code to run. Refused by name (they are unknown keys anyway).
_EXECUTABLE_KEYS = frozenset({"eval", "exec", "code", "script", "lambda", "python", "js", "javascript", "template",
                              "jinja", "expression", "expr", "formula", "function", "fn", "call", "import", "regex",
                              "pattern", "format_string", "fstring", "sql", "query", "command", "shell"})
_ROUNDING = {"HALF_UP": ROUND_HALF_UP, "HALF_EVEN": ROUND_HALF_EVEN, "DOWN": ROUND_DOWN}
_DATE_TOKENS = ("MMMM", "YYYY", "MMM", "YY", "MM", "DD", "M", "D")
_DATE_SEPARATORS = frozenset("-/., :T")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_MISSING = ("error", "default", "keep", "null")

_TRANSFORM_PARAMS: dict[str, dict[str, str]] = {
    "date": {"format": "str"},
    "edm_date": {},
    "number": {"decimals": "int", "decimal_sep": "str", "group_sep": "str", "rounding": "str", "output": "str"},
    "minor_units": {},
    "currency": {"case": "str"},
    "lookup": {"table": "str", "on_missing": "str", "default": "literal"},
    "map": {"values": "map", "on_missing": "str", "default": "literal"},
    "upper": {}, "lower": {}, "trim": {}, "digits": {}, "alnum": {}, "negate": {}, "abs": {},
    "truncate": {"max": "int"},
    "pad": {"width": "int", "char": "str", "side": "str"},
    "replace": {"find": "str", "with": "str"},
    "prefix": {"text": "str"}, "suffix": {"text": "str"},
    "multiply": {"by": "str"},
}
TRANSFORMS: tuple[str, ...] = tuple(_TRANSFORM_PARAMS)
_REQUIRED_PARAMS = {"date": ("format",), "lookup": ("table",), "map": ("values",), "truncate": ("max",),
                    "pad": ("width",), "replace": ("find",), "prefix": ("text",), "suffix": ("text",),
                    "multiply": ("by",)}


class MappingError(ValueError):
    """A mapping refused on save, or a value it could not produce at render time."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Contract:
    """What a target accepts: its fields (name -> required) for the header and each line.

    `open` targets (CSV, XLSX, generic REST/OData, presets) accept any well-formed field name; a
    fixed format accepts only its own fields and needs every required one mapped.
    """

    header: Mapping[str, bool] = field(default_factory=dict)
    lines: Mapping[str, bool] = field(default_factory=dict)
    open: bool = True
    lines_to: Optional[str] = None       # the key the line records go under (fixed for a preset body)
    needs_lines: bool = False


# ---------------------------------------------------------------------------
# validation (on save)
# ---------------------------------------------------------------------------


class _V:
    def __init__(self, kind: str, contract: Contract, lookup_tables: Optional[set[str]]) -> None:
        self.kind = kind
        self.contract = contract
        self.tables = lookup_tables
        self.problems: list[str] = []
        self.fields = 0

    def bad(self, where: str, message: str) -> None:
        if len(self.problems) < 100:
            self.problems.append(f"{where}: {message}")

    def keys(self, where: str, node: Mapping, allowed: set[str]) -> None:
        for key in node:
            if not isinstance(key, str):
                self.bad(where, "keys are strings")
            elif key.lower() in _EXECUTABLE_KEYS:
                self.bad(f"{where}.{key}", "a mapping never runs code or evaluates templates; use the "
                                           "declarative expressions (path, const, concat, coalesce) and transforms")
            elif key not in allowed:
                self.bad(f"{where}.{key}", f"unknown key (allowed: {', '.join(sorted(allowed))})")

    def literal(self, where: str, value: Any) -> None:
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
                self.bad(where, "not a finite number")
            return
        if isinstance(value, str):
            self.text(where, value, limit=2000)
            return
        self.bad(where, "a literal is a string, a number, true, false or null")

    def text(self, where: str, value: Any, *, limit: int) -> None:
        if not isinstance(value, str):
            self.bad(where, "must be a string")
            return
        if len(value) > limit:
            self.bad(where, f"at most {limit} characters")
        for marker in _TEMPLATE_MARKERS:
            if marker in value:
                self.bad(where, f"{marker!r} looks like a template; the mapping language never evaluates strings "
                                "(use concat for joined values)")
                break
        if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n" for ch in value):
            self.bad(where, "control characters are not allowed")

    def expr(self, where: str, node: Any, *, in_lines: bool, depth: int) -> None:
        if depth > v.MAX_EXPRESSION_DEPTH:
            self.bad(where, f"nested deeper than {v.MAX_EXPRESSION_DEPTH}")
            return
        if not isinstance(node, Mapping):
            self.bad(where, "an expression is an object with one of: path, const, concat, coalesce")
            return
        self.keys(where, node, {"path", "const", "concat", "sep", "coalesce", "transforms"})
        forms = [k for k in ("path", "const", "concat", "coalesce") if k in node]
        if len(forms) != 1:
            self.bad(where, "exactly one of path, const, concat, coalesce")
            return
        form = forms[0]
        if form == "path":
            path = node["path"]
            header, lines = C.HEADER_PATHS, C.LINE_PATHS
            if not isinstance(path, str):
                self.bad(f"{where}.path", "must be a string")
            elif path in header:
                pass
            elif path in lines:
                if not in_lines:
                    self.bad(f"{where}.path", f"{path!r} is a line path; it is only valid inside \"lines\"")
            else:
                self.bad(f"{where}.path", f"{path!r} is not a field of the posting object")
        elif form == "const":
            self.literal(f"{where}.const", node["const"])
        else:
            items = node[form]
            if not isinstance(items, list) or not 1 <= len(items) <= 20:
                self.bad(f"{where}.{form}", "a list of 1 to 20 expressions")
            else:
                for i, item in enumerate(items):
                    self.expr(f"{where}.{form}[{i}]", item, in_lines=in_lines, depth=depth + 1)
        if "sep" in node:
            if form != "concat":
                self.bad(f"{where}.sep", "only concat takes a separator")
            else:
                self.text(f"{where}.sep", node["sep"], limit=8)
        if "transforms" in node:
            self.transforms(f"{where}.transforms", node["transforms"])

    def transforms(self, where: str, items: Any) -> None:
        if not isinstance(items, list):
            self.bad(where, "a list of transforms")
            return
        if len(items) > v.MAX_TRANSFORMS:
            self.bad(where, f"at most {v.MAX_TRANSFORMS} transforms")
        for i, t in enumerate(items):
            here = f"{where}[{i}]"
            if not isinstance(t, Mapping):
                self.bad(here, "a transform is an object with an op")
                continue
            op = t.get("op")
            if not isinstance(op, str) or op not in _TRANSFORM_PARAMS:
                shown = op if isinstance(op, str) else type(op).__name__
                self.bad(f"{here}.op", f"unknown transform {shown!r} (allowed: {', '.join(TRANSFORMS)})")
                continue
            params = _TRANSFORM_PARAMS[op]
            self.keys(here, t, {"op", *params})
            for required in _REQUIRED_PARAMS.get(op, ()):
                if required not in t:
                    self.bad(here, f"{op} needs {required!r}")
            for name, kind in params.items():
                if name not in t:
                    continue
                value = t[name]
                at = f"{here}.{name}"
                if kind == "int":
                    if not isinstance(value, int) or isinstance(value, bool):
                        self.bad(at, "must be an integer")
                elif kind == "str":
                    self.text(at, value, limit=64)
                elif kind == "literal":
                    self.literal(at, value)
                elif kind == "map":
                    if not isinstance(value, Mapping) or not 1 <= len(value) <= 500:
                        self.bad(at, "an object of 1 to 500 entries")
                    else:
                        for k, val in value.items():
                            self.text(f"{at}.{k}", k, limit=200)
                            self.literal(f"{at}[{k!r}]", val)
            self._params(here, op, t)

    def _params(self, where: str, op: str, t: Mapping) -> None:
        if op == "date" and isinstance(t.get("format"), str):
            try:
                _date_tokens(t["format"])
            except ValueError as exc:
                self.bad(f"{where}.format", str(exc))
        elif op == "number":
            if "decimals" in t and isinstance(t["decimals"], int) and not 0 <= t["decimals"] <= 6:
                self.bad(f"{where}.decimals", "0 to 6")
            if t.get("decimal_sep", ".") not in (".", ","):
                self.bad(f"{where}.decimal_sep", "'.' or ','")
            if t.get("group_sep", "") not in ("", ",", ".", " ", "'"):
                self.bad(f"{where}.group_sep", "'', ',', '.', ' ' or \"'\"")
            if t.get("decimal_sep", ".") == t.get("group_sep", "") and t.get("group_sep", ""):
                self.bad(where, "the decimal and group separators must differ")
            if t.get("rounding", "HALF_UP") not in _ROUNDING:
                self.bad(f"{where}.rounding", f"one of {', '.join(_ROUNDING)}")
            if t.get("output", "string") not in ("string", "number"):
                self.bad(f"{where}.output", "'string' or 'number'")
        elif op == "currency" and t.get("case", "upper") not in ("upper", "lower"):
            self.bad(f"{where}.case", "'upper' or 'lower'")
        elif op in ("lookup", "map"):
            if t.get("on_missing", "error") not in _MISSING:
                self.bad(f"{where}.on_missing", f"one of {', '.join(_MISSING)}")
            if op == "lookup" and isinstance(t.get("table"), str):
                if not _LOOKUP_NAME.match(t["table"]):
                    self.bad(f"{where}.table", "a lookup table name (lower case, digits, underscores)")
                elif self.tables is not None and t["table"] not in self.tables:
                    self.bad(f"{where}.table", f"no lookup table named {t['table']!r} in this workspace")
        elif op == "truncate" and isinstance(t.get("max"), int) and not 1 <= t["max"] <= 2000:
            self.bad(f"{where}.max", "1 to 2000")
        elif op == "pad":
            if isinstance(t.get("width"), int) and not 1 <= t["width"] <= 64:
                self.bad(f"{where}.width", "1 to 64")
            if len(str(t.get("char", " "))) != 1:
                self.bad(f"{where}.char", "exactly one character")
            if t.get("side", "left") not in ("left", "right"):
                self.bad(f"{where}.side", "'left' or 'right'")
        elif op == "replace" and isinstance(t.get("find"), str) and not t["find"]:
            self.bad(f"{where}.find", "1 to 64 characters")
        elif op == "multiply" and isinstance(t.get("by"), str):
            # ARCH47-S1:multiply-literal. A plain decimal: no exponent, no NaN / Infinity, at most 12 + 12 digits.
            if not re.fullmatch(r"-?\d{1,12}(\.\d{1,12})?", t["by"]):
                self.bad(f"{where}.by", "a decimal literal such as \"0.01\" (no exponent)")

    def field_list(self, where: str, items: Any, *, in_lines: bool, allowed: Mapping[str, bool]) -> set[str]:
        seen: set[str] = set()
        if not isinstance(items, list):
            self.bad(where, "a list of fields")
            return seen
        for i, f in enumerate(items):
            here = f"{where}[{i}]"
            self.fields += 1
            if not isinstance(f, Mapping):
                self.bad(here, "a field is an object with to and value")
                continue
            self.keys(here, f, {"to", "value", "required", "default"})
            to = f.get("to")
            if not isinstance(to, str) or not _TARGET.match(to) or "__" in to:
                self.bad(f"{here}.to", "a dotted path of identifiers (letters, digits, underscores; no '__')")
            else:
                if to in seen:
                    self.bad(f"{here}.to", f"{to!r} is mapped twice")
                seen.add(to)
                if not self.contract.open and to not in allowed:
                    self.bad(f"{here}.to", f"{to!r} is not a field of this format "
                                           f"(fields: {', '.join(sorted(allowed))})")
            if "value" not in f:
                self.bad(here, "needs a value")
            else:
                self.expr(f"{here}.value", f["value"], in_lines=in_lines, depth=1)
            if "required" in f and not isinstance(f["required"], bool):
                self.bad(f"{here}.required", "true or false")
            if "default" in f:
                self.literal(f"{here}.default", f["default"])
        return seen


def validate(spec: Any, *, object_kind: str, contract: Contract, lookup_tables: Optional[set[str]] = None) -> dict:
    """The spec, checked. Raises MappingError listing every problem."""
    if not isinstance(spec, Mapping):
        raise MappingError(["a mapping is a JSON object"])
    try:
        size = len(json.dumps(spec, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise MappingError([f"not JSON: {exc}"]) from exc
    if size > v.MAX_MAPPING_BYTES:
        raise MappingError([f"{size} bytes; a mapping is at most {v.MAX_MAPPING_BYTES}"])
    chk = _V(object_kind, contract, lookup_tables)
    chk.keys("mapping", spec, {"language", "object_kind", "header", "lines", "description"})
    if spec.get("language") != LANGUAGE:
        chk.bad("mapping.language", f"must be {LANGUAGE!r}")
    if spec.get("object_kind") != object_kind:
        chk.bad("mapping.object_kind", f"must be {object_kind!r}")
    if "description" in spec:
        chk.text("mapping.description", spec["description"], limit=300)
    header = chk.field_list("header", spec.get("header", []), in_lines=False, allowed=contract.header)
    lines = spec.get("lines")
    line_fields: set[str] = set()
    if lines is not None:
        if not isinstance(lines, Mapping):
            chk.bad("lines", "an object with to and fields")
        else:
            chk.keys("lines", lines, {"to", "fields", "from"})
            if lines.get("from", "lines") not in LINE_SOURCES:
                chk.bad("lines.from", f"one of {', '.join(LINE_SOURCES)}")
            to = lines.get("to")
            if not isinstance(to, str) or not _TARGET.match(to) or "__" in to:
                chk.bad("lines.to", "a dotted path of identifiers")
            elif contract.lines_to is not None and to != contract.lines_to:
                chk.bad("lines.to", f"this target takes its lines under {contract.lines_to!r}")
            line_fields = chk.field_list("lines.fields", lines.get("fields", []), in_lines=True,
                                         allowed=contract.lines)
    elif contract.needs_lines:
        chk.bad("lines", "this target needs lines")
    if chk.fields > v.MAX_MAPPING_FIELDS:
        chk.bad("mapping", f"{chk.fields} fields; at most {v.MAX_MAPPING_FIELDS}")
    if chk.fields == 0:
        chk.bad("mapping", "maps nothing")
    for name, required in contract.header.items():
        if required and name not in header:
            chk.bad("header", f"{name!r} is required by this format and is not mapped")
    if lines is not None:
        for name, required in contract.lines.items():
            if required and name not in line_fields:
                chk.bad("lines.fields", f"{name!r} is required by this format and is not mapped")
    if chk.problems:
        raise MappingError(chk.problems)
    return dict(spec)


def _date_tokens(fmt: str) -> list[str]:
    if not 1 <= len(fmt) <= 32:
        raise ValueError("a date format is 1 to 32 characters")
    out: list[str] = []
    i = 0
    while i < len(fmt):
        for token in _DATE_TOKENS:
            if fmt.startswith(token, i):
                out.append(token)
                i += len(token)
                break
        else:
            ch = fmt[i]
            if ch not in _DATE_SEPARATORS:
                raise ValueError(f"{ch!r} is not a date token (YYYY YY MM M DD D MMM MMMM) or separator (- / . , : T)")
            out.append(ch)
            i += 1
    return out


# ---------------------------------------------------------------------------
# evaluation (at render time)
# ---------------------------------------------------------------------------


@dataclass
class Record:
    """The mapped values, typed, in mapping order."""

    header: list[tuple[str, Any]] = field(default_factory=list)
    lines: list[list[tuple[str, Any]]] = field(default_factory=list)
    lines_to: Optional[str] = None

    def header_dict(self) -> dict[str, Any]:
        return dict(self.header)

    def line_dicts(self) -> list[dict[str, Any]]:
        return [dict(x) for x in self.lines]

    def as_json(self) -> dict:
        return {"header": [[k, jsonable(val)] for k, val in self.header],
                "lines_to": self.lines_to,
                "lines": [[[k, jsonable(val)] for k, val in ln] for ln in self.lines]}


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _to_decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, bool):
        raise MappingError([f"{where}: {value!r} is not a number"])
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise MappingError([f"{where}: {value!r} is not a number"]) from exc


def _to_date(value: Any, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    raise MappingError([f"{where}: {value!r} is not a date"])


def _format_date(d: date, fmt: str) -> str:
    out = []
    for tok in _date_tokens(fmt):
        out.append({"YYYY": f"{d.year:04d}", "YY": f"{d.year % 100:02d}", "MM": f"{d.month:02d}", "M": str(d.month),
                    "DD": f"{d.day:02d}", "D": str(d.day), "MMM": _MONTHS[d.month - 1][:3],
                    "MMMM": _MONTHS[d.month - 1]}.get(tok, tok))
    return "".join(out)


def _format_number(n: Decimal, *, decimals: int, decimal_sep: str, group_sep: str, rounding: str) -> str:
    q = n.quantize(Decimal(1).scaleb(-decimals), rounding=_ROUNDING[rounding])
    sign = "-" if q < 0 else ""
    integer, _, frac = format(abs(q), "f").partition(".")
    if group_sep:
        parts = []
        while len(integer) > 3:
            parts.insert(0, integer[-3:])
            integer = integer[:-3]
        parts.insert(0, integer)
        integer = group_sep.join(parts)
    return sign + integer + (decimal_sep + frac if decimals else "")


class _Eval:
    def __init__(self, obj: dict, extra: dict, lookups: Mapping[str, Mapping[str, str]]) -> None:
        self.obj = obj
        self.extra = extra
        self.lookups = lookups
        self.currency = obj.get("currency") or ""

    def expr(self, node: Mapping, where: str, line: Optional[dict]) -> Any:
        if "path" in node:
            value = C.lookup(self.obj, node["path"], line, self.extra)
            kind = (C.LINE_PATHS if node["path"].startswith("line.") else C.HEADER_PATHS).get(node["path"])
            if value is not None and kind == "decimal":
                value = Decimal(str(value))
            elif value is not None and kind == "date" and isinstance(value, str):
                value = date.fromisoformat(value)
        elif "const" in node:
            value = node["const"]
            if isinstance(value, float):
                value = Decimal(str(value))
        elif "concat" in node:
            parts = [self.expr(x, where, line) for x in node["concat"]]
            value = str(node.get("sep", "")).join(str(jsonable(p)) for p in parts if not _empty(p)) or None
        else:
            value = None
            for x in node["coalesce"]:
                value = self.expr(x, where, line)
                if not _empty(value):
                    break
        for t in node.get("transforms", ()):
            value = self.transform(t, value, where)
        return value

    def transform(self, t: Mapping, value: Any, where: str) -> Any:
        op = t["op"]
        if op in ("lookup", "map"):
            return self._lookup(t, value, where)
        if value is None:
            return None
        if op == "date":
            return _format_date(_to_date(value, where), t["format"])
        if op == "edm_date":
            d = _to_date(value, where)
            return f"/Date({(d - date(1970, 1, 1)).days * 86400000})/"
        if op == "number":
            n = _to_decimal(value, where)
            # ARCH47-S1:currency-decimals. Without "decimals" a number is money in the posting's currency.
            decimals = int(t["decimals"]) if t.get("decimals") is not None else v.currency_exponent(self.currency)
            if t.get("output", "string") == "number":
                return n.quantize(Decimal(1).scaleb(-decimals), rounding=_ROUNDING[t.get("rounding", "HALF_UP")])
            return _format_number(n, decimals=decimals, decimal_sep=t.get("decimal_sep", "."),
                                  group_sep=t.get("group_sep", ""), rounding=t.get("rounding", "HALF_UP"))
        if op == "minor_units":
            n = _to_decimal(value, where) * (Decimal(10) ** v.currency_exponent(self.currency))
            if n != n.to_integral_value():
                raise MappingError([f"{where}: {value} has more decimals than {self.currency or 'the currency'} allows"])
            return int(n)
        if op == "currency":
            code = str(value).strip()
            if not re.fullmatch(r"[A-Za-z]{3}", code):
                raise MappingError([f"{where}: {value!r} is not a currency code"])
            return code.upper() if t.get("case", "upper") == "upper" else code.lower()
        if op in ("negate", "abs", "multiply"):
            n = _to_decimal(value, where)
            if op == "negate":
                return -n
            if op == "abs":
                return abs(n)
            return n * Decimal(t["by"])
        text = str(jsonable(value))
        if op == "upper":
            return text.upper()
        if op == "lower":
            return text.lower()
        if op == "trim":
            return " ".join(text.split())
        if op == "digits":
            return "".join(ch for ch in text if ch.isdigit())
        if op == "alnum":
            return "".join(ch for ch in text if ch.isalnum())
        if op == "truncate":
            return text[: int(t["max"])]
        if op == "pad":
            width, ch = int(t["width"]), str(t.get("char", " "))
            return text.rjust(width, ch) if t.get("side", "left") == "left" else text.ljust(width, ch)
        if op == "replace":
            return text.replace(str(t["find"]), str(t.get("with", "")))
        if op == "prefix":
            return str(t["text"]) + text
        if op == "suffix":
            return text + str(t["text"])
        raise MappingError([f"{where}: unknown transform {op!r}"])  # validate() makes this unreachable

    def _lookup(self, t: Mapping, value: Any, where: str) -> Any:
        if t["op"] == "lookup":
            table = self.lookups.get(t["table"])
            if table is None:
                raise MappingError([f"{where}: lookup table {t['table']!r} does not exist"])
        else:
            table = t["values"]
        key = None if value is None else str(jsonable(value))
        if key is not None and key in table:
            return table[key]
        missing = t.get("on_missing", "error")
        if missing == "default":
            return t.get("default")
        if missing == "keep":
            return value
        if missing == "null" or value is None:
            return None
        if t["op"] == "lookup":
            raise MappingError([f"{where}: {key!r} is not in the lookup table {t['table']!r}"])
        raise MappingError([f"{where}: {key!r} is not one of the mapped values"])


def evaluate(spec: Mapping, obj: C.PostingObject | dict, *, lookups: Mapping[str, Mapping[str, str]],
             extra: Optional[dict] = None) -> Record:
    """Apply a VALIDATED spec to an object. Raises MappingError naming every required field left empty."""
    body = obj.as_json() if isinstance(obj, C.PostingObject) else obj
    ev = _Eval(body, extra or {}, lookups)
    problems: list[str] = []
    record = Record()

    def run(fields: Sequence[Mapping], where: str, line: Optional[dict]) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for i, f in enumerate(fields):
            here = f"{where}, {f['to']}"
            try:
                value = ev.expr(f["value"], here, line)
            except MappingError as exc:
                problems.extend(exc.problems)
                continue
            if _empty(value) and "default" in f:
                value = f["default"]
                if isinstance(value, float):
                    value = Decimal(str(value))
            if _empty(value):
                if f.get("required"):
                    problems.append(f"{here}: required, and the posting has no value for it")
                    continue
                value = None
            out.append((f["to"], value))
        return out

    record.header = run(spec.get("header") or [], "header", None)
    lines = spec.get("lines")
    if lines:
        record.lines_to = lines["to"]
        for n, line in enumerate(body.get(lines.get("from", "lines")) or [], start=1):
            record.lines.append(run(lines.get("fields") or [], f"line {n}", line))
    if problems:
        raise MappingError(problems[:50])
    return record


def spec_sha(spec: Mapping) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


__all__ = ["Contract", "LANGUAGE", "MappingError", "Record", "TRANSFORMS", "evaluate", "jsonable", "spec_sha",
           "validate"]
