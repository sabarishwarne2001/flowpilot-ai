/**
 * Centralized API path construction for FlowPilot AI.
 */

const seg = (value: string): string => encodeURIComponent(value);

const ws = (workspaceId: string): string => {
  if (!workspaceId) {
    throw new Error(
      "A workspaceId is required to build this URL. Gate the query with `enabled: Boolean(workspaceId)`.",
    );
  }
  return encodeURIComponent(workspaceId);
};

const org = (organizationId: string): string => {
  if (!organizationId) {
    throw new Error("An organizationId is required to build this URL.");
  }
  return encodeURIComponent(organizationId);
};

const scoped = (workspaceId: string): string => `/workspaces/${ws(workspaceId)}`;
const webhookBase = (organizationId: string): string =>
  `/organizations/${org(organizationId)}/webhooks`;

export const ME_ENDPOINTS = {
  context: "/me/context",
  organizations: "/me/organizations",
  workspaces: "/me/workspaces",
} as const;

export const ORGANIZATION_ENDPOINTS = {
  create: "/organizations",
  slugAvailable: "/organizations/slug-available",
  detail: (organizationId: string): string => `/organizations/${seg(organizationId)}`,
  archive: (organizationId: string): string => `/organizations/${seg(organizationId)}/archive`,
  leave: (organizationId: string): string => `/organizations/${seg(organizationId)}/leave`,
  transferOwnership: (organizationId: string): string => `/organizations/${seg(organizationId)}/transfer-ownership`,
  workspaces: (organizationId: string): string => `/organizations/${seg(organizationId)}/workspaces`,
  members: (organizationId: string): string => `/organizations/${seg(organizationId)}/members`,
  member: (organizationId: string, membershipId: string): string => `/organizations/${seg(organizationId)}/members/${seg(membershipId)}`,
  deactivateMember: (organizationId: string, membershipId: string): string => `/organizations/${seg(organizationId)}/members/${seg(membershipId)}/deactivate`,
} as const;

export const ORG_EMAIL_ENDPOINTS = {
  settings: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/email-settings`,
  test: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/email-settings/test`,
} as const;

export const ORG_NOTIFICATION_ENDPOINTS = {
  list: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/notifications`,
} as const;

export const KNOWLEDGE_ENDPOINTS = {
  reindex: (workspaceId: string): string =>
    `${scoped(workspaceId)}/work-items/knowledge-base/reindex`,
} as const;

export const PROFILE_ENDPOINTS = {
  profile: "/me/profile",
  avatar: "/me/avatar",
  userAvatar: (userId: string): string => `/users/${seg(userId)}/avatar`,
} as const;

export const EMAIL_CHANGE_ENDPOINTS = {
  request: "/me/email-change/request",
  confirm: "/auth/email-change/confirm",
} as const;

export const ME_INVITATION_ENDPOINTS = {
  mine: "/me/invitations",
} as const;

export const OWNERSHIP_ENDPOINTS = {
  transfers: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/ownership-transfers`,
  accept: (organizationId: string, transferId: string): string =>
    `/organizations/${org(organizationId)}/ownership-transfers/${seg(transferId)}/accept`,
  decline: (organizationId: string, transferId: string): string =>
    `/organizations/${org(organizationId)}/ownership-transfers/${seg(transferId)}/decline`,
  cancel: (organizationId: string, transferId: string): string =>
    `/organizations/${org(organizationId)}/ownership-transfers/${seg(transferId)}/cancel`,
  mine: "/me/ownership-transfers",
} as const;

export const API_KEY_ENDPOINTS = {
  list: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/api-keys`,
  create: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/api-keys`,
  detail: (organizationId: string, keyId: string): string =>
    `/organizations/${seg(organizationId)}/api-keys/${seg(keyId)}`,
  rotate: (organizationId: string, keyId: string): string =>
    `/organizations/${seg(organizationId)}/api-keys/${seg(keyId)}/rotate`,
  revoke: (organizationId: string, keyId: string): string =>
    `/organizations/${seg(organizationId)}/api-keys/${seg(keyId)}`,
} as const;

export const WEBHOOK_ENDPOINTS = {
  endpoints: (organizationId: string): string =>
    `${webhookBase(organizationId)}/endpoints`,
  endpoint: (organizationId: string, endpointId: string): string =>
    `${webhookBase(organizationId)}/endpoints/${seg(endpointId)}`,
  rotateSecret: (organizationId: string, endpointId: string): string =>
    `${webhookBase(organizationId)}/endpoints/${seg(endpointId)}/rotate-secret`,
  deliveries: (organizationId: string, endpointId: string): string =>
    `${webhookBase(organizationId)}/endpoints/${seg(endpointId)}/deliveries`,
  attempts: (organizationId: string, deliveryId: string): string =>
    `${webhookBase(organizationId)}/deliveries/${seg(deliveryId)}/attempts`,
  redeliver: (organizationId: string, deliveryId: string): string =>
    `${webhookBase(organizationId)}/deliveries/${seg(deliveryId)}/redeliver`,
} as const;

