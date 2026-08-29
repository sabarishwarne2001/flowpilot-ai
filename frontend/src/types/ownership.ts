export type OwnershipTransferStatus =
  | "PENDING"
  | "ACCEPTED"
  | "DECLINED"
  | "CANCELLED"
  | "EXPIRED";

export interface OwnershipTransfer {
  readonly id: string;
  readonly organization_id: string;
  readonly initiated_by_id: string;
  readonly target_membership_id: string;
  readonly status: OwnershipTransferStatus;
  readonly expires_at: string;
  /** Set when the TARGET acted (accepted or declined). */
  readonly responded_at: string | null;
  /** Set when the INITIATOR withdrew. Never both. */
  readonly cancelled_at: string | null;
  readonly created_at: string;
}

export interface PendingOwnershipTransfers {
  readonly transfers: readonly OwnershipTransfer[];
}

export interface OwnershipTransferInitiateRequest {
  readonly target_membership_id: string;
  /** Re-authentication password. Never stored, echoed, or logged. */
  readonly current_password: string;
}
