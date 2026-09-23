"""Database models centralized import and registry gateway for FlowPilot AI."""

from app.db.base import Base
from app.models.user import User
from app.models.work_item import WorkItem
from app.models.job import (
    Job,
    JobStatus,
    CLAIMABLE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from app.models.automation import AutomationRule, AutomationLog
# ARCH37-S1:models-registered
from app.models.automation_trigger import AutomationRuleTrigger
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
    AutomationNodeRun,
    AutomationNodeRunStatus,
    SUPPRESSED_STATUSES,
    TERMINAL_EXECUTION_STATUSES,
)
from app.models.verification import (
    BLOCKING_STATUSES,
    DisagreementKind,
    DocumentVerification,
    DocumentVerificationField,
    RELEASING_STATUSES,
    VerificationStatus,
)
from app.models.automation_graph import (
    AutomationEdge,
    AutomationNode,
    BRANCH_LABELS,
    GRAPH_VERSION_DAG,
    GRAPH_VERSION_FLAT,
    NODE_TYPES,
)
from app.models.notification import Notification
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from app.models.assistant import (
    Conversation,
    ConversationMessage,
    StreamState,
    FinishReason,
)
from app.models.email_settings import EmailSettings, EmailEncryption
from app.models.workspace import Workspace, WorkspaceMember
from app.models.organization_invitation import (
    OrganizationInvitation,
    InvitationWorkspaceGrant,
)
from app.models.ai_settings import AISettings
from app.models.document_settings import DocumentSettings
from app.models.settings_migration_archive import SettingsMigrationArchive
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.user_session import UserSession, SessionRevokedReason, AuthMethod
from app.models.api_key import ApiKey
from app.models.public_api import (
    ApiKeyUsageDaily,
    LATENCY_BOUNDS_MS,
    LATENCY_BUCKET_COUNT,
    bucket_index_for,
    empty_buckets,
)

from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
    MembershipStatus,
)
from app.models.ownership_transfer import (
    OwnershipTransfer,
    OwnershipTransferStatus,
)
from app.models.email_change_request import (
    EmailChangeRequest,
    EmailChangeStatus,
)
from app.models.uploaded_file import UploadedFile
from app.models.organization_email_settings import OrganizationEmailSettings
from app.models.audit_log import (
    AUDIT_ACTION_ENUM_NAME,
    AUDIT_RESOURCE_TYPE_ENUM_NAME,
    AuditAction,
    AuditLog,
    AuditResourceType,
)
from app.models.outbox_event import OutboxEvent, OutboxEventStatus, OutboxVisibility
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.models.webhook_delivery_attempt import WebhookDeliveryAttempt, AttemptDisposition

