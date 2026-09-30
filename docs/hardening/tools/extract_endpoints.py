"""Static endpoint extractor for FlowPilot backend (Phase 0 mapping).

Parses app/api/v1/**.py with ast, finds every @<router>.<method>(path) handler,
resolves the mount prefix from router.py/main.py, and classifies auth, role,
plan-capability and tenant scope from the handler signature, decorator
dependencies, router-level dependencies and body (including same-module helper
functions such as _gate).
"""
import ast
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])  # backend/
API = ROOT / "app/api/v1"
METHODS = {"get", "post", "put", "patch", "delete", "websocket", "api_route"}

# (module relative path, router variable) -> mount prefix (below /api/v1 unless noted)
WS = "/workspaces/{workspace_id}"
MOUNTS = {
    ("health.py", "router"): "/api/v1/health",
    ("auth.py", "router"): "/api/v1/auth",
    ("scim.py", "router"): "",  # root mount, router has /scim/v2 prefix
    ("ingestion.py", "work_item_router"): f"/api/v1{WS}/work-items",
    ("work_items.py", "router"): f"/api/v1{WS}/work-items",
    ("ingestion.py", "session_router"): f"/api/v1{WS}/upload-sessions",
    ("ingestion.py", "batch_router"): f"/api/v1{WS}/ingestion-batches",
    ("ingestion.py", "preset_router"): f"/api/v1{WS}/document-presets",
    ("dashboard.py", "router"): f"/api/v1{WS}/dashboard",
    ("assistant.py", "router"): f"/api/v1{WS}/assistant",
    ("assistant_stream.py", "router"): f"/api/v1{WS}/assistant",
    ("assistant_sessions.py", "router"): f"/api/v1{WS}/assistant",
    ("automation.py", "router"): f"/api/v1{WS}/automation",
    ("notifications.py", "router"): f"/api/v1{WS}/notifications",
    ("ai_settings.py", "router"): f"/api/v1{WS}/ai-settings",
    ("email_settings.py", "router"): f"/api/v1{WS}/email-settings",
    ("document_settings.py", "router"): f"/api/v1{WS}/document-settings",
    ("upload.py", "router"): f"/api/v1{WS}/upload",
    ("usage.py", "workspace_router"): f"/api/v1{WS}/usage",
    ("verifications.py", "router"): f"/api/v1{WS}/verifications",
    ("review.py", "router"): f"/api/v1{WS}/review",
    ("review_collab.py", "router"): f"/api/v1{WS}/review",
    ("process_intel.py", "router"): f"/api/v1{WS}/process",
}
DEFAULT_PREFIX = "/api/v1"


def src(node):
    return ast.unparse(node) if node is not None else ""


def router_decls(tree):
    """router var -> (prefix, dependencies source)"""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = src(node.value.func)
            if fn.endswith("APIRouter"):
                prefix, deps = "", ""
                for kw in node.value.keywords:
                    if kw.arg == "prefix":
                        try:
                            prefix = ast.literal_eval(kw.value)
                        except Exception:
                            prefix = src(kw.value)
                    if kw.arg == "dependencies":
                        deps = src(kw.value)
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = (prefix, deps)
    return out


