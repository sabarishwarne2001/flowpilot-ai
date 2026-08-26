import { useState } from "react";
import { useAuthenticatedImage } from "@/hooks/useAuthenticatedImage";

interface WorkspaceLogoProps {
  workspace: {
    id: string;
    workspace_name: string;
    company_logo_url?: string | null;
  };
  className?: string;
  fallbackClassName?: string;
}

export function WorkspaceLogo({
  workspace,
  className = "h-8 w-8 rounded object-cover",
  fallbackClassName = "flex h-8 w-8 items-center justify-center rounded bg-primary/10 text-xs font-bold text-primary",
}: WorkspaceLogoProps) {
  const src = useAuthenticatedImage(workspace.company_logo_url ?? null);
  const [loadError, setLoadError] = useState(false);

  const initials = (workspace.workspace_name || "WS")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join("");

  if (!src || loadError) {
    return (
      <div className={fallbackClassName}>
        {initials}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={`${workspace.workspace_name} logo`}
      className={className}
      onError={() => setLoadError(true)}
    />
  );
}

export default WorkspaceLogo;
