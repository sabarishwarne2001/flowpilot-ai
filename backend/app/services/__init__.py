"""
Business orchestration services registry for FlowPilot AI.

Exposes high-level service workflows so API routers can import one name
instead of a module path.

WHAT THIS PACKAGE DELIBERATELY DOES NOT IMPORT (ARCH-06 Step 1c / A.2.8)
------------------------------------------------------------------------
Three names were removed from the eager imports below and must not come back:

    embedding_service           -> app.services.embedding_service
    assistant_service           -> app.services.assistant_service
    process_document_pipeline   -> app.services.document_processor

Each one reaches the machine-learning stack, and importing ANYTHING from this
package executes this file, so every one of those imports was paid by every
importer:

    app.services -> document_processor -> bm25_service -> embedding_service
                                                       -> chromadb
                                                       -> sentence_transformers
    app.services -> assistant_service  -> retrieval_service -> embedding_service

That chain was charged to user_service, organization_service, ownership_mail,
and every test that touches them — none of which has anything to do with
embeddings. It blocked TestClient throughout ARCH-05 verification in a
network-restricted sandbox and passed in CI only because that environment has
network access or a warm model cache, which is an undocumented CI dependency.

REMOVING ONLY embedding_service IS NOT ENOUGH, AND LOOKS LIKE IT IS
-------------------------------------------------------------------
A.2.8 names the `embedding_service` line, and deleting that single line leaves
the chain fully intact via document_processor and assistant_service. All three
have to go, or the verification gate

    python -c "import app.services.user_service"

still loads chromadb. Verified by import-graph analysis over this package, not
by inspection of the one line the finding quoted.

Callers of the three removed names import their submodule directly, which is
what every ARCH-04 and ARCH-05 service already does. Two call sites were
updated in this step:

    app/api/v1/assistant.py
    app/api/v1/work_items.py

A LATER EDITOR ADDING AN IMPORT HERE
-------------------------------------
Before adding a name to this file, check what it pulls in transitively. The
rule is not "no ML services in __init__" — it is that this module is on the
import path of the entire application, so anything imported here is imported
always. tests/test_service_imports.py asserts the property directly.
"""

from app.services import organization_invitation_service
from app.services import organization_member_service
from app.services import organization_service
from app.services import ownership_mail
from app.services import user_service
from app.services import workspace_service

from app.services.auth_service import register_new_user, authenticate_user
from app.services.automation_service import automation_service
from app.services.chunking_service import split_text
from app.services.extraction_service import extract_text_from_document
from app.services.llm_service import llm_service
from app.services.notification.dispatcher import notification_dispatcher
from app.services.notification.email import EmailNotificationProvider
from app.services.ocr_service import ocr_service

# Backward-compatible alias
email_notification_provider = EmailNotificationProvider

__all__ = [
    "authenticate_user",
    "automation_service",
    "EmailNotificationProvider",
    "email_notification_provider",
    "extract_text_from_document",
    "llm_service",
    "notification_dispatcher",
    "ocr_service",
    "organization_invitation_service",
    "organization_member_service",
    "organization_service",
    "ownership_mail",
    "register_new_user",
    "split_text",
    "user_service",
    "workspace_service",
]