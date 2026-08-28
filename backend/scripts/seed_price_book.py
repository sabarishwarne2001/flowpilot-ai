#!/usr/bin/env python3
"""ARCH-14 Step 1 — publish a price book."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.services import pricing_service  # noqa: E402
from app.services.pricing_service import PriceSpec  # noqa: E402

PLACEHOLDER_ENTRIES: list[dict[str, Any]] = [
    {
        "event_type": "llm.input_token",
        "provider": "groq",
        "model": None,
        "unit_price_micros": "0.100000000",
        "notes": "Provider-wide default for Groq input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "groq",
        "model": None,
        "unit_price_micros": "0.300000000",
        "notes": "Provider-wide default for Groq output tokens.",
    },
    {
        "event_type": "llm.input_token",
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "unit_price_micros": "0.590000000",
        "notes": "Groq Llama 3.3 70B input rate.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "unit_price_micros": "0.790000000",
        "notes": "Groq Llama 3.3 70B output rate.",
    },
    {
        "event_type": "llm.input_token",
        "provider": "gemini",
        "model": None,
        "unit_price_micros": "0.150000000",
        "notes": "Provider-wide default for Gemini input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "gemini",
        "model": None,
        "unit_price_micros": "0.600000000",
        "notes": "Provider-wide default for Gemini output tokens.",
    },
    {
        "event_type": "ocr.page",
        "provider": "paddleocr",
        "model": None,
        "unit_price_micros": "0",
        "notes": "Self-hosted. Zero marginal provider cost.",
    },
    {
        "event_type": "embedding.token",
        "provider": "sentence_transformers",
        "model": None,
        "unit_price_micros": "0",
        "notes": "Self-hosted embedding.",
    },
    {
        "event_type": "embedding.backfill_token",
        "provider": "sentence_transformers",
        "model": None,
        "unit_price_micros": "0",
        "notes": "Non-billable backfill.",
    },
    {
        "event_type": "storage.gb_month",
        "provider": "internal",
        "model": None,
        "unit_price_micros": "25000.000000000",
        "notes": "$0.025 per GB-month.",
    },
    {
        "event_type": "document.processed",
        "provider": "internal",
        "model": None,
        "unit_price_micros": "0",
        "notes": "Non-billable document counter.",
    },
    # --- Overage pricing entries required by Quota Tiers ---
    {
        "event_type": "storage.gb_month.overage",
        "provider": "internal",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "50000.000000000",
        "notes": "$0.05 per overage GB-month.",
    },
    {
        "event_type": "ocr.page.overage",
        "provider": "paddleocr",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "10000.000000000",
        "notes": "$0.01 per overage OCR page.",
    },
    {
        "event_type": "llm.input_token.overage",
        "provider": "groq",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "1.000000000",
        "notes": "Overage rate for Groq input tokens.",
    },
    {
        "event_type": "llm.output_token.overage",
        "provider": "groq",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "2.000000000",
        "notes": "Overage rate for Groq output tokens.",
    },
]


def _load_entries(path: Optional[Path]) -> list[PriceSpec]:
    raw = (
        json.loads(path.read_text(encoding="utf-8"))
        if path is not None
        else PLACEHOLDER_ENTRIES
    )
    if isinstance(raw, dict):
        raw = raw.get("entries", [])
    return [
        PriceSpec(
            event_type=row["event_type"],
            provider=row["provider"],
            model=row.get("model"),
            tier_key=row.get("tier_key"),
            unit=row.get("unit"),
            unit_price_micros=Decimal(str(row["unit_price_micros"])),
            notes=row.get("notes"),
        )
        for row in raw
    ]


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument(
        "--effective-from",
        type=str,
        required=True,
        help="ISO-8601 instant, e.g. 2026-08-01T00:00:00Z.",
    )
    parser.add_argument("--from-json", type=Path, default=None)
    parser.add_argument("--currency", type=str, default="USD")
    parser.add_argument("--notes", type=str, default=None)
    parser.add_argument(
        "--no-close-predecessor",
        action="store_true",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )
    args = parser.parse_args()

    entries = _load_entries(args.from_json)
    effective_from = _parse_instant(args.effective_from)

    digest = pricing_service.content_digest(
        version=args.version,
        currency=args.currency,
        effective_from=effective_from,
        entries=entries,
    )
    print(f"version:        {args.version}")
    print(f"effective_from: {effective_from.isoformat()}")
    print(f"entries:        {len(entries)}")
    print(f"content_digest: {digest}")
    for spec in sorted(entries, key=lambda s: (s.event_type, s.provider, s.model or "")):
        print(
            f"  {spec.event_type:<28} {spec.provider:<22} "
            f"{(spec.model or '*'):<24} {spec.unit_price_micros}"
        )

    if args.dry_run:
        print("\ndry-run: nothing written.")
        return 0

    db = SessionLocal()
    try:
        book = pricing_service.publish(
            db,
            version=args.version,
            effective_from=effective_from,
            entries=entries,
            currency=args.currency,
            notes=args.notes,
            close_predecessor=not args.no_close_predecessor,
        )
        book_id = str(book.id)
        book_version = book.version
        db.commit()
        print(f"\npublished price book {book_id} v{book_version}")
    except pricing_service.PriceBookValidationError as exc:
        if "already exists" in str(exc):
            print(f"\nPrice book v{args.version} is already published in the database.")
        else:
            db.rollback()
            raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())