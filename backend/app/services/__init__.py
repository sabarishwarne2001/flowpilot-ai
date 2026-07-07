"""
Business orchestration and AI pipeline services registry for FlowPilot AI.

Centralizes and exposes high-level service workflows (such as credential 
verifications, OCR, document parsing, text chunking, vector operations, 
LLM promptings, and document processing pipelines) to decouple API routers.
"""

from app.services.auth_service import register_new_user, authenticate_user
from app.services.assistant_service import assistant_service
from app.services.automation_service import automation_service
from app.services.chunking_service import split_text
from app.services.document_processor import process_document_pipeline
from app.services.embedding_service import embedding_service
from app.services.extraction_service import extract_text_from_document
from app.services.llm_service import llm_service
from app.services.notification.dispatcher import notification_dispatcher
from app.services.notification.email import EmailNotificationProvider
from app.services.ocr_service import ocr_service

# Backward-compatible alias
email_notification_provider = EmailNotificationProvider

__all__ = [
    "authenticate_user",
    "assistant_service",
    "automation_service",
    "EmailNotificationProvider",
    "email_notification_provider",
    "embedding_service",
    "extract_text_from_document",
    "llm_service",
    "notification_dispatcher",
    "ocr_service",
    "process_document_pipeline",
    "register_new_user",
    "split_text",
]