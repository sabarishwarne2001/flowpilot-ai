import {
  createContext,
  useContext,
  type ReactNode,
} from "react";

import { useQuery } from "@tanstack/react-query";

import {
  getWorkspace,
  getPublicWorkspace,
} from "@/services/api/workspace";

import { useAuthStore } from "@/store/useAuthStore";

import type { Workspace } from "@/types/workspace";

interface WorkspaceContextValue {
  workspace: Workspace | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => Promise<unknown>;
}

const WorkspaceContext =
  createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({
  children,
}: {
  children: ReactNode;
}) {
  const token = useAuthStore((state) => state.token);

  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["workspace"],
    queryFn: async () => {
      return token
        ? await getWorkspace()
        : await getPublicWorkspace();
    },
  });

  return (
    <WorkspaceContext.Provider
      value={{
        workspace: data ?? null,
        isLoading,
        error: error as Error | null,
        refetch,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);

  if (!context) {
    throw new Error(
      "useWorkspace must be used within WorkspaceProvider"
    );
  }

  return context;
}
