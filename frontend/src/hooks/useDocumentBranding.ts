import { useEffect } from "react";

import { useWorkspace } from "@/context/WorkspaceContext";

const DEFAULT_TITLE = "FlowPilot AI";

export function useDocumentBranding() {
  const { workspace } = useWorkspace();

  useEffect(() => {
    document.title =
      workspace?.company_name ||
      workspace?.workspace_name ||
      DEFAULT_TITLE;
  }, [workspace]);
}