from app.models.usage_event import UsageEvent
from app.models.spend_limit import SpendLimit, SpendLimitPeriod
from app.models.document_chunk import DocumentChunk
from app.models.price_book import PriceBook, PriceBookEntry
from app.models.warehouse_sync import (  # noqa: F401
    ExportSchedule,
    ExportSyncRun,
    WarehouseDestination,
)
from app.models.usage_rollup import (
    NIL_UUID,
    TOTAL_EVENT_TYPE,
    RollupGrain,
    RollupGranularity,
    RollupWindow,
    RollupWindowStatus,
    UsageRollup,
)
from app.models.quota_tier import (
    OveragePolicy,
    QuotaTier,
    QuotaTierEntry,
    QuotaTierKey,
)
from app.models.stripe_inbound_event import (
    CLAIMABLE_STRIPE_INBOUND_STATUSES,
    STRIPE_INBOUND_STATUS_ENUM_NAME,
    StripeInboundEvent,
    StripeInboundStatus,
    TERMINAL_STRIPE_INBOUND_STATUSES,
)
from app.models.billing_account import BillingAccount
from app.models.subscription import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    LIVE_SUBSCRIPTION_STATUSES,
    SUBSCRIPTION_STATUS_ENUM_NAME,
    SUBSCRIPTION_STATUS_VALUES,
    Subscription,
    SubscriptionStatus,
)
from app.models.billable_seat import (
    BILLABLE_SEATS_VIEW_SQL,
    BillableSeat,
)
from app.models.invoice import (
    COLLECTIBLE_INVOICE_STATUSES,
    INVOICE_LINE_KIND_ENUM_NAME,
    INVOICE_STATUS_ENUM_NAME,
    INVOICE_STATUS_VALUES,
    Invoice,
    InvoiceLineItem,
    InvoiceLineKind,
    InvoiceStatus,
)
from app.models.supplier_cogs import (
    COST_BASIS_METHOD_VALUES,
    COST_BASIS_SOURCE_VALUES,
    METHOD_ARCH14_SELL_SIDE,
    METHOD_ARCH18_PRE_CONSOLIDATION,
    METHOD_ARCH18_SUPPLIER_COST,
    HARD_COST_BASIS_SOURCES,
    RECONCILIATION_STATUS_VALUES,
    SOURCE_ESTIMATED,
    SOURCE_MEASURED,
    SOURCE_SUPPLIER_RATE_CARD,
    SOURCE_ZERO_BYOK,
    STATUS_ACCEPTED,
    STATUS_INVESTIGATE,
    STATUS_MATCHED,
    SupplierInvoice,
    SupplierReconciliation,
)
from app.models.revenue_recognition import (
    RECOGNITION_METHOD_VALUES,
    RECOGNITION_REASON_VALUES,
    SCHEDULE_STATUS_VALUES,
    RecognitionMethod,
    RecognitionReason,
    RecognizedRevenueEntry,
    RevenueSchedule,
    RevenueScheduleStatus,
)
from app.models.dunning_action import (
    DUNNING_OUTCOME_ENUM_NAME,
    DUNNING_STEP_ENUM_NAME,
    DUNNING_STEP_ORDER,
    DunningAction,
    DunningOutcome,
    DunningStep,
)
from app.models.slo import (
    DEFAULT_LATENCY_BOUNDS_MS,
    SLO_METHOD_ENUM_NAME,
    SLO_UNIT_ENUM_NAME,
    SLO_WINDOW_ENUM_NAME,
    SLODefinition,
    SLOMeasurement,
    SLOMethod,
    SLOObservation,
    SLOUnit,
    SLOWindow,
)
from app.models.compliance import (
    AUDIT_RETENTION_FLOOR_DAYS,
    COMPLIANCE_EXPORT_STATUS_VALUES,
    DATA_RESIDENCY_REGION_VALUES,
    ERASED_EMAIL_DOMAIN,
    EXPORT_COMPLETE,
    EXPORT_EXPIRED,
    EXPORT_FAILED,
    EXPORT_PENDING,
    EXPORT_RUNNING,
    MINIMUM_RETENTION_DAYS,
    PINNED_REGIONS,
    REGION_APAC,
    REGION_EU,
    REGION_GLOBAL,
    REGION_US,
    TERMINAL_EXPORT_STATUSES,
    ComplianceExport,
    ErasedSubject,
    RetentionPolicy,
    erased_email_for,
)
from app.models.byok import (
    TenantModelRoute,
    TenantProviderCredential,
)
from app.models.document_role import DocumentRole  # noqa: F401
from app.models.procurement import (  # noqa: F401
    ProcurementCase,
    ProcurementCaseLine,
    ProcurementTolerancePolicy,
)
# ARCH-32 — zero-leakage geometric PII redaction.
#
# Imported here for the same reason every other mapped class is: the
# declarative registry has to know about a table before anything can query it,
# and a model that is only imported by the module that uses it produces a
# `NoSuchTableError` at the first cross-module relationship rather than at
# import.
from app.models.redaction import (  # noqa: F401
    RedactionJob,
    RedactionRegion,
)
# ARCH-33 — semantic assertion automation.
#
# Imported here for the same reason every other mapped class is: the
# declarative registry has to know about a table before anything can query it.
# AssertionEvaluation in particular holds relationships to
# DocumentVerification and AutomationNodeRun, and a model that is only
# imported by the module that uses it produces a `NoSuchTableError` at the
# first cross-module relationship rather than at import.
from app.models.assertion import (  # noqa: F401
    AssertionDefinition,
    AssertionEvaluation,
    AssertionRetrievalPhrase,
)
from app.models.radar import (  # noqa: F401
    AnomalyFinding,
    AnomalySuppression,
    DocumentFingerprint,
)
# ARCH38-S1:models-ingestion. Registered here so Alembic --autogenerate sees
# these tables; an unregistered model is a table autogenerate proposes to drop.
from app.models.review import ReviewAssignment  # ARCH40-S1:models-import
from app.models.workspace_email_override import WorkspaceEmailOverride
from app.models.ingestion import (  # noqa: F401
    DocumentSchemaPreset,
    IngestionBatch,
    IngestionBatchItem,
    RetentionHold,
    UploadSession,
    WorkItemTag,
    WorkspaceSchemaPreset,
)
from app.models.calibration import (  # noqa: F401
    CalibrationLabel,
    CalibrationModelVersion,
)
# ARCH39-S1:models-registered
from app.models.assistant_suite import (  # noqa: F401
    ConversationScopeItem,
    PromptTemplate,
)
from app.models.partner import (  # noqa: F401
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceManifest,
    MarketplaceSignature,
    Partner,
    PartnerMember,
    PartnerMemberRole,
    PartnerOrganization,
    PartnerPayoutPeriod,
    PartnerRevShareAgreement,
    PartnerRevShareLedger,
    PartnerSigningKey,
    RevShareBasisClass,
    PAYABLE_BASIS_CLASSES,
    REV_SHARE_BASIS_CLASS_VALUES,
    SETTLED_PERIOD_STATUSES,
)
from app.models.custom_domain import (
    CERTIFICATE_STATUS_VALUES,
    CHALLENGE_LABEL,
    CUSTOM_DOMAIN_STATUS_VALUES,
    RESOLVABLE_DOMAIN_STATUSES,
    CustomDomain,
)
from app.models.tenant_branding import (
    BRANDING_COLOR_TOKENS,
    COLOR_SCHEME_VALUES,
    SENDABLE_SENDER_STATUSES,
    SENDER_DOMAIN_STATUS_VALUES,
    TenantBranding,
)
from app.models.reconciliation import (
    CATEGORY_ORDER,
    DRIFT_ALERT_BPS,
    Attribution,
    FindingSeverity,
    ProviderStatement,
    ProviderStatementLine,
    ReconciliationCategory,
    ReconciliationFinding,
    ReconciliationRun,
    ReconciliationStatus,
    StatementGrain,
)
from app.models.identity import (
    AssertionOutcome,
    DirectoryIdentity,
    DomainStatus,
    EnterpriseIdpConfig,
    IdpProtocol,
    IdpRoleMapping,
    IdpSigningCertificate,
    IpPinningMode,
    JitProvisioningMode,
    ProvisionedVia,
    SamlAssertionReplayGuard,
    ScimApiKey,
    ScimGroup,
    ScimGroupMember,
    SsoAssertion,
    SsoAuthRequest,
    TenantSecurityPolicy,
    VerifiedDomain,
)

