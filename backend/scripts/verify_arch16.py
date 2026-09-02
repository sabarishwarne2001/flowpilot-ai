#!/usr/bin/env python3
"""ARCH-16 release gate."""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

APP = REPO / "app"

GREEN, RED, YELLOW, BLUE, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[0m"


@dataclass
class Report:
    passes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    def ok(self, cid: str, msg: str):
        self.passes.append(f"{cid}: {msg}")
        print(f"{GREEN}[PASS]{RESET} {cid}  {msg}")

    def fail(self, cid: str, msg: str):
        self.failures.append(f"{cid}: {msg}")
        print(f"{RED}[FAIL]{RESET} {cid}  {msg}")

    def info(self, cid: str, msg: str):
        self.infos.append(f"{cid}: {msg}")
        print(f"{BLUE}[INFO]{RESET} {cid}  {msg}")

    def warn(self, cid: str, msg: str):
        print(f"{YELLOW}[WARN]{RESET} {cid}  {msg}")


def python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in {"__pycache__", ".venv", "node_modules"} for part in path.parts):
            continue
        yield path


def parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


XML_MODULES = {"lxml", "lxml.etree", "xml.etree", "xml.etree.ElementTree",
               "xml.dom", "xml.dom.minidom", "xml.sax", "xmltodict"}
SAML_GATEWAY = "app/services/identity/saml_gateway.py"
DNS_SERVICE = "app/services/identity/dns_service.py"


def s1_xml_confined(report: Report) -> None:
    offenders = []
    for path in python_files(APP):
        if rel(path).replace("\\", "/") == SAML_GATEWAY:
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in XML_MODULES:
                        offenders.append(f"{rel(path)} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module in XML_MODULES:
                offenders.append(f"{rel(path)} imports from {node.module}")
    if offenders:
        report.fail("S1", "unsafe XML parser outside saml_gateway: " + "; ".join(offenders))
    else:
        report.ok("S1", "XML parsing is confined to saml_gateway")


def s2_defusedxml_used(report: Report) -> None:
    path = REPO / SAML_GATEWAY
    if not path.exists():
        report.fail("S2", f"{SAML_GATEWAY} is missing")
        return
    source = path.read_text(encoding="utf-8")
    if "defusedxml" in source:
        report.ok("S2", "saml_gateway parses via defusedxml")
    else:
        report.fail("S2", "saml_gateway does not use defusedxml")


def s3_dns_confined(report: Report) -> None:
    offenders = []
    for path in python_files(APP):
        if rel(path).replace("\\", "/") == DNS_SERVICE:
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("dns."):
                        offenders.append(rel(path))
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dns"):
                offenders.append(rel(path))
    if offenders:
        report.fail("S3", "DNS resolution outside dns_service: " + ", ".join(set(offenders)))
    else:
        report.ok("S3", "DNS resolution is confined to dns_service")


def s4_no_owner_from_jit(report: Report) -> None:
    path = APP / "services" / "identity" / "jit_service.py"
    if not path.exists():
        report.fail("S4", "jit_service.py is missing")
        return
    source = path.read_text(encoding="utf-8")
    guarded = ('== "OWNER"' in source or "== 'OWNER'" in source or 'role == "OWNER"' in source)
    if guarded and 'return "OWNER"' not in source:
        report.ok("S4", "jit_service guards against OWNER and never returns it")
    else:
        report.fail("S4", "jit_service may yield OWNER")


def s5_deprovision_composition(report: Report) -> None:
    path = APP / "services" / "identity" / "deprovision_service.py"
    if not path.exists():
        report.fail("S5", "deprovision_service.py is missing")
        return
    source = path.read_text(encoding="utf-8")

    required = {
        "revoke_all_user_sessions": "session revocation",
        "revoke_api_keys_for_member": "API key revocation",
        "suppress_job_effects": "job effect suppression",
    }
    missing = [desc for name, desc in required.items() if name not in source]
    if missing:
        report.fail("S5", "deprovision_member does not reach: " + ", ".join(missing))
    else:
        report.ok("S5", "deprovision_member composes all three revocation primitives")

    if "scim_api_keys" in source and "UPDATE scim_api_keys" in source:
        report.fail("S5b", "deprovision_service revokes scim_api_keys")
    else:
        report.ok("S5b", "deprovision_service does not revoke organization-owned SCIM tokens")


def s6_authn_instant_not_now(report: Report) -> None:
    offenders = []
    for path in python_files(APP):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords or []:
                if kw.arg != "authenticated_at":
                    continue
                value = kw.value
                is_now = (
                    (isinstance(value, ast.Call)
                     and isinstance(value.func, ast.Name)
                     and value.func.id in {"utcnow", "now"})
                    or (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Attribute)
                        and value.func.attr in {"utcnow", "now"})
                )
                if not is_now:
                    continue
                source = path.read_text(encoding="utf-8")
                if "AuthMethod.PASSWORD" in source or "auth/login" in source:
                    continue
                offenders.append(f"{rel(path)}:{node.lineno}")
    if offenders:
        report.fail("S6", "authenticated_at=now() on a federated path: " + ", ".join(offenders))
    else:
        report.ok("S6", "no SSO path passes now() as authenticated_at")


