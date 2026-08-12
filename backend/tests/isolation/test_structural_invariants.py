"""
Structural and pipeline configuration invariants.
"""

def test_crud_signatures_are_workspace_scoped():
    import subprocess
    import sys
    res = subprocess.run([sys.executable, "scripts/audit_crud_scope.py"])
    assert res.returncode == 0


def test_all_tenant_routes_are_workspace_scoped():
    import subprocess
    import sys
    res = subprocess.run([sys.executable, "scripts/audit_routes.py"])
    assert res.returncode == 0


def test_vector_collections_are_workspace_partitioned():
    import subprocess
    import sys
    res = subprocess.run([sys.executable, "scripts/audit_vector_isolation.py"])
    assert res.returncode == 0


def test_tenant_context_is_constructed_only_in_deps():
    import pathlib
    offenders = [
        str(path) for path in pathlib.Path("app").rglob("*.py")
        if path.name != "deps.py"
        and "TenantContext(" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"TenantContext fabricated in: {offenders}"


def test_every_scoped_collection_has_an_isolation_test():
    from app.main import app
    from tests.isolation.test_data_isolation import SCOPED_COLLECTIONS

    prefix = "/api/v1/workspaces/{workspace_id}/"
    live = {
        route.path.removeprefix(prefix).split("/")[0]
        for route in app.routes
        if getattr(route, "path", "").startswith(prefix)
    }
    tested = {path.split("/")[0] for path, *_ in SCOPED_COLLECTIONS}
    exemptions = {
        "ai-settings",
        "email-settings",
        "document-settings",
        "dashboard",
        "logo",
        "upload",
        "leave",
        "restore",
        "members",
        "archive",
        "slug-available",
    }
    untested = live - tested - exemptions
    assert not untested, (
        f"Workspace-scoped collections with no isolation test: {untested}"
    )