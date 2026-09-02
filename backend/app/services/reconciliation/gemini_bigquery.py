"""ARCH-14 Step 5 — Gemini, via the Google Cloud billing export in BigQuery."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
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

logger = logging.getLogger("app.services.reconciliation.gemini")

GEMINI_SKU_MAP: dict[str, tuple[str, str]] = {
    "gemini 1.5 flash input": ("gemini-1.5-flash", "llm.input_token"),
    "gemini 1.5 flash output": ("gemini-1.5-flash", "llm.output_token"),
    "gemini 1.5 pro input": ("gemini-1.5-pro", "llm.input_token"),
    "gemini 1.5 pro output": ("gemini-1.5-pro", "llm.output_token"),
    "gemini 2.0 flash input": ("gemini-2.0-flash", "llm.input_token"),
    "gemini 2.0 flash output": ("gemini-2.0-flash", "llm.output_token"),
    "gemini 2.5 flash input": ("gemini-2.5-flash", "llm.input_token"),
    "gemini 2.5 flash output": ("gemini-2.5-flash", "llm.output_token"),
    "gemini 2.5 pro input": ("gemini-2.5-pro", "llm.input_token"),
    "gemini 2.5 pro output": ("gemini-2.5-pro", "llm.output_token"),
}

_TOKEN_UNITS = {"tokens", "token", "characters", "character", "count"}


@register_source
class GeminiBigQuerySource(ProviderStatementSource):
    provider: ClassVar[str] = "gemini"
    grain: ClassVar[StatementGrain] = StatementGrain.DAY
    attribution: ClassVar[Attribution] = Attribution.ALLOCATED
    fidelity_note: ClassVar[str] = (
        "Google Cloud billing export, daily per SKU. Carries no tenant "
        "identifier: the Gemini Developer API refuses request labels, so "
        "per-organization figures from this source are pro-rata allocation "
        "and not measurement. Becomes ATTESTED when ARCH-14.6a moves Gemini "
        "traffic to Vertex AI with organization_id labels."
    )

    labels_available: ClassVar[bool] = False

    def fetch(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        rows: Optional[Sequence[dict[str, Any]]] = None,
        source_key: Optional[str] = None,
        source_reference: Optional[str] = None,
        **options: Any,
    ) -> StatementPayload:
        if rows is None:
            raise StatementSourceError(
                "GeminiBigQuerySource.fetch requires `rows` from the billing export."
            )

        start = self._utc(period_start)
        end = self._utc(period_end)
        materialised = list(rows)

        lines = [self._line_from_row(row) for row in materialised]
        lines = [line for line in lines if line is not None]

        key = source_key or (
            f"gemini-billing-export:{start.date().isoformat()}:{end.date().isoformat()}"
        )

        payload = StatementPayload(
            provider=self.provider,
            source_key=key,
            grain=self.grain,
            attribution=self.attribution,
            period_start=start,
            period_end=end,
            lines=tuple(lines),
            currency=str(options.get("currency", "USD")).upper(),
            source_reference=source_reference or "gcp_billing_export_resource_v1",
            source_digest=digest_of(materialised),
            details={
                "row_count": len(materialised),
                "mapped_lines": sum(1 for line in lines if line.model),
                "unmapped_lines": sum(1 for line in lines if not line.model),
                "labels_available": self.labels_available,
                "fidelity_note": self.fidelity_note,
                "credits_included": True,
            },
        )

        unmapped = payload.details["unmapped_lines"]
        if unmapped:
            logger.warning(
                "reconciliation.gemini_unmapped_skus",
                extra={"unmapped_lines": unmapped, "source_key": key},
            )
        return payload

    def _line_from_row(self, row: dict[str, Any]) -> Optional[StatementLineSpec]:
        sku = str(row.get("sku") or row.get("sku_description") or "").strip()
        raw_cost = row.get("cost")
        if raw_cost is None:
            logger.warning(
                "reconciliation.gemini_row_without_cost", extra={"sku": sku}
            )
            return None

        cost_micros = dollars_to_micros(raw_cost)
        if cost_micros < 0:
            logger.warning(
                "reconciliation.gemini_negative_line",
                extra={"sku": sku, "cost_micros": cost_micros},
            )

        model, event_type = self._map_sku(sku, GEMINI_SKU_MAP)

        quantity = row.get("usage_amount")
        unit = str(row.get("usage_unit") or "").strip().lower()
        if quantity is not None and unit and unit not in _TOKEN_UNITS:
            logger.warning(
                "reconciliation.gemini_unknown_unit",
                extra={"sku": sku, "unit": unit},
            )
            quantity = None

        occurred = row.get("usage_date")
        if isinstance(occurred, datetime):
            occurred_on: Optional[date] = occurred.date()
        elif isinstance(occurred, date):
            occurred_on = occurred
        elif occurred:
            occurred_on = date.fromisoformat(str(occurred)[:10])
        else:
            occurred_on = None

        return StatementLineSpec(
            cost_micros=max(0, cost_micros),
            sku=sku or None,
            model=model,
            event_type=event_type,
            organization_id=None,
            occurred_on=occurred_on,
            quantity=Decimal(str(quantity)) if quantity is not None else None,
            unit="token" if quantity is not None else None,
            raw={
                k: (v.isoformat() if isinstance(v, (date, datetime)) else v)
                for k, v in row.items()
            },
        )


__all__ = ["GEMINI_SKU_MAP", "GeminiBigQuerySource"]
