"""Schema self-healing: a document's extracted fields brought back to the schema of its type.

What drifts, and what is done about it:

  RENAME   a key the model spelled its own way ("Vendor Name", "supplier", "invoice_total",
           "vendr_name") is renamed to the schema's field, when it is the schema's name after
           normalisation, one of the field's listed aliases, or within one close spelling of them.
  RETYPE   a value in the wrong shape is rewritten in the schema's: "$1,250.00" -> 1250,
           "(45.10)" -> -45.1, "12 Jan 2026" / "2026/01/12" -> "2026-01-12", "us dollars" -> "USD",
           "Acme Ltd; Contoso" -> ["Acme Ltd", "Contoso"], runs of whitespace collapsed.

What is reported and never guessed:

  MISSING_REQUIRED  a required field is absent or empty after healing
  UNPARSEABLE       a value cannot be read as the field's type ("TBC" in a total)
  AMBIGUOUS_DATE    "03/04/2026" could be 3 April or 4 March
  CONFLICT          a drifted key and the schema's own key both exist with different values

`plan()` is pure. `apply()` writes the healed fields through the same guard as a correction made
in the document viewer (a document in the review queue or under a legal hold is refused), keeps the
original fields in `schema_healing_events`, and `revert()` puts them back unless the fields changed
again since.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from app.services.batches import canonical as c

#: Keys that are the pipeline's own bookkeeping, never fields to heal.
RESERVED_KEYS = frozenset({"classification_details", "document_classification"})

_FUZZY_CUTOFF = 0.88


@dataclass(frozen=True)
class Change:
    kind: str  # RENAME | RETYPE
    field: str
    before: Any
    after: Any
    from_field: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class Issue:
    kind: str  # MISSING_REQUIRED | UNPARSEABLE | AMBIGUOUS_DATE | CONFLICT
    field: str
    message: str
    value: Any = None


@dataclass
class HealingPlan:
    document_type: Optional[str]
    schema_key: Optional[str]
    schema_label: Optional[str]
    changes: list[Change] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    healed: dict[str, Any] = field(default_factory=dict)
    required_total: int = 0
    required_present: int = 0

    @property
    def has_schema(self) -> bool:
        return self.schema_key is not None

    @property
    def completeness(self) -> Optional[float]:
        if not self.required_total:
            return None
        return round(self.required_present / self.required_total, 4)

    @property
    def missing_required(self) -> list[str]:
        return [i.field for i in self.issues if i.kind == "MISSING_REQUIRED"]

    @property
    def state(self) -> str:
        """NO_SCHEMA | HEALTHY | HEALABLE | NEEDS_ATTENTION."""
        if not self.has_schema:
            return "NO_SCHEMA"
        if self.issues:
            return "NEEDS_ATTENTION"
        if self.changes:
            return "HEALABLE"
        return "HEALTHY"

    def changes_json(self) -> list[dict[str, Any]]:
        return [asdict(change) for change in self.changes]

    def issues_json(self) -> list[dict[str, Any]]:
        return [asdict(issue) for issue in self.issues]


# ------------------------------------------------------------------ value readers

_CURRENCY_WORDS = re.compile(
    r"(?i)\b(usd|us\$|eur|euros?|gbp|inr|rs\.?|aud|cad|jpy|chf|cny|sgd|aed|nzd|zar|dollars?|rupees?|pounds?)\b"
)
_SYMBOLS = "$€£₹¥"
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def parse_number(raw: Any) -> Optional[Decimal]:
    """A money or quantity string as a Decimal, or None when it is not one."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    text = _CURRENCY_WORDS.sub("", text)
    for symbol in _SYMBOLS:
        text = text.replace(symbol, "")
    text = text.strip()
    upper = text.upper()
    if upper.endswith("CR"):
        text = text[:-2]
    elif upper.endswith("DR"):
        negative, text = not negative, text[:-2]
    text = text.strip()
    if text.endswith("-"):
        negative, text = not negative, text[:-1]
    text = text.replace(" ", "").replace(" ", "").replace("'", "").replace("_", "")
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")  # 1.234,56
        else:
            text = text.replace(",", "")  # 1,234.56
    elif "," in text:
        head, _, tail = text.rpartition(",")
        if text.count(",") == 1 and len(tail) in (1, 2):
            text = f"{head}.{tail}"  # 12,5 / 12,50
        else:
            text = text.replace(",", "")  # 1,234 / 1,234,567
    if not _PLAIN_NUMBER.fullmatch(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


def number_json(value: Decimal) -> Any:
    """A Decimal as a JSON number: an int when whole, else a float with the same digits."""
    if value == value.to_integral_value():
        return int(value)
    as_float = float(value)
    return as_float if Decimal(repr(as_float)) == value else str(value)


_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
            ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
            ("oct", "october"), ("nov", "november"), ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?")
_YMD = re.compile(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})")
_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})")
_NUMERIC = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})")
_D_MON_Y = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]+([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{2}|\d{4})")
_MON_D_Y = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{2}|\d{4})")