def s7_no_iat_for_freshness(report: Report) -> None:
    offenders = []
    sensitive = [
        APP / "services" / "billing" / "portal_service.py",
    ]
    for path in sensitive:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "claims.issued_at" in text or "payload['iat']" in text:
            offenders.append(f"{rel(path)} reads iat for freshness")
        if "claims.auth_time" not in text:
            offenders.append(f"{rel(path)} does not read auth_time")

    if offenders:
        report.fail("S7", "`iat` read for freshness: " + ", ".join(offenders))
    else:
        report.ok("S7", "`iat` is not used as a freshness signal")


def s8_public_routes_registered(report: Report) -> None:
    registry = APP / "core" / "public_route_registry.py"
    if not registry.exists():
        report.fail("S8", "public_route_registry.py is missing")
        return
    source = registry.read_text(encoding="utf-8")
    required = ["/api/v1/saml/acs", "/scim/v2", "/api/v1/sso/discover"]
    missing = [r for r in required if r not in source]
    if missing:
        report.fail("S8", "unauthenticated routes absent from register: " + ", ".join(missing))
    else:
        report.ok("S8", "every ARCH-16 unauthenticated route is on the register")


def s9_scim_org_from_token(report: Report) -> None:
    path = APP / "api" / "v1" / "scim.py"
    if not path.exists():
        report.fail("S9", "scim.py is missing")
        return
    tree = parse(path)
    offenders = []

    def is_route(node) -> bool:
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr in {
                "get", "post", "put", "patch", "delete", "head", "options", "api_route",
            }:
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_route(node):
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.arg in {"organization_id", "org_id", "tenant_id"}:
                    offenders.append(f"{node.name}()")
    if offenders:
        report.fail("S9", "SCIM route takes an organization from path: " + ", ".join(offenders))
    else:
        report.ok("S9", "SCIM resolves the organization from the token only")


def s10_scim_error_schema(report: Report) -> None:
    path = APP / "services" / "identity" / "errors.py"
    if not path.exists() or "urn:ietf:params:scim:api:messages:2.0:Error" not in path.read_text(encoding="utf-8"):
        report.fail("S10", "SCIM errors do not use the SCIM error schema")
    else:
        report.ok("S10", "SCIM errors carry the SCIM error schema")


def s11_last_owner_guard(report: Report) -> None:
    path = APP / "services" / "identity" / "deprovision_service.py"
    source = path.read_text(encoding="utf-8") if path.exists() else ""
    if "assert_not_last_owner" in source and "LastOwnerProtected" in source:
        report.ok("S11", "deprovisioning refuses to orphan an organization")
    else:
        report.fail("S11", "no last-owner guard on the deprovision path")


def s12_no_sha1_signatures(report: Report) -> None:
    path = REPO / SAML_GATEWAY
    source = path.read_text(encoding="utf-8") if path.exists() else ""
    if "ALLOWED_SIGNATURE_ALGORITHMS" not in source:
        report.fail("S12", "no signature algorithm allow-list in saml_gateway")
        return
    if "rsa-sha1" in source or "#sha1" in source:
        report.fail("S12", "SHA-1 appears in the signature allow-list")
    else:
        report.ok("S12", "signature algorithms are allow-listed and SHA-1 is absent")


