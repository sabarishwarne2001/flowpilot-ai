/**
 * Centralized API path construction for FlowPilot AI.
 */

const seg = (value: string): string => encodeURIComponent(value);

const ws = (workspaceId: string): string => {
  if (!workspaceId) {
    throw new Error(
      "A workspaceId is required to build this URL. The caller rendered " +
        "before the tenant context resolved — gate the query with " +
        "`enabled: Boolean(workspaceId)`.",
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
  knowledgeBase: (workspaceId: string): string =>
    `${scoped(workspaceId)}/work-items/knowledge-base`,
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
  health: (workspaceId: string): string => `${scoped(workspaceId)}/dashboard/health`,
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