def _year(text: str) -> int:
    value = int(text)
    if len(text) == 2:
        return 2000 + value if value < 70 else 1900 + value
    return value


def _make(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_date(raw: Any) -> tuple[Optional[str], bool]:
    """(ISO date, ambiguous). (None, True) when day and month cannot be told apart."""
    if isinstance(raw, datetime):
        return raw.date().isoformat(), False
    if isinstance(raw, date):
        return raw.isoformat(), False
    if not isinstance(raw, str):
        return None, False
    text = raw.strip()
    for pattern, order in ((_ISO, "ymd"), (_YMD, "ymd"), (_COMPACT, "ymd")):
        match = pattern.fullmatch(text)
        if match:
            y, m, d = (int(g) for g in match.groups()[:3])
            return _make(y, m, d), False
    match = _D_MON_Y.fullmatch(text)
    if match and match.group(2).lower() in _MONTHS:
        return _make(_year(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1))), False
    match = _MON_D_Y.fullmatch(text)
    if match and match.group(1).lower() in _MONTHS:
        return _make(_year(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2))), False
    match = _NUMERIC.fullmatch(text)
    if match:
        a, b, y = int(match.group(1)), int(match.group(2)), _year(match.group(3))
        if a > 12 and b <= 12:
            return _make(y, b, a), False
        if b > 12 and a <= 12:
            return _make(y, a, b), False
        if a == b:
            return _make(y, a, b), False
        return None, True
    return None, False


def parse_currency(raw: Any) -> Optional[str]:
    """The ISO code for a currency written any way (app.core.normalize.currency_code)."""
    from app.core.normalize import currency_code

    return currency_code(raw) if isinstance(raw, str) else None


_TRUE = {"yes", "y", "true", "t", "1"}
_FALSE = {"no", "n", "false", "f", "0"}
_SPLIT = re.compile(r"\s*[;\n|]\s*|\s*,\s*")


# ------------------------------------------------------------------ the planner

def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _resolve_targets(schema: c.CanonicalSchema) -> tuple[dict[str, str], list[str]]:
    """normalised name or alias -> field, and the candidate list for close spellings."""
    lookup: dict[str, str] = {}
    for spec in schema.fields:
        lookup.setdefault(c.normalize_key(spec.name), spec.name)
    for spec in schema.fields:
        for alias in spec.aliases:
            lookup.setdefault(c.normalize_key(alias), spec.name)
    return lookup, sorted(lookup)