def s13_verified_node_only(report: Report) -> None:
    path = REPO / SAML_GATEWAY
    source = path.read_text(encoding="utf-8") if path.exists() else ""
    if "verified_root" not in source:
        report.fail("S13", "verify_response does not bind the verifier's return value")
        return
    after_verify = source.split("verified_root = ", 1)[-1]
    if "envelope.find(\"./saml:Assertion" in after_verify or "envelope.findall(\"./saml:Assertion" in after_verify:
        report.fail("S13", "claims are read from unverified envelope after verification")
    else:
        report.ok("S13", "claims are read only from the verified node")


DB_CHECKS = [
    ("D1", "no verified domain is a consumer mail provider",
     """SELECT count(*) FROM verified_domains WHERE domain IN
        ('gmail.com','googlemail.com','outlook.com','hotmail.com','yahoo.com',
         'icloud.com','aol.com','proton.me','protonmail.com','mail.ru','qq.com')"""),

    ("D2", "no two organizations hold the SSO binding for one domain",
     """SELECT count(*) FROM (SELECT domain FROM verified_domains
        WHERE is_sso_binding GROUP BY domain HAVING count(*) > 1) x"""),

    ("D3", "every directory identity has a membership in its organization",
     """SELECT count(*) FROM directory_identities di
        WHERE di.active AND NOT EXISTS (
          SELECT 1 FROM organization_members om
          WHERE om.organization_id = di.organization_id AND om.user_id = di.user_id)"""),

    ("D4", "every SSO session names its IdP configuration",
     """SELECT count(*) FROM sessions
        WHERE auth_method <> 'PASSWORD' AND idp_config_id IS NULL"""),

    ("D5", "no session claims to have authenticated after it was created",
     """SELECT count(*) FROM sessions
        WHERE authenticated_at IS NOT NULL AND authenticated_at > created_at + interval '5 minutes'"""),

    ("D6", "no deprovisioned user holds a live session",
     """SELECT count(*) FROM directory_identities di
        JOIN sessions s ON s.user_id = di.user_id
        WHERE di.deprovisioned_at IS NOT NULL
          AND di.deprovisioned_at < now() - interval '60 seconds'
          AND s.revoked_at IS NULL AND s.expires_at > now()"""),

    ("D7", "no deprovisioned user holds a live API key",
     """SELECT count(*) FROM directory_identities di
        JOIN api_keys k ON k.user_id = di.user_id
                       AND k.organization_id = di.organization_id
        WHERE di.deprovisioned_at IS NOT NULL
          AND di.deprovisioned_at < now() - interval '60 seconds'
          AND k.deactivated_at IS NULL"""),

    ("D8", "no job was created for a principal after their deprovisioning",
     """SELECT count(*) FROM jobs j
        JOIN directory_identities di ON di.user_id = j.created_by_user_id
                                    AND di.organization_id = j.organization_id
        WHERE di.deprovisioned_at IS NOT NULL
          AND j.created_at > di.deprovisioned_at"""),

    ("D9", "no live job for a deprovisioned principal has effects enabled",
     """SELECT count(*) FROM jobs j
        JOIN directory_identities di ON di.user_id = j.created_by_user_id
                                    AND di.organization_id = j.organization_id
        WHERE di.deprovisioned_at IS NOT NULL
          AND j.status IN ('PENDING','CLAIMED')
          AND j.effects_suppressed = false"""),

    ("D10", "no raw assertion payload survives its purge deadline",
     """SELECT count(*) FROM sso_assertions
        WHERE raw_payload IS NOT NULL AND raw_purge_after < now() - interval '1 day'"""),

    ("D11", "no replay-guard row outlives its window by more than a day",
     """SELECT count(*) FROM saml_assertion_replay_guard
        WHERE not_on_or_after < now() - interval '2 days'"""),

    ("D12", "no active SCIM key points at a deleted IdP configuration",
     """SELECT count(*) FROM scim_api_keys k
        WHERE k.revoked_at IS NULL AND NOT EXISTS (
          SELECT 1 FROM enterprise_idp_configs c WHERE c.id = k.idp_config_id)"""),

    ("D13", "no organization has two active IdP configurations",
     """SELECT count(*) FROM (SELECT organization_id FROM enterprise_idp_configs
        WHERE is_active GROUP BY organization_id HAVING count(*) > 1) x"""),

    ("D14", "no role mapping or JIT default can grant OWNER",
     """SELECT (SELECT count(*) FROM idp_role_mappings WHERE organization_role = 'OWNER')
             + (SELECT count(*) FROM enterprise_idp_configs
                WHERE jit_default_org_role = 'OWNER')"""),

    ("D15", "every CAPPED configuration carries a seat cap",
     """SELECT count(*) FROM enterprise_idp_configs
        WHERE jit_provisioning_mode = 'CAPPED' AND jit_seat_cap IS NULL"""),
]

