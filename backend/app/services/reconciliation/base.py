"""ARCH-14 Step 5 — the provider statement source interface."""

from __future__ import annotations

import abc
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar, Optional

from app.models.reconciliation import Attribution, StatementGrain

logger = logging.getLogger("app.services.reconciliation")


class StatementSourceError(Exception):
    """A statement could not be read or could not be trusted."""


@dataclass(frozen=True)
class StatementLineSpec:
    cost_micros: int
    sku: Optional[str] = None
    model: Optional[str] = None
    event_type: Optional[str] = None
    organization_id: Optional[Any] = None
    occurred_on: Optional[date] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    currency: str = "USD"
    raw: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class StatementPayload:
    provider: str
    source_key: str
    grain: StatementGrain
    attribution: Attribution
    period_start: datetime
    period_end: datetime
    lines: tuple[StatementLineSpec, ...]
    currency: str = "USD"
    source_reference: Optional[str] = None
    source_digest: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def total_cost_micros(self) -> int:
        return sum(line.cost_micros for line in self.lines)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StatementPayload {self.provider} {self.source_key} "
            f"{len(self.lines)} lines {self.total_cost_micros}µ>"
        )


def digest_of(payload: Any) -> str:
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def dollars_to_micros(value: Any) -> int:
    return int(
        (Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP)
    )


class ProviderStatementSource(abc.ABC):
    provider: ClassVar[str]
    grain: ClassVar[StatementGrain]
    attribution: ClassVar[Attribution]
    fidelity_note: ClassVar[str] = ""

    @abc.abstractmethod
    def fetch(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        **options: Any,
    ) -> StatementPayload:
        """Read the provider's record for a period. Must not write."""

    @classmethod
    def _utc(cls, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @classmethod
    def _map_sku(cls, sku: str, mapping: dict[str, tuple[str, str]]) -> tuple[
        Optional[str], Optional[str]
    ]:
        normalised = (sku or "").strip().lower()
        for prefix, target in mapping.items():
            if normalised.startswith(prefix):
                return target
        logger.warning(
            "reconciliation.unmapped_sku",
            extra={"provider": cls.provider, "sku": sku},
        )
        return None, None

    @classmethod
    def describe(cls) -> dict[str, str]:
        return {
            "provider": cls.provider,
            "grain": cls.grain.value,
            "attribution": cls.attribution.value,
            "fidelity_note": cls.fidelity_note,
        }


_REGISTRY: dict[str, type[ProviderStatementSource]] = {}


def register_source(source: type[ProviderStatementSource]) -> type[ProviderStatementSource]:
    for required in ("provider", "grain", "attribution"):
        if not hasattr(source, required):
            raise StatementSourceError(
                f"{source.__name__} does not declare `{required}`."
            )
    _REGISTRY[source.provider] = source
    return source


def source_for(provider: str) -> type[ProviderStatementSource]:
    try:
        return _REGISTRY[provider.strip().lower()]
    except KeyError as exc:
        raise StatementSourceError(
            f"No statement source registered for provider {provider!r}."
        ) from exc


def registered_sources() -> dict[str, type[ProviderStatementSource]]:
    return dict(_REGISTRY)


__all__ = [
    "Attribution",
    "ProviderStatementSource",
    "StatementGrain",
    "StatementLineSpec",
    "StatementPayload",
    "StatementSourceError",
    "digest_of",
    "dollars_to_micros",
    "register_source",
    "registered_sources",
    "source_for",
]