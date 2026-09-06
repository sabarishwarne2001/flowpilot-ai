#!/usr/bin/env python3
"""ARCH-14 Step 1 & ARCH-18 — publish commercial price book with rate cards and cost bases.

Idempotent. Safe to run with or without CLI arguments.

    python scripts/seed_price_book.py
    python scripts/seed_price_book.py --version 1 --json
"""

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
from app.models.price_book import PriceBook  # noqa: E402
from app.services import pricing_service  # noqa: E402
from app.services.pricing_service import PriceSpec  # noqa: E402
from sqlalchemy import select  # noqa: E402

PLACEHOLDER_ENTRIES: list[dict[str, Any]] = [
    # --- Groq ---
    {
        "event_type": "llm.input_token",
        "provider": "groq",
        "model": None,
        "unit_price_micros": "0.100000000",
        "cost_basis_micros": "0.050000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Groq input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "groq",
        "model": None,
        "unit_price_micros": "0.300000000",
        "cost_basis_micros": "0.075000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Groq output tokens.",
    },
    {
        "event_type": "llm.input_token",
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "unit_price_micros": "0.590000000",
        "cost_basis_micros": "0.059000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Groq Llama 3.3 70B input rate.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "unit_price_micros": "0.790000000",
        "cost_basis_micros": "0.079000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Groq Llama 3.3 70B output rate.",
    },
    # --- Google Gemini ---
    {
        "event_type": "llm.input_token",
        "provider": "gemini",
        "model": None,
        "unit_price_micros": "0.150000000",
        "cost_basis_micros": "0.075000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Gemini input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "gemini",
        "model": None,
        "unit_price_micros": "0.600000000",
        "cost_basis_micros": "0.300000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Gemini output tokens.",
    },
    # --- OpenAI ---
    {
        "event_type": "llm.input_token",
        "provider": "openai",
        "model": None,
        "unit_price_micros": "2.500000000",
        "cost_basis_micros": "1.500000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for OpenAI input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "openai",
        "model": None,
        "unit_price_micros": "10.000000000",
        "cost_basis_micros": "6.000000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for OpenAI output tokens.",
    },
    # --- Anthropic ---
    {
        "event_type": "llm.input_token",
        "provider": "anthropic",
        "model": None,
        "unit_price_micros": "3.000000000",
        "cost_basis_micros": "2.000000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Anthropic input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "anthropic",
        "model": None,
        "unit_price_micros": "15.000000000",
        "cost_basis_micros": "10.000000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Anthropic output tokens.",
    },
    # --- Mistral ---
    {
        "event_type": "llm.input_token",
        "provider": "mistral",
        "model": None,
        "unit_price_micros": "0.800000000",
        "cost_basis_micros": "0.400000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Mistral input tokens.",
    },
    {
        "event_type": "llm.output_token",
        "provider": "mistral",
        "model": None,
        "unit_price_micros": "2.400000000",
        "cost_basis_micros": "1.200000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Provider-wide default for Mistral output tokens.",
    },
    # --- Ingestion, OCR & Storage ---
    {
        "event_type": "ocr.page",
        "provider": "paddleocr",
        "model": None,
        "unit_price_micros": "20000.000000000",
        "cost_basis_micros": "2000.000000000",
        "cost_basis_source": "MODELLED_ESTIMATE",
        "notes": "Self-hosted OCR extraction.",
    },
    {
        "event_type": "embedding.token",
        "provider": "sentence_transformers",
        "model": None,
        "unit_price_micros": "0.100000000",
        "cost_basis_micros": "0.010000000",
        "cost_basis_source": "MODELLED_ESTIMATE",
        "notes": "Self-hosted embedding.",
    },
    {
        "event_type": "embedding.backfill_token",
        "provider": "sentence_transformers",
        "model": None,
        "unit_price_micros": "0",
        "cost_basis_micros": "0",
        "cost_basis_source": "ZERO_BYOK",
        "notes": "Non-billable backfill.",
    },
    {
        "event_type": "storage.gb_month",
        "provider": "internal",
        "model": None,
        "unit_price_micros": "150000.000000000",
        "cost_basis_micros": "23000.000000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Tenant object storage.",
    },
    {
        "event_type": "document.processed",
        "provider": "internal",
        "model": None,
        "unit_price_micros": "0",
        "cost_basis_micros": "0",
        "cost_basis_source": "ZERO_BYOK",
        "notes": "Non-billable document counter.",
    },
    # --- Overage Pricing Entries ---
    {
        "event_type": "storage.gb_month.overage",
        "provider": "internal",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "150000.000000000",
        "cost_basis_micros": "23000.000000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "$0.15 per overage GB-month.",
    },
    {
        "event_type": "ocr.page.overage",
        "provider": "paddleocr",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "20000.000000000",
        "cost_basis_micros": "2000.000000000",
        "cost_basis_source": "MODELLED_ESTIMATE",
        "notes": "$0.02 per overage OCR page.",
    },
    {
        "event_type": "llm.input_token.overage",
        "provider": "groq",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "1.000000000",
        "cost_basis_micros": "0.059000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Overage rate for Groq input tokens.",
    },
    {
        "event_type": "llm.output_token.overage",
        "provider": "groq",
        "model": None,
        "tier_key": "overage",
        "unit_price_micros": "2.000000000",
        "cost_basis_micros": "0.079000000",
        "cost_basis_source": "SUPPLIER_RATE_CARD",
        "notes": "Overage rate for Groq output tokens.",
    },
    # --- Non-billable API Gateway Metering ---
    {
        "event_type": "api.request",
        "provider": "platform",
        "model": None,
        "unit_price_micros": "0",
        "cost_basis_micros": "0",
        "cost_basis_source": "ZERO_BYOK",
        "notes": "Non-billable developer gateway request counter.",
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
            cost_basis_micros=Decimal(str(row["cost_basis_micros"]))
            if row.get("cost_basis_micros") is not None
            else None,
            cost_basis_source=row.get("cost_basis_source"),
            notes=row.get("notes"),
        )
        for row in raw
    ]


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, default=1, help="Price book version (default: 1)")
    parser.add_argument(
        "--effective-from",
        dest="effective_from",
        type=str,
        default=None,
        help="ISO-8601 instant, e.g. 2026-08-01T00:00:00Z (default: current UTC timestamp)",
    )
    parser.add_argument("--from-json", type=Path, default=None)
    parser.add_argument("--currency", type=str, default="USD")
    parser.add_argument("--notes", type=str, default=None)
    parser.add_argument("--no-close-predecessor", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    entries = _load_entries(args.from_json)
    effective_from = (
        _parse_instant(args.effective_from)
        if args.effective_from
        else datetime.now(timezone.utc)
    )

    db = SessionLocal()
    try:
        existing = db.execute(
            select(PriceBook).where(PriceBook.version == args.version)
        ).scalar_one_or_none()

        if existing is not None:
            msg = {
                "status": "already-published",
                "version": existing.version,
                "price_book_id": str(existing.id),
                "currency": existing.currency,
                "published_at": existing.published_at.isoformat() if existing.published_at else None,
            }
            if args.as_json:
                print(json.dumps(msg, indent=2))
            else:
                print(f"Price book v{existing.version} is already published ({existing.id}).")
            return 0

        digest = pricing_service.content_digest(
            version=args.version,
            currency=args.currency,
            effective_from=effective_from,
            entries=entries,
        )

        if args.dry_run:
            print(f"version:        {args.version}")
            print(f"effective_from: {effective_from.isoformat()}")
            print(f"entries:        {len(entries)}")
            print(f"content_digest: {digest}")
            print("\ndry-run: nothing written.")
            return 0

        book = pricing_service.publish(
            db,
            version=args.version,
            effective_from=effective_from,
            entries=entries,
            currency=args.currency,
            notes=args.notes or "Initial platform default price book",
            close_predecessor=not args.no_close_predecessor,
        )
        book_id = str(book.id)
        book_version = book.version
        db.commit()

        if args.as_json:
            print(
                json.dumps(
                    {
                        "status": "published",
                        "version": book_version,
                        "price_book_id": book_id,
                        "currency": book.currency,
                        "entries": len(entries),
                        "content_digest": digest,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Published price book {book_id} v{book_version} with {len(entries)} entries.")
    except pricing_service.PriceBookValidationError as exc:
        if "already exists" in str(exc):
            print(f"Price book v{args.version} is already published.")
            return 0
        db.rollback()
        print(f"Validation error: {exc}")
        return 1
    except Exception as exc:
        db.rollback()
        print(f"seed_price_book error: {exc}")
        return 1
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())