export const WORKSPACE_ENDPOINTS = {
  detail: (workspaceId: string): string => scoped(workspaceId),
  logo: (workspaceId: string): string => `${scoped(workspaceId)}/logo`,
  uploadLogo: (workspaceId: string): string => `${scoped(workspaceId)}/upload/logo`,
  slugAvailable: (workspaceId: string): string => `${scoped(workspaceId)}/slug-available`,
  archive: (workspaceId: string): string => `${scoped(workspaceId)}/archive`,
  restore: (workspaceId: string): string => `${scoped(workspaceId)}/restore`,
  leave: (workspaceId: string): string => `${scoped(workspaceId)}/leave`,
  members: (workspaceId: string): string => `${scoped(workspaceId)}/members`,
  member: (workspaceId: string, membershipId: string): string => `${scoped(workspaceId)}/members/${seg(membershipId)}`,
  revokeMember: (workspaceId: string, membershipId: string): string => `${scoped(workspaceId)}/members/${seg(membershipId)}/revoke`,
} as const;

export const INVITATION_ENDPOINTS = {
  list: (organizationId: string): string => `/organizations/${org(organizationId)}/invitations`,
  create: (organizationId: string): string => `/organizations/${org(organizationId)}/invitations`,
  revoke: (organizationId: string, invitationId: string): string => `/organizations/${org(organizationId)}/invitations/${seg(invitationId)}/revoke`,
  resend: (organizationId: string, invitationId: string): string => `/organizations/${org(organizationId)}/invitations/${seg(invitationId)}/resend`,
  preview: "/invitations/preview",
  accept: "/invitations/accept",
  reject: "/invitations/reject",
} as const;

export const SETTINGS_ENDPOINTS = {
  aiSettings: (workspaceId: string): string => `${scoped(workspaceId)}/ai-settings`,
  aiSettingsModels: (workspaceId: string): string => `${scoped(workspaceId)}/ai-settings/models`,
  aiSettingsProviders: (workspaceId: string): string => `${scoped(workspaceId)}/ai-settings/providers`,
  aiSettingsTest: (workspaceId: string): string => `${scoped(workspaceId)}/ai-settings/test`,
  emailSettings: (workspaceId: string): string => `${scoped(workspaceId)}/email-settings`,
  emailSettingsTest: (workspaceId: string): string => `${scoped(workspaceId)}/email-settings/test`,
  documentSettings: (workspaceId: string): string => `${scoped(workspaceId)}/document-settings/`,
} as const;

export const WORK_ITEM_ENDPOINTS = {
  list: (workspaceId: string): string => `${scoped(workspaceId)}/work-items`,
  upload: (workspaceId: string): string => `${scoped(workspaceId)}/work-items`,
  details: (workspaceId: string, workItemId: string): string =>
    `${scoped(workspaceId)}/work-items/${seg(workItemId)}`,
  reprocess: (workspaceId: string, workItemId: string): string =>
    `${scoped(workspaceId)}/work-items/${seg(workItemId)}/reprocess`,
  remove: (workspaceId: string, workItemId: string): string =>
    `${scoped(workspaceId)}/work-items/${seg(workItemId)}`,
} as const;

export const ASSISTANT_ENDPOINTS = {
  conversations: (workspaceId: string): string =>
    `${scoped(workspaceId)}/assistant/conversations`,
  conversation: (workspaceId: string, conversationId: string): string =>
    `${scoped(workspaceId)}/assistant/conversations/${seg(conversationId)}`,
  messages: (workspaceId: string, conversationId: string): string =>
    `${scoped(workspaceId)}/assistant/conversations/${seg(conversationId)}/messages`,
  documentConversation: (workspaceId: string, workItemId: string): string =>
    `${scoped(workspaceId)}/assistant/documents/${seg(workItemId)}/conversation`,
} as const;

export const DASHBOARD_ENDPOINTS = {
  overview: (workspaceId: string): string => `${scoped(workspaceId)}/dashboard/overview`,
} as const;

export const AUTOMATION_ENDPOINTS = {
  rules: (workspaceId: string): string => `${scoped(workspaceId)}/automation/rules`,
  rule: (workspaceId: string, ruleId: string): string =>
    `${scoped(workspaceId)}/automation/rules/${seg(ruleId)}`,
  logs: (workspaceId: string): string => `${scoped(workspaceId)}/automation/logs`,
} as const;

export const NOTIFICATION_ENDPOINTS = {
  list: (workspaceId: string): string => `${scoped(workspaceId)}/notifications`,
  detail: (workspaceId: string, notificationId: string): string =>
    `${scoped(workspaceId)}/notifications/${seg(notificationId)}`,
  markAllRead: (workspaceId: string): string =>
    `${scoped(workspaceId)}/notifications/mark-all-read`,
} as const;

