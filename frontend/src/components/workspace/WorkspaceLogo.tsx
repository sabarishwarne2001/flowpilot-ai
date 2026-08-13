import { useAuthenticatedImage } from "@/hooks/useAuthenticatedImage";

interface WorkspaceLogoProps {
  workspace: {
    id: string;
    workspace_name: string;
    company_logo_url?: string | null;
  };
}

export function WorkspaceLogo({ workspace }: WorkspaceLogoProps) {
  const src = useAuthenticatedImage(workspace.company_logo_url ?? null);

  if (!src) {
    return (
      <div className="flex h-8 w-8 items-center justify-center rounded bg-primary/10 text-xs font-bold text-primary">
        {workspace.workspace_name.slice(0, 2).toUpperCase()}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={`${workspace.workspace_name} logo`}
      className="h-8 w-8 rounded object-cover"
    />
  );
}
