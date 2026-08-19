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
from app.services.embedding_service import embedding_service
from app.services.retrieval_service import retrieval_service
from app.services.chunk_retrieval_service import chunk_retrieval_service
from app.services.lexical_search_service import lexical_search_service
from app.services.hybrid_search_service import hybrid_search_service

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
    "embedding_service",
    "retrieval_service",
    "chunk_retrieval_service",
    "lexical_search_service",
    "hybrid_search_service",
]