def module_aliases(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = src(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = src(node.value)
    return out


def expand(text, aliases, depth=2):
    for _ in range(depth):
        extra = []
        for name, val in aliases.items():
            if re.search(r"\b" + re.escape(name) + r"\b", text):
                extra.append(val)
        text = text + " " + " ".join(extra)
    return text


def module_helpers(tree):
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
    return funcs


CAP_RE = re.compile(r"require_capability(?:_for_organization)?\(|has_capability\(|require_addon\(|capability_gate\.|\b(?:gate|service)\.require\(db|_require_kind\(")
CAPKEY_RE = re.compile(r"(?:entitlements\.)?([A-Z_]+_CAPABILITY|ADDON_[A-Z_]+|capability\.[a-z_]+|addon\.[a-z_]+)")


def called_names(fn):
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def gate_info(fn, helpers, seen=None):
    """Return set of capability keys referenced by gating in fn or helpers it calls."""
    seen = seen or set()
    body = src(fn)
    keys = set()
    gated = bool(CAP_RE.search(body))
    if gated:
        keys |= set(CAPKEY_RE.findall(body))
    for name in called_names(fn):
        if name in helpers and name not in seen and name != fn.name:
            seen.add(name)
            g, k = gate_info(helpers[name], helpers, seen)
            if g:
                gated = True
                keys |= k
    return gated, keys


def classify(sig, deps, body, router_deps, path, mod):
    text = " ".join([sig, deps, router_deps])
    everything = text + " " + body
    auth = "unknown"
    if re.search(r"require_superadmin|SuperAdminUser|RequireSuperAdmin", everything):
        auth = "superadmin"
    elif re.search(r"require_api_key|PublicApiCtx", everything):
        auth = "api_key"
    elif re.search(r"scim|bearer_token|ScimAuth|require_scim", everything, re.I) and "scim" in mod:
        auth = "scim_token"
    elif re.search(r"CurrentUser|VerifiedUser|get_current_user|get_current_active_user|get_verified_user|OrgContext|WorkspaceCtx|Require(Org|Workspace)|get_(organization|workspace|billing_organization|sso_compliant_organization)_context|OrgAdminCtx|SSOCompliantOrgContext", text):
        auth = "user_jwt"
    elif re.search(r"signature|Stripe-Signature|webhook-signature|verify_signature|construct_event", everything, re.I) and ("webhook" in mod or "billing" in mod):
        auth = "signature"
    elif re.search(r"token", path) or re.search(r"token", sig):
        auth = "public_token"
    else:
        auth = "public"

    role = ""
    m = re.findall(r"RequireOrg(Owner|Admin|Member)|RequireWorkspace(Viewer|Contributor|Admin|Member)|RequireOrgRole\(\[([^\]]*)\]|RequireWorkspaceRole\(([^)]*)\)|OrgAdminCtx|get_billing_organization_context|RequireScope\(([^)]*)\)", text)
    roles = []
    for tup in m:
        a, b, c, d, e = tup
        if a:
            roles.append("org_" + a.lower())
        elif b:
            roles.append("ws_" + ("viewer" if b == "Member" else b.lower()))
        elif c:
            roles.append("org_roles:" + c.replace("OrganizationRole.", "").replace(" ", ""))
        elif d:
            roles.append("ws_role:" + d.replace("WorkspaceRole.", ""))
        elif e:
            roles.append("scope:" + e.strip())
    if "OrgAdminCtx" in text:
        roles.append("org_admin")
    if "get_billing_organization_context" in text:
        roles.append("org_billing_ctx")
    if auth == "superadmin":
        roles.append("superadmin")
    # in-body role checks
    if re.search(r"OrganizationRole\.OWNER|role != .?OWNER|_require_owner|require_owner", body):
        roles.append("body:owner-check")
    if re.search(r"is_superuser", body):
        roles.append("body:superuser-check")
    role = ";".join(dict.fromkeys(roles))

    scope = "none"
    if "{workspace_id}" in path or re.search(r"WorkspaceCtx|RequireWorkspace|get_workspace_context", text):
        scope = "workspace"
    elif re.search(r"\{organization_id\}|\{org_id\}|OrgContext|RequireOrg|get_organization_context|OrgAdminCtx|SSOCompliantOrgContext|get_billing_organization_context", path + text):
        scope = "organization"
    elif auth == "superadmin":
        scope = "platform(cross-tenant)"
    elif auth == "api_key":
        scope = "organization(api-key)"
    elif auth in ("user_jwt",):
        scope = "user"
    return auth, role, scope


def main():
    rows = []
    for py in sorted(API.rglob("*.py")):
        rel = str(py.relative_to(API))
        if rel in ("router.py", "__init__.py") or rel.endswith("/__init__.py"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8-sig"))
        routers = router_decls(tree)
        helpers = module_helpers(tree)
        aliases = module_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr not in METHODS or not isinstance(dec.func.value, ast.Name):
                    continue
                rvar = dec.func.value.id
                if rvar not in routers:
                    continue
                method = dec.func.attr.upper()
                path = ""
                if dec.args:
                    try:
                        path = ast.literal_eval(dec.args[0])
                    except Exception:
                        consts = {}
                        for k, v in aliases.items():
                            try:
                                consts[k] = ast.literal_eval(v)
                            except Exception:
                                pass
                        try:
                            path = eval(src(dec.args[0]), {"__builtins__": {}}, consts)
                        except Exception:
                            path = src(dec.args[0])
                dec_deps = ""
                status = ""
                for kw in dec.keywords:
                    if kw.arg == "path":
                        path = ast.literal_eval(kw.value)
                    if kw.arg == "dependencies":
                        dec_deps = src(kw.value)
                    if kw.arg == "methods":
                        method = "|".join(ast.literal_eval(kw.value))
                    if kw.arg == "status_code":
                        status = src(kw.value)
                rprefix, rdeps = routers[rvar]
                mount = MOUNTS.get((rel, rvar), DEFAULT_PREFIX)
                full = f"{mount}{rprefix}{path}"
                sig = expand(src(node.args), aliases)
                dec_deps = expand(dec_deps, aliases)
                body = src(node)
                gated, keys = gate_info(node, helpers)
                if gated and not any("CAPABILITY" in k or "capability" in k for k in keys):
                    modcap = aliases.get("CAPABILITY", "")
                    if modcap:
                        keys.add(modcap.split(".")[-1])
                    elif rel == "process_intel.py":
                        keys.add("PROCESS_INTELLIGENCE_CAPABILITY")
                    elif rel == "review_collab.py":
                        keys.add("COLLABORATIVE_REVIEW_CAPABILITY")
                auth, role, scope = classify(sig, dec_deps, body, expand(rdeps, aliases), full, rel)
                rows.append({
                    "module": rel,
                    "router": rvar,
                    "method": method,
                    "path": full,
                    "handler": node.name,
                    "auth": auth,
                    "roles": role,
                    "scope": scope,
                    "plan_gated": "yes" if gated else "no",
                    "capability": ";".join(sorted(k for k in keys if "capability" in k.lower() or "ADDON" in k or "addon" in k)),
                    "line": node.lineno,
                })
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)


if __name__ == "__main__":
    main()
