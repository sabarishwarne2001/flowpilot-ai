import { useEffect } from "react";

import { useWorkspace } from "@/context/WorkspaceContext";
import { hexToHsl } from "@/utils/color";

export function useWorkspaceTheme() {
  const { workspace } = useWorkspace();

  useEffect(() => {
    const root = document.documentElement;
    const THEME_CACHE_KEY = "workspace-theme";

    if (!workspace) {
      return;
    }

    const primary = hexToHsl(workspace.primary_color);
    const secondary = hexToHsl(workspace.secondary_color);
    localStorage.setItem(
      THEME_CACHE_KEY,
      JSON.stringify({
        primary: workspace.primary_color,
        secondary: workspace.secondary_color,
      })
    );

    /* Workspace Colors */

    root.style.setProperty(
      "--workspace-primary",
      workspace.primary_color
    );

    root.style.setProperty(
      "--workspace-secondary",
      workspace.secondary_color
    );

    /* Tailwind Theme */

    root.style.setProperty("--primary", primary);
    root.style.setProperty("--ring", primary);
    root.style.setProperty("--secondary", secondary);

    return () => {
      root.style.removeProperty("--workspace-primary");
      root.style.removeProperty("--workspace-secondary");

      root.style.removeProperty("--primary");
      root.style.removeProperty("--secondary");
      root.style.removeProperty("--ring");
    };
  }, [workspace]);
}
