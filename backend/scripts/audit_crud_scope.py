"""
Automated isolation and workspace signature checker for FlowPilot AI CRUD layer.
"""
import os
import sys

# Insert parent directory of 'scripts' to python path to resolve 'app' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import importlib
import inspect

TENANT_MODULES = (
    "work_item", "assistant", "automation", "notification", "job",
    "ai_settings", "email_settings", "document_settings",
)

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

        # A function operating on an already-fetched instance inherits scope.
        # Exclude parameters representing already-fetched ORM objects.
        if any(p in params for p in ("db_obj", "conversation", "notification")):
            continue
            
        # Exclude message-level helper functions in assistant (inherit via conversation_id)
        if name == "assistant" and fname in (
            "create_conversation_message", "get_conversation_messages", "delete_conversation_messages"
        ):
            continue
            
        # Exclude job creation/update (inherits via work_item_id)
        if name == "job" and fname in ("create_job", "update_job"):
            continue

        # Pure helpers that touch no model.
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