/** Phase 2 — TruthMesh. Every route is gated on capability.truthmesh. */
import { apiClient } from "@/services/api/client";
import { downloadBlob } from "@/services/api/audit";
import type {
  ConflictStatus,
  MeshConflict,
  MeshDocument,
  MeshGraph,
  MeshLink,
  MeshMatrix,
  MeshOverview,
  Simulation,
  SimulationRequest,
  SimulationSummary,
} from "@/types/truthmesh";

const base = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}/truthmesh`;

export const meshKeys = {
  all: (workspaceId: string) => ["truthmesh", workspaceId] as const,
  overview: (workspaceId: string) => ["truthmesh", workspaceId, "overview"] as const,
  graph: (workspaceId: string, focus: string | null, minStrength: number) =>
    ["truthmesh", workspaceId, "graph", focus ?? "all", minStrength] as const,
  document: (workspaceId: string, workItemId: string) => ["truthmesh", workspaceId, "document", workItemId] as const,
  conflicts: (workspaceId: string, filters: Record<string, string | undefined>) =>
    ["truthmesh", workspaceId, "conflicts", filters] as const,
  matrix: (workspaceId: string) => ["truthmesh", workspaceId, "matrix"] as const,
  simulations: (workspaceId: string) => ["truthmesh", workspaceId, "simulations"] as const,
  simulation: (workspaceId: string, id: string) => ["truthmesh", workspaceId, "simulation", id] as const,
};

export const getOverview = async (workspaceId: string): Promise<MeshOverview> =>
  (await apiClient.get<MeshOverview>(`${base(workspaceId)}/overview`)).data;

export const getGraph = async (
  workspaceId: string,
  params: { focus?: string | null; minStrength?: number; depth?: number } = {},
): Promise<MeshGraph> =>
  (
    await apiClient.get<MeshGraph>(`${base(workspaceId)}/graph`, {
      params: {
        ...(params.focus ? { focus: params.focus, depth: params.depth ?? 2 } : {}),
        ...(params.minStrength !== undefined ? { min_strength: params.minStrength } : {}),
      },
    })
  ).data;

export const getMeshDocument = async (workspaceId: string, workItemId: string): Promise<MeshDocument> =>
  (await apiClient.get<MeshDocument>(`${base(workspaceId)}/documents/${encodeURIComponent(workItemId)}`)).data;

export const listMeshConflicts = async (
  workspaceId: string,
  filters: {
    status?: string | undefined;
    severity?: string | undefined;
    kind?: string | undefined;
    workItemId?: string | undefined;
    limit?: number | undefined;
  },
): Promise<{ items: MeshConflict[]; total: number }> =>
  (
    await apiClient.get<{ items: MeshConflict[]; total: number }>(`${base(workspaceId)}/conflicts`, {
      params: {
        status: filters.status ?? "ACTIVE",
        ...(filters.severity ? { severity: filters.severity } : {}),
        ...(filters.kind ? { kind: filters.kind } : {}),
        ...(filters.workItemId ? { work_item_id: filters.workItemId } : {}),
        limit: filters.limit ?? 100,
      },
    })
  ).data;

export const decideConflict = async (
  workspaceId: string,
  conflictId: string,
  body: { status: ConflictStatus; note?: string },
): Promise<MeshConflict> =>
  (await apiClient.patch<MeshConflict>(`${base(workspaceId)}/conflicts/${encodeURIComponent(conflictId)}`, body)).data;

export const decideLink = async (
  workspaceId: string,
  linkId: string,
  decision: "CONFIRMED" | "REJECTED" | "AUTO",
): Promise<MeshLink> =>
  (await apiClient.post<MeshLink>(`${base(workspaceId)}/links/${encodeURIComponent(linkId)}/decision`, { decision }))
    .data;

export const getMatrix = async (workspaceId: string): Promise<MeshMatrix> =>
  (await apiClient.get<MeshMatrix>(`${base(workspaceId)}/matrix`)).data;

export const rebuildMesh = async (workspaceId: string): Promise<{ job_id: string; status: string }> =>
  (await apiClient.post<{ job_id: string; status: string }>(`${base(workspaceId)}/rebuild`)).data;

export const runSimulation = async (workspaceId: string, body: SimulationRequest): Promise<Simulation> =>
  (await apiClient.post<Simulation>(`${base(workspaceId)}/simulations`, body)).data;

export const listSimulations = async (workspaceId: string): Promise<SimulationSummary[]> =>
  (await apiClient.get<SimulationSummary[]>(`${base(workspaceId)}/simulations`)).data;

export const getSimulation = async (workspaceId: string, id: string): Promise<Simulation> =>
  (await apiClient.get<Simulation>(`${base(workspaceId)}/simulations/${encodeURIComponent(id)}`)).data;

export const exportConflictsCsv = async (workspaceId: string): Promise<void> => {
  const response = await apiClient.get<Blob>(`${base(workspaceId)}/conflicts/export.csv`, { responseType: "blob" });
  downloadBlob(response.data, "truthmesh-conflicts.csv");
};
