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


def _engine_isolation_coverage() -> set[str]:
    """Collections an engine test proves isolated with assert_workspace_isolated.

    PHASE 4. tests/engines/isolation.py fills workspace A through the real
    engines, then sweeps every list route of a second workspace for ANY id A
    owns and addresses A's objects through B's URLs. The helper itself fails
    when a named collection showed no row of A (a sweep over nothing), so a
    name listed in a call is a collection that was really exercised. A module
    that proves a collection with its own assertion declares it in
    ISOLATION_PROVEN_HERE. Read statically so this invariant needs no database.
    """
    import ast
    from pathlib import Path

    covered: set[str] = set()
    for path in (Path(__file__).resolve().parents[1] / "engines").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple)
                    and any(getattr(t, "id", None) == "ISOLATION_PROVEN_HERE" for t in node.targets)):
                covered |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "assert_workspace_isolated"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "collections" and isinstance(keyword.value, ast.Tuple):
                    covered |= {e.value for e in keyword.value.elts if isinstance(e, ast.Constant)}
                elif keyword.arg == "known" and isinstance(keyword.value, ast.Dict):
                    covered |= {k.value for k in keyword.value.keys if isinstance(k, ast.Constant)}
    return covered


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
    tested |= _engine_isolation_coverage()
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
