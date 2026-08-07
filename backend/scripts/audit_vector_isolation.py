"""
Automated Vector Isolation validation script for FlowPilot AI.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.embedding_service import embedding_service

failures = []
for collection in embedding_service.client.list_collections():
    name = collection.name
    if not name.startswith("ws_"):
        failures.append(f"non-workspace collection present: {name}")
        continue

    expected = name.removeprefix("ws_")
    payload = collection.get(include=["metadatas"])
    for meta in payload["metadatas"]:
        actual = (meta or {}).get("workspace_id")
        if actual != expected:
            failures.append(f"{name}: vector tagged workspace {actual}")
            break

for f in failures:
    print("FAIL", f)

if failures:
    print(f"\n{len(failures)} vector isolation violation(s) found.")
    sys.exit(1)
else:
    print("\nSUCCESS: All vector collections strictly workspace-isolated.")
    sys.exit(0)