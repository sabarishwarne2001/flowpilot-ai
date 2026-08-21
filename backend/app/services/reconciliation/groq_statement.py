"""ARCH-14 Step 5 — Groq, via an operator-supplied statement export."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Optional, Sequence

from app.models.reconciliation import Attribution, StatementGrain
from app.services.reconciliation.base import (
    ProviderStatementSource,
    StatementLineSpec,
    StatementPayload,
    StatementSourceError,
    digest_of,
    dollars_to_micros,
    register_source,
)

logger = logging.getLogger("app.services.reconciliation.groq")

GROQ_SKU_MAP: dict[str, tuple[str, str]] = {
    "llama-3.3-70b input": ("llama-3.3-70b-versatile", "llm.input_token"),
    "llama-3.3-70b output": ("llama-3.3-70b-versatile", "llm.output_token"),
    "llama-3.1-8b input": ("llama-3.1-8b-instant", "llm.input_token"),
    "llama-3.1-8b output": ("llama-3.1-8b-instant", "llm.output_token"),
    "mixtral-8x7b input": ("mixtral-8x7b-32768", "llm.input_token"),
    "mixtral-8x7b output": ("mixtral-8x7b-32768", "llm.output_token"),
}

_COLUMNS: dict[str, tuple[str, ...]] = {
    "date": ("date", "usage_date", "day", "period"),
    "sku": ("sku", "model", "description", "line_item", "product"),
    "quantity": ("tokens", "quantity", "usage", "units", "token_count"),
    "cost": ("cost", "amount", "cost_usd", "total", "charge"),
    "direction": ("direction", "type", "token_type"),
}


def _resolve_columns(fieldnames: Sequence[str]) -> dict[str, Optional[str]]:
    lowered = {name.strip().lower(): name for name in fieldnames if name}
    resolved: dict[str, Optional[str]] = {}
    for logical, aliases in _COLUMNS.items():
        resolved[logical] = next(
            (lowered[alias] for alias in aliases if alias in lowered), None
        )
    return resolved


@register_source
class GroqStatementSource(ProviderStatementSource):
    provider: ClassVar[str] = "groq"
    grain: ClassVar[StatementGrain] = StatementGrain.DAY
    attribution: ClassVar[Attribution] = Attribution.ALLOCATED
    fidelity_note: ClassVar[str] = (
        "Operator-exported CSV from the Groq console, daily per model. Groq "
        "publishes no per-request attribution, so this is ALLOCATED "
        "structurally. Figures lag for 1-2 days after period closes."
    )

    def fetch(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        csv_text: Optional[str] = None,
        rows: Optional[Sequence[dict[str, Any]]] = None,
        source_key: Optional[str] = None,
        source_reference: Optional[str] = None,
        **options: Any,
    ) -> StatementPayload:
        if csv_text is None and rows is None:
            raise StatementSourceError(
                "GroqStatementSource.fetch requires `csv_text` or `rows`."
            )

        start = self._utc(period_start)
        end = self._utc(period_end)

        materialised = list(rows) if rows is not None else self._parse(csv_text or "")
        if not materialised:
            raise StatementSourceError(
                "The Groq statement parsed to zero lines."
            )

        lines = [
            line
            for line in (self._line_from_row(row) for row in materialised)
            if line is not None
        ]

        key = source_key or (
            f"groq-console-export:{start.date().isoformat()}:{end.date().isoformat()}"
        )

        return StatementPayload(
            provider=self.provider,
            source_key=key,
            grain=self.grain,
            attribution=self.attribution,
            period_start=start,
            period_end=end,
            lines=tuple(lines),
            currency=str(options.get("currency", "USD")).upper(),
            source_reference=source_reference or "groq-console-csv",
            source_digest=digest_of(materialised),
            details={
                "row_count": len(materialised),
                "mapped_lines": sum(1 for line in lines if line.model),
                "unmapped_lines": sum(1 for line in lines if not line.model),
                "fidelity_note": self.fidelity_note,
            },
        )

    def _parse(self, csv_text: str) -> list[dict[str, Any]]:
        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise StatementSourceError("The Groq statement CSV has no header row.")

        columns = _resolve_columns(reader.fieldnames)
        if columns["cost"] is None or columns["sku"] is None:
            raise StatementSourceError(
                f"The Groq statement CSV has no recognisable cost or SKU column. Saw: {list(reader.fieldnames)}."
            )

        parsed: list[dict[str, Any]] = []
        for raw in reader:
            parsed.append(
                {
                    "date": raw.get(columns["date"]) if columns["date"] else None,
                    "sku": raw.get(columns["sku"]),
                    "quantity": (
                        raw.get(columns["quantity"]) if columns["quantity"] else None
                    ),
                    "cost": raw.get(columns["cost"]),
                    "direction": (
                        raw.get(columns["direction"]) if columns["direction"] else None
                    ),
                    "_raw": dict(raw),
                }
            )
        return parsed

    def _line_from_row(self, row: dict[str, Any]) -> Optional[StatementLineSpec]:
        raw_cost = row.get("cost")
        if raw_cost in (None, ""):
            return None
        try:
            cost_micros = dollars_to_micros(str(raw_cost).replace("$", "").strip())
        except (InvalidOperation, ValueError):
            logger.warning(
                "reconciliation.groq_unparseable_cost", extra={"value": raw_cost}
            )
            return None

        sku = str(row.get("sku") or "").strip()
        direction = str(row.get("direction") or "").strip().lower()
        lookup = sku if not direction else f"{sku} {direction}"
        model, event_type = self._map_sku(lookup, GROQ_SKU_MAP)

        quantity: Optional[Decimal] = None
        raw_quantity = row.get("quantity")
        if raw_quantity not in (None, ""):
            try:
                quantity = Decimal(str(raw_quantity).replace(",", "").strip())
            except InvalidOperation:
                logger.warning(
                    "reconciliation.groq_unparseable_quantity",
                    extra={"value": raw_quantity},
                )

        occurred_on: Optional[date] = None
        raw_date = row.get("date")
        if raw_date:
            try:
                occurred_on = date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                logger.warning(
                    "reconciliation.groq_unparseable_date", extra={"value": raw_date}
                )

        return StatementLineSpec(
            cost_micros=max(0, cost_micros),
            sku=sku or None,
            model=model,
            event_type=event_type,
            organization_id=None,
            occurred_on=occurred_on,
            quantity=quantity,
            unit="token" if quantity is not None else None,
            raw=row.get("_raw"),
        )


__all__ = ["GROQ_SKU_MAP", "GroqStatementSource"]