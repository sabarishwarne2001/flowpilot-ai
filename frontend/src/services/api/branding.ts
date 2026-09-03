import apiClient from "@/services/api/client";
import { BRANDING_ENDPOINTS } from "@/services/api/endpoints";
import type {
  BrandingManifest,
  CertificateStatusResponse,
  CustomDomainCreate,
  CustomDomainDetail,
  DomainVerificationResult,
  SenderDomainStatusResponse,
  SenderDomainUpdate,
  TenantBrandingResponse,
  TenantBrandingUpdate,
} from "@/types/branding";

const JSON_HEADERS = { Accept: "application/json" } as const;

// ---------------------------------------------------------------------------
// Custom domains — OWNER for every write, ADMIN for reads
// ---------------------------------------------------------------------------

export const listCustomDomains = async (
  organizationId: string,
): Promise<CustomDomainDetail[]> => {
  const response = await apiClient.get<CustomDomainDetail[]>(
    BRANDING_ENDPOINTS.domains(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const claimCustomDomain = async (
  organizationId: string,
  payload: CustomDomainCreate,
): Promise<CustomDomainDetail> => {
  const response = await apiClient.post<CustomDomainDetail>(
    BRANDING_ENDPOINTS.domains(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const verifyCustomDomain = async (
  organizationId: string,
  domainId: string,
): Promise<DomainVerificationResult> => {
  const response = await apiClient.post<DomainVerificationResult>(
    BRANDING_ENDPOINTS.verifyDomain(organizationId, domainId),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const reissueChallenge = async (
  organizationId: string,
  domainId: string,
): Promise<CustomDomainDetail> => {
  const response = await apiClient.post<CustomDomainDetail>(
    BRANDING_ENDPOINTS.reissueChallenge(organizationId, domainId),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const setPrimaryDomain = async (
  organizationId: string,
  domainId: string,
  isPrimary: boolean,
): Promise<CustomDomainDetail> => {
  const response = await apiClient.put<CustomDomainDetail>(
    BRANDING_ENDPOINTS.primaryDomain(organizationId, domainId),
    { is_primary: isPrimary },
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const requestCertificate = async (
  organizationId: string,
  domainId: string,
): Promise<CertificateStatusResponse> => {
  const response = await apiClient.post<CertificateStatusResponse>(
    BRANDING_ENDPOINTS.certificate(organizationId, domainId),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

/**
 * Stop serving the hostname, keeping the claim.
 *
 * DELETE on the certificate sub-resource, not on the domain. The row survives,
 * so no other tenant can take the name. `releaseCustomDomain` is the one that
 * frees it.
 */
export const revokeCustomDomain = async (
  organizationId: string,
  domainId: string,
): Promise<CustomDomainDetail> => {
  const response = await apiClient.delete<CustomDomainDetail>(
    BRANDING_ENDPOINTS.certificate(organizationId, domainId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

/** Delete the row, freeing the hostname for anyone to claim. */
export const releaseCustomDomain = async (
  organizationId: string,
  domainId: string,
): Promise<void> => {
  await apiClient.delete(BRANDING_ENDPOINTS.domain(organizationId, domainId), {
    headers: JSON_HEADERS,
  });
};

// ---------------------------------------------------------------------------
// Branding — ADMIN
// ---------------------------------------------------------------------------

export const getBranding = async (
  organizationId: string,
): Promise<TenantBrandingResponse> => {
  const response = await apiClient.get<TenantBrandingResponse>(
    BRANDING_ENDPOINTS.branding(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateBranding = async (
  organizationId: string,
  payload: TenantBrandingUpdate,
): Promise<TenantBrandingResponse> => {
  const response = await apiClient.put<TenantBrandingResponse>(
    BRANDING_ENDPOINTS.branding(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

const uploadAsset = async (
  url: string,
  file: File,
): Promise<TenantBrandingResponse> => {
  const form = new FormData();
  form.append("file", file);
  // No explicit Content-Type: the browser has to set the multipart boundary,
  // and overriding it here produces a request the server cannot parse.
  const response = await apiClient.post<TenantBrandingResponse>(url, form);
  return response.data;
};

export const uploadLogo = async (
  organizationId: string,
  file: File,
): Promise<TenantBrandingResponse> =>
  uploadAsset(BRANDING_ENDPOINTS.logo(organizationId), file);

export const uploadFavicon = async (
  organizationId: string,
  file: File,
): Promise<TenantBrandingResponse> =>
  uploadAsset(BRANDING_ENDPOINTS.favicon(organizationId), file);

export const clearLogo = async (
  organizationId: string,
): Promise<TenantBrandingResponse> => {
  const response = await apiClient.delete<TenantBrandingResponse>(
    BRANDING_ENDPOINTS.logo(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const clearFavicon = async (
  organizationId: string,
): Promise<TenantBrandingResponse> => {
  const response = await apiClient.delete<TenantBrandingResponse>(
    BRANDING_ENDPOINTS.favicon(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const setSenderDomain = async (
  organizationId: string,
  payload: SenderDomainUpdate,
): Promise<SenderDomainStatusResponse> => {
  const response = await apiClient.put<SenderDomainStatusResponse>(
    BRANDING_ENDPOINTS.senderDomain(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const verifySenderDomain = async (
  organizationId: string,
): Promise<SenderDomainStatusResponse> => {
  const response = await apiClient.post<SenderDomainStatusResponse>(
    BRANDING_ENDPOINTS.verifySenderDomain(organizationId),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

/**
 * The unauthenticated, host-resolved theme payload.
 *
 * Takes no organization id: the tenant is decided entirely by the Host the
 * browser sent. Adding a parameter here would be adding one to an endpoint
 * that must not have one.
 */
export const getBrandingManifest = async (): Promise<BrandingManifest> => {
  const response = await apiClient.get<BrandingManifest>(
    BRANDING_ENDPOINTS.manifest,
    { headers: JSON_HEADERS },
  );
  return response.data;
};
