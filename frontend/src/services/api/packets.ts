/** ARCH43-S2:api-client — the Universal Packet Dicer. Every route is gated on capability.case_intelligence. */
import { apiClient } from "@/services/api/client";
import type { PacketSplitDetail, PacketSplitList, PacketSplitStatus, WorkItemLineage } from "@/types/packets";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}`;
const split = (workspaceId: string, splitId: string): string =>
  `${ws(workspaceId)}/packet-splits/${encodeURIComponent(splitId)}`;

export const listPacketSplits = async (workspaceId: string, status?: PacketSplitStatus): Promise<PacketSplitList> =>
  (await apiClient.get<PacketSplitList>(`${ws(workspaceId)}/packet-splits`, { params: status ? { status } : {} })).data;

export const getPacketSplit = async (workspaceId: string, splitId: string): Promise<PacketSplitDetail> =>
  (await apiClient.get<PacketSplitDetail>(split(workspaceId, splitId))).data;

export const detectPacketSplit = async (workspaceId: string, workItemId: string, force = false): Promise<PacketSplitDetail> =>
  (await apiClient.post<PacketSplitDetail>(
    `${ws(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/packet-split`, undefined, { params: { force } },
  )).data;

export const correctPacketSplit = async (
  workspaceId: string, splitId: string, boundaries: readonly number[],
): Promise<PacketSplitDetail> =>
  (await apiClient.put<PacketSplitDetail>(`${split(workspaceId, splitId)}/boundaries`, { boundaries })).data;

export const approvePacketSplit = async (
  workspaceId: string, splitId: string, boundaries?: readonly number[],
): Promise<PacketSplitDetail> =>
  (await apiClient.post<PacketSplitDetail>(`${split(workspaceId, splitId)}/approve`, boundaries ? { boundaries } : {})).data;

export const rejectPacketSplit = async (workspaceId: string, splitId: string): Promise<PacketSplitDetail> =>
  (await apiClient.post<PacketSplitDetail>(`${split(workspaceId, splitId)}/reject`)).data;

export const getWorkItemLineage = async (workspaceId: string, workItemId: string): Promise<WorkItemLineage> =>
  (await apiClient.get<WorkItemLineage>(`${ws(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/lineage`)).data;