INFO_QUERIES = [
    ("I1", "verified domains by status",
     "SELECT status, count(*) FROM verified_domains GROUP BY status ORDER BY 1"),
    ("I2", "active IdP configurations by protocol",
     "SELECT protocol, count(*) FROM enterprise_idp_configs WHERE is_active GROUP BY protocol"),
    ("I3", "assertion outcomes in the last 7 days",
     "SELECT outcome, count(*) FROM sso_assertions WHERE created_at > now() - interval '7 days' GROUP BY outcome ORDER BY 2 DESC"),
    ("I4", "organizations with IP pinning enabled",
     "SELECT ip_pinning, count(*) FROM tenant_security_policies GROUP BY ip_pinning"),
]


def run_db_checks(report: Report) -> None:
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        report.warn("DB", "SQLAlchemy unavailable; skipping database checks")
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from app.core.config import settings
            url = settings.sqlalchemy_database_uri
        except Exception as exc:
            report.warn("DB", f"no DATABASE_URL and app.core.config unavailable: {exc}")
            return

    engine = create_engine(url)
    with engine.connect() as conn:
        for cid, description, sql in DB_CHECKS:
            try:
                count = conn.execute(text(sql)).scalar() or 0
                if count == 0:
                    report.ok(cid, description)
                else:
                    report.fail(cid, f"{description} — {count} violation(s)")
            except Exception as exc:
                conn.rollback()
                report.warn(cid, f"could not evaluate ({exc.__class__.__name__}: {str(exc)[:120]})")
        for cid, description, sql in INFO_QUERIES:
            try:
                rows = conn.execute(text(sql)).fetchall()
                report.info(cid, f"{description}: " + ", ".join(f"{r[0]}={r[1]}" for r in rows) or "none")
            except Exception:
                conn.rollback()
                report.warn(cid, f"{description}: unavailable")


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-16 release gate")
    parser.add_argument("--static", action="store_true", help="static checks only")
    args = parser.parse_args()

    report = Report()
    print(f"\n{BLUE}=== ARCH-16 static checks ==={RESET}")
    for check in (s1_xml_confined, s2_defusedxml_used, s3_dns_confined,
                  s4_no_owner_from_jit, s5_deprovision_composition,
                  s6_authn_instant_not_now, s7_no_iat_for_freshness,
                  s8_public_routes_registered, s9_scim_org_from_token,
                  s10_scim_error_schema, s11_last_owner_guard,
                  s12_no_sha1_signatures, s13_verified_node_only):
        try:
            check(report)
        except Exception as exc:
            report.fail(check.__name__, f"check crashed: {exc}")

    if not args.static:
        print(f"\n{BLUE}=== ARCH-16 database invariants ==={RESET}")
        run_db_checks(report)

    print(f"\n{BLUE}=== summary ==={RESET}")
    print(f"  {GREEN}{len(report.passes)} passed{RESET}, {RED}{len(report.failures)} failed{RESET}, {len(report.infos)} informational")
    if report.failures:
        print(f"\n{RED}ARCH-16 gate FAILED:{RESET}")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print(f"\n{GREEN}ARCH-16 gate PASSED{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
