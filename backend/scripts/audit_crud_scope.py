"""
Automated isolation and workspace signature checker for FlowPilot AI CRUD layer.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import importlib
import inspect

TENANT_MODULES = (
    "work_item", "assistant", "automation", "notification", "job",
    "ai_settings", "email_settings", "document_settings",
)

# Explicit allowlist of pre-secured workspace scopes and organization-scoped queries.
SCOPE_INHERITED = {
    "assistant.create_conversation_message": "conversation fetched under scope",
    "assistant.get_conversation_messages":   "conversation fetched under scope",
    "assistant.delete_conversation_messages":"conversation fetched under scope",
    "assistant.update_conversation_title":   "conversation fetched under scope",
    "assistant.delete_conversation":         "conversation fetched under scope",
    "job.create_job":                        "work_item fetched under scope",
    "job.update_job":                        "job fetched under scope",
    "notification.update_notification_read_status":     "notification fetched under scope",
    "notification.update_notification_delivery_status": "notification fetched under scope",
    "notification.delete_notification":                 "notification fetched under scope",
    "notification.list_organization_scoped_for_user":  "organization-scoped notifications query where workspace_id is null",
}

failures = []
for name in TENANT_MODULES:
    module = importlib.import_module(f"app.crud.{name}")
    for fname, fn in vars(module).items():
        if fname.startswith("_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != module.__name__:
            continue

        params = inspect.signature(fn).parameters
        try:
            source = inspect.getsource(fn)
        except OSError:
            continue

        # Clean lookup against explicit allowlist
        key = f"{name}.{fname}"
        if key in SCOPE_INHERITED:
            continue

        if "db_obj" in params:
            continue
        if "db" not in params:
            continue

        if "workspace_id" not in params:
            failures.append(f"{name}.{fname}: no workspace_id parameter")
            continue
        if ".user_id ==" in source and ".workspace_id ==" not in source:
            failures.append(
                f"{name}.{fname}: filters on user_id without workspace_id"
            )

for f in failures:
    print("FAIL", f)

if failures:
    print(f"\n{len(failures)} isolation violation(s) found.")
    sys.exit(1)
else:
    print("\nSUCCESS: All CRUD signatures strictly workspace-scoped.")
    sys.exit(0)