def _retype(spec: c.FieldSpec, value: Any) -> tuple[Any, Optional[Issue], str]:
    """(new value, issue or None, note). The value is returned unchanged when nothing applies."""
    kind = spec.kind
    if _empty(value):
        return value, None, ""
    if kind == c.STRING:
        if isinstance(value, str):
            tidy = " ".join(value.split())
            return tidy, None, "whitespace" if tidy != value else ""
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return str(value), None, "number written as text"
        return value, None, ""
    if kind in (c.NUMBER, c.INTEGER):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if kind == c.INTEGER and isinstance(value, float) and value.is_integer():
                return int(value), None, "whole number"
            return value, None, ""
        parsed = parse_number(value)
        if parsed is None:
            return value, Issue("UNPARSEABLE", spec.name, f"'{value}' is not a number.", value), ""
        if kind == c.INTEGER and parsed != parsed.to_integral_value():
            return value, Issue("UNPARSEABLE", spec.name, f"'{value}' is not a whole number.", value), ""
        return number_json(parsed), None, "read as a number"
    if kind == c.BOOLEAN:
        if isinstance(value, bool):
            return value, None, ""
        text = str(value).strip().lower()
        if text in _TRUE:
            return True, None, "read as yes/no"
        if text in _FALSE:
            return False, None, "read as yes/no"
        return value, Issue("UNPARSEABLE", spec.name, f"'{value}' is not yes or no.", value), ""
    if kind == c.DATE:
        iso, ambiguous = parse_date(value)
        if iso is not None:
            return iso, None, "" if iso == value else "read as a date"
        if ambiguous:
            return value, Issue(
                "AMBIGUOUS_DATE", spec.name, f"'{value}' could be day/month or month/day.", value
            ), ""
        return value, Issue("UNPARSEABLE", spec.name, f"'{value}' is not a date.", value), ""
    if kind == c.CURRENCY:
        code = parse_currency(value)
        if code is None:
            return value, None, ""
        return code, None, "" if code == value else "ISO currency code"
    if kind == c.STRING_LIST:
        if isinstance(value, str):
            parts = [p for p in (" ".join(s.split()) for s in _SPLIT.split(value)) if p]
            return parts, None, "split into a list"
        if isinstance(value, list):
            tidy = [" ".join(v.split()) if isinstance(v, str) else v for v in value]
            tidy = [v for v in tidy if not _empty(v)]
            return tidy, None, "whitespace" if tidy != value else ""
        return value, None, ""
    if kind == c.ANY_LIST:
        if isinstance(value, Mapping):
            return [dict(value)], None, "single item made a list"
        return value, None, ""
    return value, None, ""


def plan(entities: Optional[Mapping[str, Any]], schema: Optional[c.CanonicalSchema]) -> HealingPlan:
    """What healing would change in `entities` to meet `schema`. Pure: reads nothing else."""
    source = dict(entities or {})
    document_type = c.document_type_of(source)
    result = HealingPlan(
        document_type=document_type,
        schema_key=schema.key if schema else None,
        schema_label=schema.label if schema else None,
        healed=copy.deepcopy(source),
    )
    if schema is None:
        return result

    specs = schema.by_name()
    lookup, candidates = _resolve_targets(schema)
    healed: dict[str, Any] = {}
    renamed_from: dict[str, str] = {}

    for key, value in source.items():
        if key in RESERVED_KEYS or key in specs:
            healed[key] = value
            continue
        normalized = c.normalize_key(key)
        target = lookup.get(normalized)
        if target is None and normalized:
            close = difflib.get_close_matches(normalized, candidates, n=1, cutoff=_FUZZY_CUTOFF)
            target = lookup[close[0]] if close else None
        if target is None or target in RESERVED_KEYS:
            healed[key] = value
            continue
        existing = source.get(target, healed.get(target))
        if target in source or target in healed:
            if existing == value or _empty(value):
                result.changes.append(Change("RENAME", target, value, existing, key, "repeat of the schema's field; dropped"))
                continue
            if _empty(existing):
                healed[target] = value
                renamed_from[target] = key
                result.changes.append(Change("RENAME", target, existing, value, key, "filled from a drifted key"))
                continue
            result.issues.append(Issue(
                "CONFLICT", target, f"'{key}' and '{target}' disagree; neither was changed.", value
            ))
            healed[key] = value
            continue
        healed[target] = value
        renamed_from[target] = key
        result.changes.append(Change("RENAME", target, value, value, key, ""))

    for name, spec in specs.items():
        if name not in healed:
            continue
        before = healed[name]
        after, issue, note = _retype(spec, before)
        if issue is not None:
            result.issues.append(issue)
        elif after != before:
            healed[name] = after
            result.changes.append(Change("RETYPE", name, before, after, renamed_from.get(name), note))

    required = [spec.name for spec in schema.fields if spec.required]
    result.required_total = len(required)
    for name in required:
        if _empty(healed.get(name)):
            result.issues.append(Issue("MISSING_REQUIRED", name, f"'{name}' is required and was not found."))
        else:
            result.required_present += 1

    result.healed = healed
    return result


