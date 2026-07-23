import apiClient from "./client";

import type {
  Workspace,
  WorkspaceCreate,
} from "@/types/workspace";

export const getWorkspace = async (): Promise<Workspace> => {
  const response = await apiClient.get("/workspace");
  return response.data;
};

export const saveWorkspace = async (
  payload: WorkspaceCreate,
): Promise<Workspace> => {
  const response = await apiClient.put(
    "/workspace",
    payload,
  );

  return response.data;
};
