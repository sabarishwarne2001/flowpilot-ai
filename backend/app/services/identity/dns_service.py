"""ARCH-16 — DNS TXT resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.services.identity._integration import get_settings

logger = logging.getLogger(__name__)


@dataclass
class TxtLookupResult:
    domain: str
    records: list[str] = field(default_factory=list)
    resolved: bool = False
    error: str | None = None

    def contains(self, expected: str) -> bool:
        return any(r.strip() == expected for r in self.records)


def _normalise(record) -> str:
    if hasattr(record, "strings"):
        parts = [
            p.decode("utf-8", errors="replace") if isinstance(p, (bytes, bytearray))
            else str(p)
            for p in record.strings
        ]
        return "".join(parts)
    text = str(record).strip()
    if text.startswith('"') and text.endswith('"'):
        return "".join(chunk for chunk in text.replace('" "', "").split('"') if chunk)
    return text


def lookup_txt(domain: str, *, subdomain: str | None = None) -> TxtLookupResult:
    settings = get_settings()
    fqdn = f"{subdomain}.{domain}" if subdomain else domain
    result = TxtLookupResult(domain=fqdn)

    try:
        import dns.resolver
        import dns.exception
    except ImportError:
        result.error = "dnspython is not installed"
        logger.error("ARCH-16: dnspython missing; domain verification cannot run")
        return result

    resolver = dns.resolver.Resolver(configure=False)
    configured = getattr(settings, "DNS_RESOLVERS", "1.1.1.1,8.8.8.8")
    resolver.nameservers = [s.strip() for s in str(configured).split(",") if s.strip()]
    timeout = float(getattr(settings, "DNS_TIMEOUT_S", 5))
    resolver.timeout = timeout
    resolver.lifetime = timeout

    try:
        answers = resolver.resolve(fqdn, "TXT")
        result.records = [_normalise(r) for r in answers]
        result.resolved = True
    except dns.resolver.NXDOMAIN:
        result.resolved = True
        result.records = []
    except dns.resolver.NoAnswer:
        result.resolved = True
        result.records = []
    except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
        result.error = f"resolver unavailable: {exc}"
    except Exception as exc:
        result.error = f"unexpected: {exc}"

    return result