/** ARCH-17 — per-tenant SLO targets and compliance. */
export const SLO_ENDPOINTS = {
  list: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/slos`,
  detail: (organizationId: string, sloKey: string): string =>
    `/organizations/${seg(organizationId)}/slos/${seg(sloKey)}`,
} as const;

/**
 * ARCH-20 — data governance, residency and compliance.
 *
 * Organization-scoped, unlike COGS_ENDPOINTS below: compliance is one
 * tenant's own data and is read by that tenant's owner or admin, not across
 * tenants by a superadmin.
 */
export const COMPLIANCE_ENDPOINTS = {
  overview: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/compliance`,
  residency: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/compliance/residency`,
  retention: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/compliance/retention`,
  erasures: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/compliance/erasures`,
  erasurePreview: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/compliance/erasures/preview`,
  exports: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/compliance/exports`,
  exportDownload: (organizationId: string, exportId: string): string =>
    `/organizations/${org(organizationId)}/compliance/exports/${seg(exportId)}/download`,
} as const;

/**
 * ARCH-21 — the tenant developer platform.
 *
 * Organization-scoped, like COMPLIANCE_ENDPOINTS: these manage one tenant's
 * own API keys and are read by that tenant's owner or admin.
 */
export const DEVELOPER_ENDPOINTS = {
  overview: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/developer`,
  tiers: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/developer/tiers`,
  keys: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/developer/keys`,
  keyTier: (organizationId: string, keyId: string): string =>
    `/organizations/${org(organizationId)}/developer/keys/${seg(keyId)}/tier`,
  keyMetrics: (organizationId: string, keyId: string): string =>
    `/organizations/${org(organizationId)}/developer/keys/${seg(keyId)}/metrics`,
  explorer: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/developer/explorer`,
} as const;

/**
 * ARCH-22 — enterprise BYOK and per-tenant model routing.
 *
 * Organization-scoped like COMPLIANCE_ENDPOINTS and DEVELOPER_ENDPOINTS.
 * `credential` takes a provider in the path rather than a credential id: the
 * console works in terms of "my OpenAI key", and there is at most one active
 * credential per provider per tenant, so an id would be an indirection with
 * no extra expressive power.
 */
export const BYOK_ENDPOINTS = {
  overview: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/byok`,
  providers: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/byok/providers`,
  credentials: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/byok/credentials`,
  credential: (organizationId: string, provider: string): string =>
    `/organizations/${org(organizationId)}/byok/credentials/${seg(provider)}`,
  validate: (organizationId: string, provider: string): string =>
    `/organizations/${org(organizationId)}/byok/credentials/${seg(provider)}/validate`,
  fallback: (organizationId: string, provider: string): string =>
    `/organizations/${org(organizationId)}/byok/credentials/${seg(provider)}/fallback`,
  routes: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/byok/routes`,
  route: (organizationId: string, taskType: string): string =>
    `/organizations/${org(organizationId)}/byok/routes/${seg(taskType)}`,
  savings: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/byok/savings`,
} as const;

/**
 * ARCH-21 — the public gateway.
 *
 * Listed for documentation and for the API explorer's copy targets. The
 * frontend never calls these: they authenticate with an API key, not the
 * session cookie apiClient carries, and sending a session to them would fail
 * with 401 by design. Nothing in src/ imports these into a request.
 */
export const PUBLIC_API_ENDPOINTS = {
  version: "/public",
  documents: "/public/documents",
  document: (workItemId: string): string => `/public/documents/${seg(workItemId)}`,
  query: "/public/query",
  workflows: "/public/workflows",
  triggerWorkflow: (ruleId: string): string =>
    `/public/workflows/${seg(ruleId)}/trigger`,
} as const;

/**
 * ARCH-18 — platform COGS and unit economics.
 *
 * Note the absence of an organization id anywhere in these paths. These are
 * cross-tenant reads gated by `require_superadmin`; a path that scoped to one
 * organization would belong under ORGANIZATION_ENDPOINTS behind an org role.
 */
export const COGS_ENDPOINTS = {
  marginSummary: (): string => `/admin/cogs/margins/summary`,
  tenantEconomics: (): string => `/admin/cogs/margins/tenants`,
  providerCosts: (): string => `/admin/cogs/margins/providers`,
  rateCard: (): string => `/admin/cogs/rate-card`,
  supplierInvoices: (): string => `/admin/cogs/supplier-invoices`,
  reconcileInvoice: (supplierInvoiceId: string): string =>
    `/admin/cogs/supplier-invoices/${seg(supplierInvoiceId)}/reconcile`,
  invoiceReconciliations: (supplierInvoiceId: string): string =>
    `/admin/cogs/supplier-invoices/${seg(supplierInvoiceId)}/reconciliations`,
  acceptVariance: (reconciliationId: string): string =>
    `/admin/cogs/reconciliations/${seg(reconciliationId)}/accept`,
} as const;