def entities_digest(entities: Optional[Mapping[str, Any]]) -> str:
    """A stable SHA-256 of a document's fields (key order does not matter)."""
    payload = json.dumps(entities or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ writing

class HealingRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def apply(
    db: Session,
    *,
    work_item: Any,
    healing: HealingPlan,
    organization_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    batch_id: Optional[uuid.UUID],
) -> Any:
    """Write `healing` to the document. Returns the SchemaHealingEvent; raises HealingRefused."""
    from app.models.audit_log import AuditAction, AuditResourceType
    from app.models.batches import SchemaHealingEvent
    from app.services import audit_service, field_correction_service

    if not healing.has_schema:
        raise HealingRefused("NO_SCHEMA", "There is no schema for this document's type.")
    if not healing.changes:
        raise HealingRefused("NOTHING_TO_HEAL", "Its fields already match the schema.")
    if work_item.status != "COMPLETED":
        raise HealingRefused("NOT_COMPLETED", "Only a document that finished processing can be healed.")
    blocked = field_correction_service.editability(db, work_item=work_item, organization_id=organization_id)
    if blocked is not None:
        raise HealingRefused(blocked.code, blocked.message)

    original = copy.deepcopy(dict(work_item.extracted_entities or {}))
    healed = copy.deepcopy(healing.healed)
    event = SchemaHealingEvent(
        id=uuid.uuid4(),
        workspace_id=work_item.workspace_id,
        work_item_id=work_item.id,
        batch_id=batch_id,
        schema_key=healing.schema_key or "",
        changes=json.loads(json.dumps(healing.changes_json(), default=str)),
        original_entities=json.loads(json.dumps(original, default=str)),
        result_sha256=entities_digest(healed),
        applied_by_user_id=user_id,
    )
    db.add(event)
    work_item.extracted_entities = healed
    work_item.extraction_metadata = {
        **(work_item.extraction_metadata or {}),
        "schema_healing": {
            "event_id": str(event.id),
            "schema_key": healing.schema_key,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "changes": len(healing.changes),
        },
    }
    db.flush()
    audit_service.record(
        db,
        organization_id=organization_id,
        workspace_id=work_item.workspace_id,
        actor_id=user_id,
        resource_type=AuditResourceType.WORK_ITEM,
        resource_id=work_item.id,
        action=AuditAction.UPDATED,
        details={
            "operation": "schema_healing.apply",
            "event_id": str(event.id),
            "schema_key": healing.schema_key,
            "renamed": sum(1 for ch in healing.changes if ch.kind == "RENAME"),
            "retyped": sum(1 for ch in healing.changes if ch.kind == "RETYPE"),
            "batch_id": str(batch_id) if batch_id else None,
        },
    )
    return event


def revert(
    db: Session,
    *,
    event: Any,
    work_item: Any,
    organization_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
) -> None:
    """Put back the fields `event` replaced, unless they changed again since."""
    from app.models.audit_log import AuditAction, AuditResourceType
    from app.services import audit_service, field_correction_service

    if event.reverted_at is not None:
        raise HealingRefused("ALREADY_REVERTED", "This healing was already undone.")
    blocked = field_correction_service.editability(db, work_item=work_item, organization_id=organization_id)
    if blocked is not None:
        raise HealingRefused(blocked.code, blocked.message)
    if entities_digest(work_item.extracted_entities) != event.result_sha256:
        raise HealingRefused(
            "CHANGED_SINCE",
            "The fields were corrected after healing; undoing it would discard those corrections.",
        )
    work_item.extracted_entities = copy.deepcopy(event.original_entities)
    metadata = dict(work_item.extraction_metadata or {})
    metadata.pop("schema_healing", None)
    work_item.extraction_metadata = metadata
    event.reverted_at = datetime.now(timezone.utc)
    event.reverted_by_user_id = user_id
    db.flush()
    audit_service.record(
        db,
        organization_id=organization_id,
        workspace_id=work_item.workspace_id,
        actor_id=user_id,
        resource_type=AuditResourceType.WORK_ITEM,
        resource_id=work_item.id,
        action=AuditAction.UPDATED,
        details={"operation": "schema_healing.revert", "event_id": str(event.id)},
    )


__all__ = [
    "Change",
    "HealingPlan",
    "HealingRefused",
    "Issue",
    "RESERVED_KEYS",
    "apply",
    "entities_digest",
    "number_json",
    "parse_currency",
    "parse_date",
    "parse_number",
    "plan",
    "revert",
]
