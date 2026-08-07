"""
Helper assertions for ARCH-02 multi-tenant data validation.
"""

def assert_workspace_scoped(
    client, *, token, owner_ws, foreign_ws, path,
    field: str, marker: str,
) -> None:
    """
    Asserts a marker row is visible in its own workspace and absent from
    another, in one call. Supports both dictionary envelopes and raw arrays.
    """
    headers = {"Authorization": f"Bearer {token}"}

    owned = client.get(f"/api/v1/workspaces/{owner_ws}/{path}", headers=headers)
    assert owned.status_code == 200, owned.text
    
    data = owned.json()
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    owned_values = [row[field] for row in items]
    assert marker in owned_values, (
        f"POSITIVE CONTROL FAILED: {marker!r} not visible in its own "
        f"workspace at {path}."
    )

    foreign = client.get(f"/api/v1/workspaces/{foreign_ws}/{path}", headers=headers)
    assert foreign.status_code == 200, foreign.text
    
    fdata = foreign.json()
    fitems = fdata["items"] if isinstance(fdata, dict) and "items" in fdata else fdata
    foreign_values = [row[field] for row in fitems]
    assert marker not in foreign_values, (
        f"ISOLATION BREACH: {marker!r} from workspace {owner_ws} is visible "
        f"in workspace {foreign_ws} at {path}."
    )