/**
 * Webhook endpoint and delivery management (ARCH-08 / ARCH-09).
 *
 * createEndpoint and rotateSecret return the signing secret. It is returned
 * once per issuance and no endpoint re-reveals it — do not cache it, store it,
 * or log it. Same discipline as API key tokens.
 */

import apiClient from "@/services/api/client";
import { WEBHOOK_ENDPOINTS } from "@/services/api/endpoints";

import type {
  WebhookAttempt,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEndpointCreated,
  WebhookEndpointCreateRequest,
  WebhookEndpointUpdateRequest,
  WebhookRotateSecretResult,
} from "@/types/webhook";

export const listWebhookEndpoints = async (
  organizationId: string,
): Promise<WebhookEndpoint[]> => {
  const response = await apiClient.get<WebhookEndpoint[]>(
    WEBHOOK_ENDPOINTS.endpoints(organizationId),
  );
  return response.data;
};

/** Creates an endpoint. The returned secret is shown once and never again. */
export const createWebhookEndpoint = async (
  organizationId: string,
  data: WebhookEndpointCreateRequest,
): Promise<WebhookEndpointCreated> => {
  const response = await apiClient.post<WebhookEndpointCreated>(
    WEBHOOK_ENDPOINTS.endpoints(organizationId),
    data,
  );
  return response.data;
};

export const updateWebhookEndpoint = async (
  organizationId: string,
  endpointId: string,
  data: WebhookEndpointUpdateRequest,
): Promise<WebhookEndpoint> => {
  const response = await apiClient.patch<WebhookEndpoint>(
    WEBHOOK_ENDPOINTS.endpoint(organizationId, endpointId),
    data,
  );
  return response.data;
};

export const deleteWebhookEndpoint = async (
  organizationId: string,
  endpointId: string,
): Promise<void> => {
  await apiClient.delete(WEBHOOK_ENDPOINTS.endpoint(organizationId, endpointId));
};

/**
 * Rotates the signing secret.
 *
 * Both old and new secrets sign deliveries until previous_secret_valid_until,
 * so a receiver can be migrated without dropping events mid-flight.
 */
export const rotateWebhookSecret = async (
  organizationId: string,
  endpointId: string,
): Promise<WebhookRotateSecretResult> => {
  const response = await apiClient.post<WebhookRotateSecretResult>(
    WEBHOOK_ENDPOINTS.rotateSecret(organizationId, endpointId),
    {},
  );
  return response.data;
};

export const listWebhookDeliveries = async (
  organizationId: string,
  endpointId: string,
  params: { status?: string; limit?: number } = {},
): Promise<WebhookDelivery[]> => {
  const response = await apiClient.get<WebhookDelivery[]>(
    WEBHOOK_ENDPOINTS.deliveries(organizationId, endpointId),
    { params: { status: params.status, limit: params.limit ?? 50 } },
  );
  return response.data;
};

export const listWebhookAttempts = async (
  organizationId: string,
  deliveryId: string,
): Promise<WebhookAttempt[]> => {
  const response = await apiClient.get<WebhookAttempt[]>(
    WEBHOOK_ENDPOINTS.attempts(organizationId, deliveryId),
  );
  return response.data;
};

/**
 * Requeues a delivery.
 *
 * Returns 409 in three distinct situations, each needing different guidance:
 * the delivery is CLAIMED by a worker, it already DELIVERED (redelivery would
 * duplicate), or the endpoint is disabled. The caller should read the detail.
 */
export const redeliverWebhookDelivery = async (
  organizationId: string,
  deliveryId: string,
): Promise<WebhookDelivery> => {
  const response = await apiClient.post<WebhookDelivery>(
    WEBHOOK_ENDPOINTS.redeliver(organizationId, deliveryId),
    {},
  );
  return response.data;
};

export const webhooksApi = {
  listWebhookEndpoints,
  createWebhookEndpoint,
  updateWebhookEndpoint,
  deleteWebhookEndpoint,
  rotateWebhookSecret,
  listWebhookDeliveries,
  listWebhookAttempts,
  redeliverWebhookDelivery,
} as const;

export default webhooksApi;