__all__ = [
    "Base",
    "User",
    "WorkItem",
    "Job",
    "JobStatus",
    "CLAIMABLE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "AutomationRule",
    "AutomationRuleTrigger",
    "AutomationLog",
    "Notification",
    "NotificationDelivery",
    "NotificationDeliveryStatus",
    "Conversation",
    "ConversationMessage",
    "StreamState",
    "FinishReason",
    "EmailSettings",
    "EmailEncryption",
    "Workspace",
    "WorkspaceMember",
    "OrganizationInvitation",
    "InvitationWorkspaceGrant",
    "AISettings",
    "DocumentSettings",
    "Organization",
    "OrganizationMember",
    "OrganizationRole",
    "OrganizationStatus",
    "MembershipStatus",
    "SettingsMigrationArchive",
    "AuthToken",
    "AuthTokenPurpose",
    "UserSession",
    "SessionRevokedReason",
    "ApiKey",
    "OwnershipTransfer",
    "OwnershipTransferStatus",
    "EmailChangeRequest",
    "EmailChangeStatus",
    "UploadedFile",
    "OrganizationEmailSettings",
    "AuditLog",
    "AuditAction",
    "AuditResourceType",
    "AUDIT_ACTION_ENUM_NAME",
    "AUDIT_RESOURCE_TYPE_ENUM_NAME",
    "OutboxEvent",
    "OutboxEventStatus",
    "WebhookEndpoint",
    "WebhookEndpointStatus",
    "WebhookDelivery",
    "WebhookDeliveryStatus",
    "WebhookDeliveryAttempt",
    "AttemptDisposition",
    "UsageEvent",
    "SpendLimit",
    "SpendLimitPeriod",
    "DocumentChunk",
    "PriceBook",
    "PriceBookEntry",
    "UsageRollup",
    "RollupWindow",
    "RollupGrain",
    "RollupGranularity",
    "RollupWindowStatus",
    "NIL_UUID",
    "TOTAL_EVENT_TYPE",
    "QuotaTier",
    "QuotaTierEntry",
    "QuotaTierKey",
    "OveragePolicy",
    "ProviderStatement",
    "ProviderStatementLine",
    "ReconciliationRun",
    "ReconciliationFinding",
    "ReconciliationCategory",
    "ReconciliationStatus",
    "StatementGrain",
    "COST_BASIS_SOURCE_VALUES",
    "HARD_COST_BASIS_SOURCES",
    "RECONCILIATION_STATUS_VALUES",
    "SOURCE_ESTIMATED",
    "SOURCE_MEASURED",
    "SOURCE_SUPPLIER_RATE_CARD",
    "SOURCE_ZERO_BYOK",
    "STATUS_ACCEPTED",
    "STATUS_INVESTIGATE",
    "STATUS_MATCHED",
    "SupplierInvoice",
    "SupplierReconciliation",
    "COST_BASIS_METHOD_VALUES",
    "METHOD_ARCH14_SELL_SIDE",
    "METHOD_ARCH18_PRE_CONSOLIDATION",
    "METHOD_ARCH18_SUPPLIER_COST",
    "RECOGNITION_METHOD_VALUES",
    "RECOGNITION_REASON_VALUES",
    "SCHEDULE_STATUS_VALUES",
    "RecognitionMethod",
    "RecognitionReason",
    "RecognizedRevenueEntry",
    "RevenueSchedule",
    "RevenueScheduleStatus",
    "Attribution",
    "FindingSeverity",
    "CATEGORY_ORDER",
    "DRIFT_ALERT_BPS",
    "AutomationExecution",
    "AutomationExecutionStatus",
    "AutomationNodeRun",
    "AutomationNodeRunStatus",
    "SUPPRESSED_STATUSES",
    "TERMINAL_EXECUTION_STATUSES",
    "AutomationEdge",
    "AutomationNode",
    "BRANCH_LABELS",
    "GRAPH_VERSION_DAG",
    "GRAPH_VERSION_FLAT",
    "NODE_TYPES",
    "OutboxVisibility",
    "BLOCKING_STATUSES",
    "DisagreementKind",
    "DocumentVerification",
    "DocumentVerificationField",
    "RELEASING_STATUSES",
    "VerificationStatus",
    "StripeInboundEvent",
    "StripeInboundStatus",
    "STRIPE_INBOUND_STATUS_ENUM_NAME",
    "CLAIMABLE_STRIPE_INBOUND_STATUSES",
    "TERMINAL_STRIPE_INBOUND_STATUSES",
    "BillingAccount",
    "Subscription",
    "SubscriptionStatus",
    "SUBSCRIPTION_STATUS_ENUM_NAME",
    "SUBSCRIPTION_STATUS_VALUES",
    "LIVE_SUBSCRIPTION_STATUSES",
    "ENTITLED_SUBSCRIPTION_STATUSES",
    "BillableSeat",
    "BILLABLE_SEATS_VIEW_SQL",
    "Invoice",
    "InvoiceLineItem",
    "InvoiceLineKind",
    "InvoiceStatus",
    "INVOICE_STATUS_ENUM_NAME",
    "INVOICE_STATUS_VALUES",
    "INVOICE_LINE_KIND_ENUM_NAME",
    "COLLECTIBLE_INVOICE_STATUSES",
    "VerifiedDomain",
    "DomainStatus",
    "EnterpriseIdpConfig",
    "IdpProtocol",
    "JitProvisioningMode",
    "IdpSigningCertificate",
    "IdpRoleMapping",
    "DirectoryIdentity",
    "ProvisionedVia",
    "ScimGroup",
    "ScimGroupMember",
    "ScimApiKey",
    "TenantSecurityPolicy",
    "IpPinningMode",
    "AuthMethod",
    "SsoAuthRequest",
    "SsoAssertion",
    "AssertionOutcome",
    "SamlAssertionReplayGuard",
    "DunningAction",
    "DunningStep",
    "DunningOutcome",
    "DUNNING_STEP_ORDER",
    "DUNNING_STEP_ENUM_NAME",
    "DUNNING_OUTCOME_ENUM_NAME",
    "SLODefinition",
    "SLOObservation",
    "SLOMeasurement",
    "SLOUnit",
    "SLOWindow",
    "SLOMethod",
    "SLO_UNIT_ENUM_NAME",
    "SLO_WINDOW_ENUM_NAME",
    "SLO_METHOD_ENUM_NAME",
    "DEFAULT_LATENCY_BOUNDS_MS",
    "ErasedSubject",
    "ComplianceExport",
    "RetentionPolicy",
    "DATA_RESIDENCY_REGION_VALUES",
    "COMPLIANCE_EXPORT_STATUS_VALUES",
    "PINNED_REGIONS",
    "TERMINAL_EXPORT_STATUSES",
    "REGION_US",
    "REGION_EU",
    "REGION_APAC",
    "REGION_GLOBAL",
    "EXPORT_PENDING",
    "EXPORT_RUNNING",
    "EXPORT_COMPLETE",
    "EXPORT_FAILED",
    "EXPORT_EXPIRED",
    "AUDIT_RETENTION_FLOOR_DAYS",
    "MINIMUM_RETENTION_DAYS",
    "ERASED_EMAIL_DOMAIN",
    "erased_email_for",
    "ApiKeyUsageDaily",
    "TenantProviderCredential",
    "TenantModelRoute",
    "LATENCY_BOUNDS_MS",
    "LATENCY_BUCKET_COUNT",
    "bucket_index_for",
    "empty_buckets",
    # ARCH-25 — white-label, custom domains and tenant branding.
    "CustomDomain",
    "TenantBranding",
    "CUSTOM_DOMAIN_STATUS_VALUES",
    "CERTIFICATE_STATUS_VALUES",
    "RESOLVABLE_DOMAIN_STATUSES",
    "CHALLENGE_LABEL",
    "SENDER_DOMAIN_STATUS_VALUES",
    "SENDABLE_SENDER_STATUSES",
    "COLOR_SCHEME_VALUES",
    "BRANDING_COLOR_TOKENS",
    # ARCH-31 — procurement three-way matching.
    #
    # DocumentRole maps the table arch31_step0_document_roles created; Step 0
    # shipped it without a mapped class because nothing read it yet.
    # candidates.py is the first reader.
    "DocumentRole",
    "ProcurementCase",
    "ProcurementCaseLine",
    "ProcurementTolerancePolicy",
    # ARCH-32 — zero-leakage geometric PII redaction.
    "RedactionJob",
    "RedactionRegion",
    # ARCH-33 — semantic assertion automation with confidence triage.
    "AssertionDefinition",
    "AssertionEvaluation",
    "AssertionRetrievalPhrase",
    # ARCH-34 — cross-document anomaly and duplicate radar.
    "AnomalyFinding",
    "AnomalySuppression",
    "DocumentFingerprint",
    # ARCH-35 — calibrated autonomy and conformal risk control.
    # ARCH-38 — batch ingestion and universal document intelligence.
    "DocumentSchemaPreset",
    "IngestionBatch",
    "IngestionBatchItem",
    "RetentionHold",
    "UploadSession",
    "WorkItemTag",
    "WorkspaceSchemaPreset",
    "CalibrationLabel",
    "CalibrationModelVersion",
    # ARCH-27 — partner marketplace, reseller tenancy and revenue share.
    "MarketplaceInstallation",
    "MarketplaceItem",
    "MarketplaceManifest",
    "MarketplaceSignature",
    "Partner",
    "PartnerMember",
    "PartnerMemberRole",
    "PartnerOrganization",
    "PartnerPayoutPeriod",
    "PartnerRevShareAgreement",
    "PartnerRevShareLedger",
    "PartnerSigningKey",
    # ARCH40-S1:models-registered. Both tables must be imported before
    # `Base.metadata` is used, or the composite foreign keys onto
    # `workspaces (id, organization_id)` and
    # `workspace_members (user_id, workspace_id)` resolve against a metadata
    # that does not yet contain their targets.
    "ReviewAssignment",
    "WorkspaceEmailOverride",
    "RevShareBasisClass",
    "PAYABLE_BASIS_CLASSES",
    "REV_SHARE_BASIS_CLASS_VALUES",
    "SETTLED_PERIOD_STATUSES",
]

# ARCH41-S2:models-registry
from app.models.extraction_memory import (  # noqa: E402,F401
    ExtractionAnchorRule,
    ExtractionExemplar,
    ExtractionMemoryApplication,
    ExtractionMemorySettings,
    ExtractionMemoryTrial,
    ExtractionTemplate,
    ExtractionTemplateMember,
)
