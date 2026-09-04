/**
 * ARCH-27 — API client for the partner portal and the tenant marketplace.
 *
 * TWO PRINCIPALS, TWO PATH SHAPES
 * ===============================
 *
 * `/partners/{id}/...` authenticates a PARTNER principal — a tier above
 * organization, gated by partner_members. `/organizations/{id}/marketplace/...`
 * authenticates an ORGANIZATION principal through the ordinary org-role
 * dependencies.
 *
 * They are separate exports rather than one flat object because a caller
 * should not be able to reach for a partner-scoped path while holding an
 * organization id, or the reverse. The types make that a compile error.
 *
 * NOTHING HERE RECOMPUTES A DIGEST
 * ================================
 *
 * `PayoutStatement.digest_matches` arrives from the server and is passed
 * through untouched. A verification the browser performs is a verification an
 * attacker's browser can skip.
 */

import { apiClient } from "@/services/api/client";
import {
  MARKETPLACE_ENDPOINTS,
  PARTNER_ENDPOINTS,
} from "@/services/api/endpoints";
import type {
  AssignOrganizationRequest,
  BookOfBusinessEntry,
  ComputePayoutRequest,
  Installation,
  InstallManifestRequest,
  ManifestDetail,
  MarketplaceItem,
  Partner,
  PartnerEconomics,
  PartnerMember,
  PayoutPeriod,
  PayoutStatement,
  RegisterSigningKeyRequest,
  RevShareAgreement,
  SigningKey,
} from "@/types/partner";

// ---------------------------------------------------------------------------
// Partner portal
// ---------------------------------------------------------------------------

export const partnerApi = {
  listMine: async (): Promise<Partner[]> => {
    const { data } = await apiClient.get<Partner[]>(PARTNER_ENDPOINTS.list);
    return data;
  },

  get: async (partnerId: string): Promise<Partner> => {
    const { data } = await apiClient.get<Partner>(
      PARTNER_ENDPOINTS.detail(partnerId),
    );
    return data;
  },

  listMembers: async (partnerId: string): Promise<PartnerMember[]> => {
    const { data } = await apiClient.get<PartnerMember[]>(
      PARTNER_ENDPOINTS.members(partnerId),
    );
    return data;
  },

  getBook: async (
    partnerId: string,
    includeEnded = false,
  ): Promise<BookOfBusinessEntry[]> => {
    const { data } = await apiClient.get<BookOfBusinessEntry[]>(
      PARTNER_ENDPOINTS.book(partnerId),
      { params: { include_ended: includeEnded } },
    );
    return data;
  },

  assignOrganization: async (
    partnerId: string,
    payload: AssignOrganizationRequest,
  ): Promise<BookOfBusinessEntry> => {
    const { data } = await apiClient.post<BookOfBusinessEntry>(
      PARTNER_ENDPOINTS.book(partnerId),
      payload,
    );
    return data;
  },

  releaseOrganization: async (
    partnerId: string,
    organizationId: string,
  ): Promise<void> => {
    await apiClient.delete(
      PARTNER_ENDPOINTS.bookEntry(partnerId, organizationId),
    );
  },

  listSigningKeys: async (partnerId: string): Promise<SigningKey[]> => {
    const { data } = await apiClient.get<SigningKey[]>(
      PARTNER_ENDPOINTS.signingKeys(partnerId),
    );
    return data;
  },

  registerSigningKey: async (
    partnerId: string,
    payload: RegisterSigningKeyRequest,
  ): Promise<SigningKey> => {
    const { data } = await apiClient.post<SigningKey>(
      PARTNER_ENDPOINTS.signingKeys(partnerId),
      payload,
    );
    return data;
  },

  revokeSigningKey: async (
    partnerId: string,
    keyId: string,
    reason: string,
  ): Promise<SigningKey> => {
    const { data } = await apiClient.post<SigningKey>(
      PARTNER_ENDPOINTS.revokeSigningKey(partnerId, keyId),
      { reason },
    );
    return data;
  },

  listAgreements: async (partnerId: string): Promise<RevShareAgreement[]> => {
    const { data } = await apiClient.get<RevShareAgreement[]>(
      PARTNER_ENDPOINTS.agreements(partnerId),
    );
    return data;
  },

  listPayouts: async (partnerId: string): Promise<PayoutPeriod[]> => {
    const { data } = await apiClient.get<PayoutPeriod[]>(
      PARTNER_ENDPOINTS.payouts(partnerId),
    );
    return data;
  },

  computePayout: async (
    partnerId: string,
    payload: ComputePayoutRequest,
  ): Promise<PayoutPeriod> => {
    const { data } = await apiClient.post<PayoutPeriod>(
      PARTNER_ENDPOINTS.payouts(partnerId),
      payload,
    );
    return data;
  },

  getStatement: async (
    partnerId: string,
    periodId: string,
  ): Promise<PayoutStatement> => {
    const { data } = await apiClient.get<PayoutStatement>(
      PARTNER_ENDPOINTS.payout(partnerId, periodId),
    );
    return data;
  },

  getEconomics: async (partnerId: string): Promise<PartnerEconomics> => {
    const { data } = await apiClient.get<PartnerEconomics>(
      PARTNER_ENDPOINTS.economics(partnerId),
    );
    return data;
  },

  listCatalog: async (partnerId: string): Promise<MarketplaceItem[]> => {
    const { data } = await apiClient.get<MarketplaceItem[]>(
      PARTNER_ENDPOINTS.catalog(partnerId),
    );
    return data;
  },
};

// ---------------------------------------------------------------------------
// Tenant marketplace
// ---------------------------------------------------------------------------

export const marketplaceApi = {
  browse: async (
    organizationId: string,
    category?: string,
  ): Promise<MarketplaceItem[]> => {
    const { data } = await apiClient.get<MarketplaceItem[]>(
      MARKETPLACE_ENDPOINTS.catalog(organizationId),
      { params: category ? { category } : undefined },
    );
    return data;
  },

  inspect: async (
    organizationId: string,
    manifestId: string,
  ): Promise<ManifestDetail> => {
    const { data } = await apiClient.get<ManifestDetail>(
      MARKETPLACE_ENDPOINTS.manifest(organizationId, manifestId),
    );
    return data;
  },

  listInstallations: async (
    organizationId: string,
  ): Promise<Installation[]> => {
    const { data } = await apiClient.get<Installation[]>(
      MARKETPLACE_ENDPOINTS.installations(organizationId),
    );
    return data;
  },

  install: async (
    organizationId: string,
    payload: InstallManifestRequest,
  ): Promise<Installation> => {
    const { data } = await apiClient.post<Installation>(
      MARKETPLACE_ENDPOINTS.installations(organizationId),
      payload,
    );
    return data;
  },

  uninstall: async (
    organizationId: string,
    installationId: string,
  ): Promise<void> => {
    await apiClient.delete(
      MARKETPLACE_ENDPOINTS.installation(organizationId, installationId),
    );
  },
};
