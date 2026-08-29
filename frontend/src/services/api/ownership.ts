import apiClient from "@/services/api/client";
import { OWNERSHIP_ENDPOINTS } from "@/services/api/endpoints";
import type {
  OwnershipTransfer,
  OwnershipTransferInitiateRequest,
  PendingOwnershipTransfers,
} from "@/types/ownership";

export const initiateOwnershipTransfer = async (
  organizationId: string,
  data: OwnershipTransferInitiateRequest,
): Promise<OwnershipTransfer> => {
  const response = await apiClient.post<OwnershipTransfer>(
    OWNERSHIP_ENDPOINTS.transfers(organizationId),
    data,
  );
  return response.data;
};

export const acceptOwnershipTransfer = async (
  organizationId: string,
  transferId: string,
): Promise<OwnershipTransfer> => {
  const response = await apiClient.post<OwnershipTransfer>(
    OWNERSHIP_ENDPOINTS.accept(organizationId, transferId),
    {},
  );
  return response.data;
};

export const declineOwnershipTransfer = async (
  organizationId: string,
  transferId: string,
): Promise<OwnershipTransfer> => {
  const response = await apiClient.post<OwnershipTransfer>(
    OWNERSHIP_ENDPOINTS.decline(organizationId, transferId),
    {},
  );
  return response.data;
};

export const cancelOwnershipTransfer = async (
  organizationId: string,
  transferId: string,
): Promise<OwnershipTransfer> => {
  const response = await apiClient.post<OwnershipTransfer>(
    OWNERSHIP_ENDPOINTS.cancel(organizationId, transferId),
    {},
  );
  return response.data;
};

export const listMyOwnershipTransfers =
  async (): Promise<PendingOwnershipTransfers> => {
    const response = await apiClient.get<PendingOwnershipTransfers>(
      OWNERSHIP_ENDPOINTS.mine,
    );
    return response.data;
  };

export const ownershipApi = {
  initiateOwnershipTransfer,
  acceptOwnershipTransfer,
  declineOwnershipTransfer,
  cancelOwnershipTransfer,
  listMyOwnershipTransfers,
} as const;

export default ownershipApi;
