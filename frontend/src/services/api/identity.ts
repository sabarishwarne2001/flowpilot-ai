import apiClient from "@/services/api/client";
import type {
  CertificateCreate,
  CertificateRead,
  DirectoryIdentityRead,
  DomainClaimRequest,
  DomainClaimResponse,
  DomainRead,
  DryRunRequest,
  DryRunResult,
  IdpConfigCreate,
  IdpConfigRead,
  RoleMappingCreate,
  ScimKeyCreate,
  ScimKeyIssued,
  ScimKeyRead,
  SecurityPolicyRead,
  SecurityPolicyUpdate,
} from "@/types/identity";

const seg = (value: string): string => encodeURIComponent(value);

const base = (organizationId: string): string => {
  if (!organizationId) {
    throw new Error(
      "An organizationId is required to build this URL. Gate the query with `enabled: Boolean(organizationId)`.",
    );
  }
  return `/organizations/${seg(organizationId)}/identity`;
};

export const IDENTITY_ENDPOINTS = {
  domains: (o: string) => `${base(o)}/domains`,
  domainVerify: (o: string, d: string) => `${base(o)}/domains/${seg(d)}/verify`,
  domainBindSso: (o: string, d: string) =>
    `${base(o)}/domains/${seg(d)}/bind-sso`,

  idpConfigs: (o: string) => `${base(o)}/idp-configs`,
  certificates: (o: string, c: string) =>
    `${base(o)}/idp-configs/${seg(c)}/certificates`,
  roleMappings: (o: string, c: string) =>
    `${base(o)}/idp-configs/${seg(c)}/role-mappings`,
  dryRun: (o: string, c: string) => `${base(o)}/idp-configs/${seg(c)}/dry-run`,
  activate: (o: string, c: string) =>
    `${base(o)}/idp-configs/${seg(c)}/activate`,

  scimKeys: (o: string) => `${base(o)}/scim-keys`,
  scimKeyRotate: (o: string, k: string) =>
    `${base(o)}/scim-keys/${seg(k)}/rotate`,
  scimKey: (o: string, k: string) => `${base(o)}/scim-keys/${seg(k)}`,

  securityPolicy: (o: string) => `${base(o)}/security-policy`,
  directory: (o: string) => `${base(o)}/directory`,
} as const;

export const listDomains = async (
  organizationId: string,
): Promise<DomainRead[]> => {
  const response = await apiClient.get<DomainRead[]>(
    IDENTITY_ENDPOINTS.domains(organizationId),
  );
  return response.data;
};

export const claimDomain = async (
  organizationId: string,
  payload: DomainClaimRequest,
): Promise<DomainClaimResponse> => {
  const response = await apiClient.post<DomainClaimResponse>(
    IDENTITY_ENDPOINTS.domains(organizationId),
    payload,
  );
  return response.data;
};

export const verifyDomain = async (
  organizationId: string,
  domainId: string,
): Promise<DomainRead> => {
  const response = await apiClient.post<DomainRead>(
    IDENTITY_ENDPOINTS.domainVerify(organizationId, domainId),
    {},
  );
  return response.data;
};

export const bindDomainSso = async (
  organizationId: string,
  domainId: string,
): Promise<DomainRead> => {
  const response = await apiClient.post<DomainRead>(
    IDENTITY_ENDPOINTS.domainBindSso(organizationId, domainId),
    {},
  );
  return response.data;
};

export const listIdpConfigs = async (
  organizationId: string,
): Promise<IdpConfigRead[]> => {
  const response = await apiClient.get<IdpConfigRead[]>(
    IDENTITY_ENDPOINTS.idpConfigs(organizationId),
  );
  return response.data;
};

export const createIdpConfig = async (
  organizationId: string,
  payload: IdpConfigCreate,
): Promise<IdpConfigRead> => {
  const response = await apiClient.post<IdpConfigRead>(
    IDENTITY_ENDPOINTS.idpConfigs(organizationId),
    payload,
  );
  return response.data;
};

export const addCertificate = async (
  organizationId: string,
  configId: string,
  payload: CertificateCreate,
): Promise<CertificateRead> => {
  const response = await apiClient.post<CertificateRead>(
    IDENTITY_ENDPOINTS.certificates(organizationId, configId),
    payload,
  );
  return response.data;
};

export const addRoleMapping = async (
  organizationId: string,
  configId: string,
  payload: RoleMappingCreate,
): Promise<unknown> => {
  const response = await apiClient.post(
    IDENTITY_ENDPOINTS.roleMappings(organizationId, configId),
    payload,
  );
  return response.data;
};

export const dryRunRoleMapping = async (
  organizationId: string,
  configId: string,
  payload: DryRunRequest,
): Promise<DryRunResult> => {
  const response = await apiClient.post<DryRunResult>(
    IDENTITY_ENDPOINTS.dryRun(organizationId, configId),
    payload,
  );
  return response.data;
};

export const activateIdpConfig = async (
  organizationId: string,
  configId: string,
): Promise<IdpConfigRead> => {
  const response = await apiClient.post<IdpConfigRead>(
    IDENTITY_ENDPOINTS.activate(organizationId, configId),
    {},
  );
  return response.data;
};

export const listScimKeys = async (
  organizationId: string,
): Promise<ScimKeyRead[]> => {
  const response = await apiClient.get<ScimKeyRead[]>(
    IDENTITY_ENDPOINTS.scimKeys(organizationId),
  );
  return response.data;
};

export const createScimKey = async (
  organizationId: string,
  payload: ScimKeyCreate,
): Promise<ScimKeyIssued> => {
  const response = await apiClient.post<ScimKeyIssued>(
    IDENTITY_ENDPOINTS.scimKeys(organizationId),
    payload,
  );
  return response.data;
};

export const rotateScimKey = async (
  organizationId: string,
  keyId: string,
): Promise<ScimKeyIssued> => {
  const response = await apiClient.post<ScimKeyIssued>(
    IDENTITY_ENDPOINTS.scimKeyRotate(organizationId, keyId),
    {},
  );
  return response.data;
};

export const revokeScimKey = async (
  organizationId: string,
  keyId: string,
): Promise<void> => {
  await apiClient.delete(IDENTITY_ENDPOINTS.scimKey(organizationId, keyId));
};

export const getSecurityPolicy = async (
  organizationId: string,
): Promise<SecurityPolicyRead> => {
  const response = await apiClient.get<SecurityPolicyRead>(
    IDENTITY_ENDPOINTS.securityPolicy(organizationId),
  );
  return response.data;
};

export const updateSecurityPolicy = async (
  organizationId: string,
  payload: SecurityPolicyUpdate,
): Promise<SecurityPolicyRead> => {
  const response = await apiClient.put<SecurityPolicyRead>(
    IDENTITY_ENDPOINTS.securityPolicy(organizationId),
    payload,
  );
  return response.data;
};

export const listDirectory = async (
  organizationId: string,
): Promise<DirectoryIdentityRead[]> => {
  const response = await apiClient.get<DirectoryIdentityRead[]>(
    IDENTITY_ENDPOINTS.directory(organizationId),
  );
  return response.data;
};

export const identityApi = {
  listDomains,
  claimDomain,
  verifyDomain,
  bindDomainSso,
  listIdpConfigs,
  createIdpConfig,
  addCertificate,
  addRoleMapping,
  dryRunRoleMapping,
  activateIdpConfig,
  listScimKeys,
  createScimKey,
  rotateScimKey,
  revokeScimKey,
  getSecurityPolicy,
  updateSecurityPolicy,
  listDirectory,
} as const;

export default identityApi;
