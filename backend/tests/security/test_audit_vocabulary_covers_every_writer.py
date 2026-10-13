"""Every audit write names a resource type and action the audit log can store (campaign session 3, F-294).

The identity services write audit rows through `_integration.write_audit`, which swallows any error
from `audit_service.record` and only logs it. They named resource types the audit vocabulary does
not have (`TENANT_SECURITY_POLICY`, `ENTERPRISE_IDP_CONFIG`, `IDP_CERTIFICATE`, `SCIM_API_KEY`,
`VERIFIED_DOMAIN`, `DIRECTORY_IDENTITY`, ...), so every one of those writes failed and was dropped:
the sign-in policy, identity providers, signing certificates, SCIM keys, verified domains and
directory provisioning never reached the append-only audit log.

This walks `app/` for every audit write with a literal resource type or action and checks each
against `AuditResourceType` and `AuditAction` (and so against the database enums, which the
migrations keep equal to them).
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.audit_log import AuditAction, AuditResourceType

APP = Path(__file__).resolve().parents[2] / "app"
WRITERS = {"record", "record_independent", "write_audit", "_audit", "audit"}


def _literals() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name not in WRITERS:
                continue
            for keyword in node.keywords:
                if keyword.arg in ("resource_type", "action") and isinstance(keyword.value, ast.Constant) \
                        and isinstance(keyword.value.value, str):
                    where = f"app/{path.relative_to(APP).as_posix()}:{node.lineno}"
                    found.append((where, keyword.arg, keyword.value.value))
    return found


def test_the_scan_sees_the_identity_writers() -> None:
    assert any("identity" in where for where, _, _ in _literals())


def test_every_literal_audit_resource_type_and_action_exists() -> None:
    unknown = [
        f"{where} {field}={value!r}"
        for where, field, value in _literals()
        if (field == "resource_type" and value not in AuditResourceType.__members__)
        or (field == "action" and value not in AuditAction.__members__)
    ]
    assert unknown == []
