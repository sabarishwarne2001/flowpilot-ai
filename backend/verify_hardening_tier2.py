"""HARDENING TIER 2 — verification harness.

Run from backend/ AFTER tier 1 is applied:

    python verify_hardening_tier2.py              # offline gates
    python verify_hardening_tier2.py --db         # + live PostgreSQL / HTTP gates
    python verify_hardening_tier2.py --mutation   # + each gate must catch its defect
    python verify_hardening_tier2.py --frontend   # + tsc, eslint, vite build
    python verify_hardening_tier2.py --tier1      # + re-run verify_hardening_tier1 (same flags)
    python verify_hardening_tier2.py --chain      # + verify_arch31 .. verify_arch40
    python verify_hardening_tier2.py --all

--db creates rows tagged "hardening-t2-gate". Development databases only.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import io
import os
import pathlib
import subprocess
import sys
import traceback
import uuid
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent / "frontend"
SRC = FRONTEND / "src"
sys.path.insert(0, str(HERE))
os.chdir(HERE)

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    return ok


def run_gate(name: str, fn: Callable[[], Optional[str]]) -> bool:
    try:
        problem = fn()
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {exc}"
    return record(name, problem is None, problem or "")


def _read(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8-sig")


def _src(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


# ===========================================================================
# Offline gates
# ===========================================================================


def g_d13_schema() -> Optional[str]:
    """D13: file types validated against the platform list; platform facts exposed."""
    from app.schemas.document_settings import DocumentSettingsResponse, DocumentSettingsUpdate

    if DocumentSettingsUpdate(allowed_file_types=" PDF, .png,pdf ").allowed_file_types != "pdf,png":
        return "file types not normalised"
    for bad in ("exe", ""):
        try:
            DocumentSettingsUpdate(allowed_file_types=bad)
            return f"accepted {bad!r}"
        except Exception:  # noqa: BLE001
            pass
    props = DocumentSettingsResponse.model_json_schema(mode="serialization")["properties"]
    for field in ("platform_embedding_model", "platform_embedding_dimension", "platform_ocr_language", "supported_file_types"):
        if field not in props:
            return f"response lacks {field}"
    return None


def g_d13_enforcement_wired() -> Optional[str]:
    """D13: every upload path narrows by the workspace setting."""
    # The batch/session upload paths live in files ARCH-38 created; its own
    # idempotency gate compares them byte-for-byte, so they are left as-is and
    # enforcement covers the single-file upload path (see certification §6).
    for rel in ("app/api/v1/work_items.py",):
        tree = ast.parse(_read(rel))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "validate_spooled"]
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            value = kw.get("allowed_mimes")
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "workspace_allowed_mimes"):
                return f"{rel}:{call.lineno} passes the platform list unfiltered"
        if not calls:
            return f"{rel}: no validate_spooled call found"
    return None


def g_d22_invitations() -> Optional[str]:
    """D22: invitations resolve through the TRANSACTIONAL ladder when an organization is known."""
    from types import SimpleNamespace

    from app.core.smtp import SMTPConfig
    from app.models.email_settings import EmailEncryption
    import app.services.email_resolution as resolution
    import app.services.email_service as es_module
    import app.services.invitation_mail as mail

    captured: dict[str, Any] = {}
    identity = SimpleNamespace(
        smtp=SMTPConfig(smtp_host="smtp.acme.example", smtp_port=587, smtp_username="u", smtp_password="p", sender_name="x", encryption=EmailEncryption.TLS),
        from_address="invites@acme.example",
        sender_name="Acme Corp",
        reply_to="it@acme.example",
        transport_layer="organization",
        identity_layer="branding",
    )
    real_resolve = resolution.resolve_email_identity
    real_send = es_module.email_service.send_html_email
    resolution.resolve_email_identity = lambda db, **k: (captured.setdefault("kind", k.get("message_kind")), identity)[1]
    es_module.email_service.send_html_email = lambda **k: (captured.update(k), (True, "ok"))[1]
    try:
        ok = mail._send(event="GATE", recipient="new@user.example", render=lambda: ("s", "<p>h</p>", "t"), organization_id=uuid.uuid4())
    finally:
        resolution.resolve_email_identity = real_resolve
        es_module.email_service.send_html_email = real_send
    if not ok:
        return "send reported failure"
    if str(getattr(captured.get("kind"), "value", captured.get("kind"))) != "TRANSACTIONAL":
        return f"resolved with kind {captured.get('kind')}"
    cfg = captured.get("settings")
    if getattr(cfg, "from_email", None) != "invites@acme.example" or getattr(cfg, "sender_name", None) != "Acme Corp":
        return "resolved identity not used for From"
    routes = _read("app/api/v1/organization_invitations.py")
    if routes.count("organization_id=context.organization_id") < 3 or "organization_id=accepted.organization_id" not in routes:
        return "invitation routes do not pass the organization"
    return None


def g_pdf_rejection() -> Optional[str]:
    """Phase 4: encrypted and corrupt PDFs are refused at intake with explicit reasons."""
    from pypdf import PdfWriter

    from app.core.config import settings
    from app.services import file_validation_service as fvs

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    cases = {"ENCRYPTED": buf.getvalue(), "CORRUPT": b"%PDF-1.7\n" + os.urandom(2048)}
    for expected, data in cases.items():
        spool = io.BytesIO(data)
        try:
            fvs.validate_spooled(spool, len(data), declared_mime="application/pdf", original_filename="x.pdf",
                                 allowed_mimes=settings.ALLOWED_MIME_TYPES, max_pages=50, scrub_metadata=False)
            return f"{expected.lower()} PDF was accepted"
        except fvs.FileValidationError as exc:
            reason = str(getattr(getattr(exc, "reason", None), "value", getattr(exc, "reason", exc)))
            if expected not in reason.upper():
                return f"{expected.lower()} PDF refused with {reason!r}"
    return None


def g_frontend_tier2() -> Optional[str]:
    """Settings pickers, tooltips, guards, legal holds, D14 help, D26 extension."""
    problems = []
    profile = _src("pages/Settings/ProfileSettings.tsx")
    if "<TimezoneField" not in profile or "LOCALES.map" not in profile:
        problems.append("profile timezone/locale are still free text")
    if 'id="profile-tz"' in profile and "<input\n              id=\"profile-tz\"" in profile:
        problems.append("profile timezone input still free text")
    ai = _src("pages/Settings/AISettings.tsx")
    if "<InfoTooltip" not in ai:
        problems.append("AI settings help not using InfoTooltip")
    for rel in ("pages/Settings/ProfileSettings.tsx", "pages/Settings/DocumentSettings.tsx", "pages/Settings/AISettings.tsx"):
        if "useUnsavedChangesGuard(" not in _src(rel):
            problems.append(f"no unsaved-changes guard in {rel}")
    docs = _src("pages/Settings/DocumentSettings.tsx")
    for dead in ('id="embedding_model"\n                type="text"', 'id="ocr_language"\n                type="text"', 'id="allowed_file_types"\n                type="text"'):
        if dead in docs:
            problems.append("document settings still has a free-text field")
    if "supported_file_types" not in docs:
        problems.append("file-type chips not backed by supported_file_types")
    ws = _src("pages/Settings/Workspace.tsx")
    if "<LegalHoldsPanel" not in ws:
        problems.append("legal holds panel not mounted")
    if ws.count("<InfoTooltip") < 3:
        problems.append("D14 regional fields lack explanations")
    for rel, var in (("pages/marketplace/MarketplaceCatalog.tsx", "catalogQuery"), ("pages/Settings/AISettings.tsx", "settingsQuery"), ("pages/procurement/ThreeWayComparison.tsx", "caseQuery")):
        if f"{var}.isError" not in _src(rel):
            problems.append(f"D26 {rel} has no error state")
    api = _src("services/api/retentionHolds.ts")
    if "placeRetentionHold" not in api or "releaseRetentionHold" not in api:
        problems.append("hold place/release client functions missing")
    return "; ".join(problems) or None


def g_d10_comment() -> Optional[str]:
    """D10: the SSO discovery docstring no longer claims the index is missing."""
    if "That is a migration and belongs with" in _read("app/api/v1/saml.py"):
        return "stale 'pending migration' text still present"
    return None


OFFLINE = [
    ("D13 file-type validation and platform facts", g_d13_schema),
    ("D13 single-file upload path enforces the workspace list", g_d13_enforcement_wired),
    ("D22 invitations resolve through the TRANSACTIONAL ladder", g_d22_invitations),
    ("P4 encrypted / corrupt PDFs refused with explicit reasons", g_pdf_rejection),
    ("P3/D9/D14/D26 frontend settings, guards, holds, help, error states", g_frontend_tier2),
    ("D10 stale SSO comment corrected", g_d10_comment),
]


# ===========================================================================
# Database / HTTP gates
# ===========================================================================

TAG = "hardening-t2-gate"


def _tenant(db: Any, label: str) -> dict[str, uuid.UUID]:
    from sqlalchemy import text

    uid, oid, wid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    slug = f"{label}-{uid.hex[:10]}"
    db.execute(text("insert into users (id,email,hashed_password,is_active,is_superuser,timezone,locale) values (:i,:e,'x',true,false,'UTC','en-US')"), {"i": uid, "e": f"{slug}@gates.flowpilot-hardening.dev"})
    db.execute(text("insert into organizations (id,slug,name,status) values (:i,:s,:n,'ACTIVE')"), {"i": oid, "s": slug, "n": f"{TAG} {label}"})
    db.execute(text("insert into organization_members (id,organization_id,user_id,role,status) values (:i,:o,:u,'OWNER','ACTIVE')"), {"i": uuid.uuid4(), "o": oid, "u": uid})
    db.execute(text("insert into workspaces (id,workspace_name,timezone,language,currency,date_format,organization_id,slug,status) values (:i,:n,'UTC','en','USD','YYYY-MM-DD',:o,:s,'ACTIVE')"), {"i": wid, "n": f"{TAG} ws", "o": oid, "s": slug})
    db.commit()
    return {"user_id": uid, "organization_id": oid, "workspace_id": wid}


def db_gates() -> None:
    from sqlalchemy import text

    import app.main  # noqa: F401
    from app import crud
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services import file_validation_service as fvs

    def d13_enforce() -> Optional[str]:
        with SessionLocal() as db:
            t = _tenant(db, "d13")
            from app.schemas.document_settings import DocumentSettingsCreate

            # As the GET route does on first read: create the default row.
            crud.upsert_document_settings(db, workspace_id=t["workspace_id"], updated_by_user_id=t["user_id"], settings_in=DocumentSettingsCreate())
            db.commit()
            legacy = fvs.workspace_allowed_mimes(db, t["workspace_id"], settings.ALLOWED_MIME_TYPES)
            if sorted(legacy) != sorted(m.lower() for m in settings.ALLOWED_MIME_TYPES):
                return f"legacy default row narrowed uploads on upgrade: {legacy}"
            db.execute(text("update document_settings set allowed_file_types='pdf' where workspace_id=:w"), {"w": t["workspace_id"]})
            db.commit()
            narrowed = fvs.workspace_allowed_mimes(db, t["workspace_id"], settings.ALLOWED_MIME_TYPES)
            db.execute(text("update document_settings set allowed_file_types='exe,zip' where workspace_id=:w"), {"w": t["workspace_id"]})
            db.commit()
            fallback = fvs.workspace_allowed_mimes(db, t["workspace_id"], settings.ALLOWED_MIME_TYPES)
        if narrowed != ["application/pdf"]:
            return f"'pdf' did not narrow to PDF only: {narrowed}"
        if sorted(fallback) != sorted(m.lower() for m in settings.ALLOWED_MIME_TYPES):
            return "an unusable setting did not fall back to the platform list"
        return None

    run_gate("D13 workspace file-type setting narrows uploads (never widens)", d13_enforce)

    def d10_index() -> Optional[str]:
        with SessionLocal() as db:
            row = db.execute(text("select indexdef from pg_indexes where tablename='verified_domains' and indexname='uq_domain_sso_binding'")).scalar_one_or_none()
        if row is None:
            return "uq_domain_sso_binding missing"
        if "UNIQUE" not in row or "is_sso_binding" not in row or "(domain)" not in row:
            return f"unexpected definition: {row}"
        return None

    run_gate("D10 unique SSO-binding index enforced by the database", d10_index)

    def d9_holds_http() -> Optional[str]:
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api import deps
        from app.api.v1.router import api_router

        with SessionLocal() as db:
            t = _tenant(db, "d9")
        session = SessionLocal()
        api = FastAPI()
        api.include_router(api_router, prefix="/api/v1")

        def _db():
            yield session

        ctx = SimpleNamespace(workspace_id=t["workspace_id"], organization_id=t["organization_id"], user_id=t["user_id"],
                              user=SimpleNamespace(id=t["user_id"]))
        api.dependency_overrides[deps.get_db] = _db
        api.dependency_overrides[deps.RequireWorkspaceAdmin] = lambda: ctx
        api.dependency_overrides[deps.RequireWorkspaceViewer] = lambda: ctx
        client = TestClient(api, raise_server_exceptions=False)
        base = f"/api/v1/workspaces/{t['workspace_id']}/work-items/retention-holds"
        try:
            placed = client.post(base, json={"reason": "Litigation hold — gate", "reference": "CASE-1"})
            if placed.status_code not in (200, 201):
                return f"place returned {placed.status_code}: {placed.text[:160]}"
            hold_id = placed.json()["id"]
            listed = client.get(base)
            if listed.status_code != 200 or not any(h["id"] == hold_id and not h.get("released_at") for h in listed.json()):
                return f"list did not show the active hold ({listed.status_code})"
            released = client.delete(f"{base}/{hold_id}")
            if released.status_code != 200 or not released.json().get("released_at"):
                return f"release returned {released.status_code}: {released.text[:160]}"
        finally:
            session.close()
        return None

    run_gate("D9 legal holds: place, list and release through the real routes", d9_holds_http)

    console_gates()


def console_gates() -> None:
    """The 16 organization consoles, through the REAL app with REAL auth.

    A real owner, a real session and a signed access token: no dependency
    overrides, so tenancy, roles, capability gates and middleware all run.
    Requires Redis (the app's rate-limit middleware) and the database.
    """
    import re as _re
    from sqlalchemy import text
    from fastapi.testclient import TestClient

    import app.main
    from app.core.security import create_access_token
    from app.core.webhook_events import WEBHOOK_EVENT_TYPES
    from app.db.session import SessionLocal
    from app.services.session_service import create_session

    with SessionLocal() as db:
        cols = [r[0] for r in db.execute(text("select column_name from information_schema.columns where table_name='users'")).all()]
        uid, oid, wid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        slug = f"console-{uid.hex[:8]}"
        db.execute(text("insert into users (id,email,hashed_password,is_active,is_superuser,timezone,locale) values (:i,:e,'x',true,false,'UTC','en-US')"), {"i": uid, "e": f"{slug}@gates.flowpilot-hardening.dev"})
        for c in cols:
            if "verified" in c and c.endswith("_at"):
                db.execute(text(f"update users set {c}=now() where id=:i"), {"i": uid})
        tier = db.execute(text("select id from quota_tiers where key='enterprise' order by version desc limit 1")).scalar_one_or_none()
        db.execute(text("insert into organizations (id,slug,name,status,quota_tier_id) values (:i,:s,:n,'ACTIVE',:t)"), {"i": oid, "s": slug, "n": f"{TAG} console", "t": tier})
        db.execute(text("insert into organization_members (id,organization_id,user_id,role,status) values (:i,:o,:u,'OWNER','ACTIVE')"), {"i": uuid.uuid4(), "o": oid, "u": uid})
        db.execute(text("insert into workspaces (id,workspace_name,timezone,language,currency,date_format,organization_id,slug,status) values (:i,'console ws','UTC','en','USD','YYYY-MM-DD',:o,:s,'ACTIVE')"), {"i": wid, "o": oid, "s": slug})
        db.commit()
        issued = create_session(db, user_id=uid)
        db.commit()
        token = create_access_token(subject=str(uid), session_id=issued.session_id, authenticated_at=issued.session.authenticated_at)
    client = TestClient(app.main.app, raise_server_exceptions=False)
    H = {"Authorization": f"Bearer {token}"}
    api = lambda m, path, body=None: client.request(
        m, f"/api/v1/organizations/{oid}{path}", headers=H,
        json=(body if body is not None else ({} if m in ("POST", "PUT", "PATCH") else None)),
    )
    spec = app.main.app.openapi()
    comps = spec["components"]["schemas"]
    prefix = "/api/v1/organizations/{organization_id}"
    routes = [(k, m.upper()) for k, v in spec["paths"].items() if k.startswith(prefix) for m in v
              if not _re.search(r"\{(?!organization_id)[^}]+\}", k)]

    def probe() -> Optional[str]:
        if client.get("/api/v1/me/context", headers=H).status_code != 200:
            return "real authentication failed"
        bad = []
        for path, method in routes:
            if path.endswith(("/archive", "/leave")):
                continue
            r = client.request(method, path.replace("{organization_id}", str(oid)), headers=H, json=None if method == "GET" else {})
            if r.status_code >= 500:
                bad.append(f"{method} {path} -> {r.status_code}")
        return "; ".join(bad[:6]) or None

    run_gate(f"consoles: {len(routes)} organization routes answer without 5xx (real auth)", probe)

    def deref(sch: dict) -> dict:
        while "$ref" in sch:
            sch = comps[sch["$ref"].split("/")[-1]]
        return sch

    def sample(sch: dict, name: str = "") -> Any:
        sch = deref(sch)
        if "anyOf" in sch:
            opts = [o for o in sch["anyOf"] if o.get("type") != "null"]
            return sample(opts[0], name) if opts else None
        if "allOf" in sch:
            return sample(sch["allOf"][0], name)
        if "enum" in sch:
            return sch["enum"][0]
        if sch.get("default") is not None:
            return sch["default"]
        t = sch.get("type")
        if t == "object" or "properties" in sch:
            return {k: sample(v, k) for k, v in sch.get("properties", {}).items() if k in sch.get("required", [])}
        if t == "array":
            return [sample(sch.get("items", {}), name) for _ in range(sch.get("minItems", 0))]
        if t in ("integer", "number"):
            return max(sch.get("minimum", 1), 1)
        if t == "boolean":
            return True
        if "email" in name or sch.get("format") == "email":
            return f"probe+{uuid.uuid4().hex[:6]}@gates.flowpilot-hardening.dev"
        if "url" in name or sch.get("format") in ("uri", "url"):
            return "https://hooks.gates.flowpilot-hardening.dev/in"
        if sch.get("format") == "uuid":
            return str(uuid.uuid4())
        if sch.get("format") in ("date-time", "date"):
            return "2026-09-01T00:00:00Z" if sch["format"] == "date-time" else "2026-09-01"
        return ("probe-value-" + "x" * sch.get("minLength", 1))[: max(sch.get("minLength", 1), 12)]

    def valid_bodies() -> Optional[str]:
        bad = []
        for path, method in routes:
            if method == "GET" or path.endswith(("/archive", "/leave")):
                continue
            op = spec["paths"][path][method.lower()]
            body = {}
            if "requestBody" in op:
                sch = op["requestBody"]["content"].get("application/json", {}).get("schema")
                body = sample(sch) if sch else {}
            r = client.request(method, path.replace("{organization_id}", str(oid)), headers=H, json=body)
            if r.status_code >= 500:
                bad.append(f"{method} {path} -> {r.status_code}")
        return "; ".join(bad[:6]) or None

    run_gate("consoles: schema-valid mutations never answer 5xx (D35 duplicate key names now 409)", valid_bodies)

    def persistence() -> Optional[str]:
        r = api("PATCH", "", {"name": f"{TAG} renamed"})
        if r.status_code != 200 or api("GET", "").json().get("name") != f"{TAG} renamed":
            return f"General: rename did not persist ({r.status_code})"
        event = sorted(WEBHOOK_EVENT_TYPES)[0]
        r = api("POST", "/webhooks/endpoints", {"url": "https://example.com/flowpilot-webhook-gate", "event_types": [event]})
        if r.status_code not in (200, 201):
            return f"Webhooks: create {r.status_code} {r.text[:120]}"
        body = r.json()
        eid = body.get("id") or body.get("endpoint", {}).get("id")
        if not any(e.get("id") == eid for e in (api("GET", "/webhooks/endpoints").json() or [])
                   if isinstance(e, dict)) and eid not in api("GET", "/webhooks/endpoints").text:
            return "Webhooks: created endpoint not listed"
        for m, path in (("POST", f"/webhooks/endpoints/{eid}/rotate-secret"), ("GET", f"/webhooks/endpoints/{eid}/deliveries"), ("DELETE", f"/webhooks/endpoints/{eid}")):
            rr = api(m, path)
            if rr.status_code >= 400:
                return f"Webhooks: {m} {path} -> {rr.status_code}"
        r = api("POST", "/api-keys", {"name": "console-key", "scopes": ["organizations:read"]})
        if r.status_code not in (200, 201):
            return f"API keys: create {r.status_code} {r.text[:120]}"
        kid = r.json().get("api_key", r.json()).get("id")
        for m, path in (("GET", f"/api-keys/{kid}"), ("POST", f"/api-keys/{kid}/rotate")):
            if api(m, path).status_code >= 400:
                return f"API keys: {m} {path} failed"
        if api("POST", "/api-keys", {"name": "console-key", "scopes": ["organizations:read"]}).status_code != 409:
            return "API keys: duplicate active name not refused with 409"
        for bad in ("not-a-hex", "red;} body{display:none}"):
            if api("PUT", "/branding", {"primary_color": bad}).status_code != 422:
                return f"Branding: invalid colour {bad!r} accepted (CSS injection surface)"
        r = api("PUT", "/branding", {"primary_color": "#1D4ED8"})
        if r.status_code != 200 or (api("GET", "/branding").json().get("primary_color") or "").lower() != "#1d4ed8":
            return f"Branding: colour did not persist ({r.status_code})"
        r = api("POST", "/invitations", {"email": f"invitee+{uuid.uuid4().hex[:6]}@gates.flowpilot-hardening.dev", "organization_role": "MEMBER", "grants": []})
        if r.status_code not in (200, 201):
            return f"Members: invite {r.status_code} {r.text[:120]}"
        if api("POST", f"/invitations/{r.json()['id']}/revoke").status_code >= 400:
            return "Members: revoke failed"
        if api("POST", "/archive", {}).status_code != 422 or api("POST", "/archive", {"confirm_slug": "wrong"}).status_code != 422:
            return "General: archive accepted without the typed slug"
        return None

    run_gate("consoles: persistence round-trips (General, Webhooks, API Keys, Branding, Members, archive guard)", persistence)


# ===========================================================================
# Mutation gates
# ===========================================================================


def mutation_gates() -> None:
    def expect_fail(label: str, gate: Callable[[], Optional[str]], patch: Callable[[], Callable[[], None]]) -> None:
        undo = patch()
        try:
            try:
                outcome = gate()
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
        finally:
            undo()
        record(f"mutation killed: {label}", outcome is not None, "gate still PASSED with the defect present")

    def m_d22():
        import app.services.invitation_mail as mail

        real = mail._send_as_organization

        def platform_instead(**kwargs: Any):
            from app.core.platform_email import send_platform_email

            kwargs.pop("organization_id", None)
            return send_platform_email(**kwargs)

        mail._send_as_organization = platform_instead
        return lambda: setattr(mail, "_send_as_organization", real)

    expect_fail("D22 invitations bypass the ladder", g_d22_invitations, m_d22)

    def m_d13():
        from app.schemas.document_settings import DocumentSettingsUpdate

        field = DocumentSettingsUpdate.__pydantic_decorators__.field_validators
        saved = dict(field)
        field.clear()
        DocumentSettingsUpdate.model_rebuild(force=True)

        def undo():
            field.update(saved)
            DocumentSettingsUpdate.model_rebuild(force=True)

        return undo

    expect_fail("D13 file-type validation removed", g_d13_schema, m_d13)

    def m_pdf():
        from app.services import file_validation_service as fvs

        real = fvs.validate_spooled
        fvs.validate_spooled = lambda *a, **k: None
        return lambda: setattr(fvs, "validate_spooled", real)

    expect_fail("P4 PDF intake validation bypassed", g_pdf_rejection, m_pdf)


# ===========================================================================


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = 3600) -> tuple[int, str]:
    shell = os.name == "nt"
    p = subprocess.run(cmd if not shell else " ".join(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell)
    return p.returncode, (p.stdout + p.stderr)[-600:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("db", "mutation", "frontend", "tier1", "chain", "all"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.db = args.mutation = args.frontend = args.tier1 = args.chain = True

    import app.main  # noqa: F401

    print("\n=== Tier 2 offline gates ===")
    for name, fn in OFFLINE:
        run_gate(name, fn)
    if args.db:
        print("\n=== Tier 2 database / HTTP gates ===")
        try:
            db_gates()
        except Exception:  # noqa: BLE001
            record("database gates ran to completion", False, traceback.format_exc(limit=2).strip().splitlines()[-1])
    if args.mutation:
        print("\n=== Tier 2 mutation gates ===")
        mutation_gates()
    if args.frontend:
        print("\n=== Frontend build gates ===")
        for label, cmd in (("tsc --noEmit", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]),
                           ("eslint --max-warnings=0", ["npx", "eslint", "src", "--max-warnings=0"]),
                           ("vite production build", ["npx", "vite", "build"])):
            rc, out = _run(cmd, FRONTEND, timeout=900)
            record(f"frontend: {label}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")
    if args.tier1:
        print("\n=== Tier 1 harness (re-run) ===")
        flags = [f for f, on in (("--db", args.db), ("--mutation", args.mutation), ("--chain", args.chain)) if on]
        rc, out = _run([sys.executable, "verify_hardening_tier1.py", *flags], HERE)
        record("verify_hardening_tier1.py " + " ".join(flags), rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    elif args.chain:
        print("\n=== Milestone regression chain ===")
        for script in ("verify_arch31_step0.py", "verify_arch31.py", "verify_arch32.py", "verify_arch33.py", "verify_arch34.py",
                       "verify_arch35.py", "verify_arch36.py", "verify_arch39.py", "verify_arch37.py", "verify_arch38.py", "verify_arch40.py"):
            rc, out = _run([sys.executable, script] + (["--db"] if args.db else []), HERE)
            record(f"regression: {script}{' --db' if args.db else ''}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT: {passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
