"""
Automated API Routing validation script for FlowPilot AI.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app

SCOPED_ROUTERS = (
    "work-items", "dashboard", "assistant", "automation",
    "notifications", "ai-settings", "email-settings", "document-settings"
)

ORGANIZATION_SCOPED_PREFIX = "/organizations/{organization_id}/"

failures = []
for route in app.routes:
    path = getattr(route, "path", "")
    if any(f"/{seg}" in path for seg in SCOPED_ROUTERS):
        if ORGANIZATION_SCOPED_PREFIX in path:
            continue
        if "/workspaces/{workspace_id}/" not in path:
            failures.append(f"unscoped: {path}")

for f in failures:
    print("FAIL", f)

if failures:
    print(f"\n{len(failures)} unscoped tenant route(s) found.")
    sys.exit(1)
else:
    print("\nSUCCESS: All tenant routing fully workspace-scoped.")
    sys.exit(0)
