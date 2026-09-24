/** ARCH42-S2:api-client — Entity Resolution & Document Knowledge Graph. */
import { apiClient } from "@/services/api/client";
import type {
  CandidateRow,
  Entity360,
  EntityGraph,
  EntityKind,
  EntityList,
  EntityPotential,
  EntityRow,
  EntitySummary,
  ResolveResult,
  WorkItemEntities,
} from "@/types/entities";

const base = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}/entities`;
const one = (workspaceId: string, entityId: string): string => `${base(workspaceId)}/${encodeURIComponent(entityId)}`;
const item = (workspaceId: string, workItemId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/entities`;

export interface EntityListParams {
  readonly kind?: EntityKind | undefined;
  readonly q?: string | undefined;
  readonly page?: number;
  readonly page_size?: number;
}

export const getEntityPotential = async (workspaceId: string): Promise<EntityPotential> =>
  (await apiClient.get<EntityPotential>(`${base(workspaceId)}/potential`)).data;

export const getEntitySummary = async (workspaceId: string): Promise<EntitySummary> =>
  (await apiClient.get<EntitySummary>(`${base(workspaceId)}/summary`)).data;

export const listEntities = async (workspaceId: string, params: EntityListParams): Promise<EntityList> =>
  (await apiClient.get<EntityList>(base(workspaceId), { params })).data;

export const listMergeCandidates = async (workspaceId: string): Promise<CandidateRow[]> =>
  (await apiClient.get<CandidateRow[]>(`${base(workspaceId)}/merge-candidates`)).data;

export const getEntity = async (workspaceId: string, entityId: string): Promise<Entity360> =>
  (await apiClient.get<Entity360>(one(workspaceId, entityId))).data;

export const getEntityGraph = async (workspaceId: string, entityId: string, depth: number): Promise<EntityGraph> =>
  (await apiClient.get<EntityGraph>(`${one(workspaceId, entityId)}/graph`, { params: { depth } })).data;

export const renameEntity = async (workspaceId: string, entityId: string, displayName: string): Promise<EntityRow> =>
  (await apiClient.patch<EntityRow>(one(workspaceId, entityId), { display_name: displayName })).data;

export const mergeEntity = async (workspaceId: string, entityId: string, intoEntityId: string): Promise<EntityRow> =>
  (await apiClient.post<EntityRow>(`${one(workspaceId, entityId)}/merge`, { into_entity_id: intoEntityId })).data;

export const unmergeEntity = async (workspaceId: string, entityId: string): Promise<EntityRow> =>
  (await apiClient.post<EntityRow>(`${one(workspaceId, entityId)}/unmerge`)).data;

export const splitEntity = async (workspaceId: string, entityId: string, mentionIds: readonly string[]): Promise<EntityRow> =>
  (await apiClient.post<EntityRow>(`${one(workspaceId, entityId)}/split`, { mention_ids: mentionIds })).data;

export const eraseEntity = async (workspaceId: string, entityId: string): Promise<Record<string, number>> =>
  (await apiClient.delete<Record<string, number>>(one(workspaceId, entityId))).data;

export const getWorkItemEntities = async (workspaceId: string, workItemId: string): Promise<WorkItemEntities> =>
  (await apiClient.get<WorkItemEntities>(item(workspaceId, workItemId))).data;

export const resolveWorkItemEntities = async (workspaceId: string, workItemId: string): Promise<ResolveResult> =>
  (await apiClient.post<ResolveResult>(`${item(workspaceId, workItemId)}/resolve`)).data;

export const entityKeys = {
  all: (workspaceId: string) => ["entities", workspaceId] as const,
  potential: (workspaceId: string) => ["entities", workspaceId, "potential"] as const,
  summary: (workspaceId: string) => ["entities", workspaceId, "summary"] as const,
  list: (workspaceId: string, params: EntityListParams) => ["entities", workspaceId, "list", params] as const,
  detail: (workspaceId: string, entityId: string) => ["entities", workspaceId, "detail", entityId] as const,
  graph: (workspaceId: string, entityId: string, depth: number) => ["entities", workspaceId, "graph", entityId, depth] as const,
  workItem: (workspaceId: string, workItemId: string) => ["entities", workspaceId, "work-item", workItemId] as const,
};
