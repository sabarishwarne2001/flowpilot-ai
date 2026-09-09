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

/*
 * The ownershipApi namespace wrapper was removed here, with its default export.
 *
 * It was an object literal re-exporting the named functions above, and it had
 * exactly two references in the whole repository: its own declaration and its
 * own `export default`. Every consumer imports the standalone functions
 * directly, which is why it could sit here for months looking like API
 * surface while being reachable from nothing.
 *
 * Safe to delete precisely because of that count -- anything importing it
 * would have appeared in the same grep that found it dead.
 */
