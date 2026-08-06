import { useEffect } from "react";

import { useOptionalTenant } from "@/routes/TenantContext";

const DEFAULT_TITLE = "FlowPilot AI";

/**
 * Sets the document title from the active tenant.
 *
 * ARCH-01 removed workspace.company_name, which this hook read first. That
 * column was the tenant's identity rather than the workspace's, and it moved to
 * Organization.name — so the organization name is its direct successor and the
 * displayed title is unchanged for a single-workspace tenant.
 *
 * The workspace name is appended because a tenant may now hold several
 * workspaces, and "Acme Inc." on every tab is less useful than "Engineering ·
 * Acme Inc." once that is true.
 *
 * useOptionalTenant rather than useResolvedTenant: this may run on
 * unauthenticated screens, where there is no tenant and none should be
 * inferred. Outside tenant scope it falls back to the product name rather than
 * throwing.
 */
export function useDocumentBranding() {
  const tenant = useOptionalTenant();

  const organizationName = tenant?.organization.organization_name ?? null;
  const workspaceName = tenant?.workspace.workspace_name ?? null;

  useEffect(() => {
    if (organizationName && workspaceName) {
      document.title = `${workspaceName} · ${organizationName}`;
      return;
    }

    document.title = organizationName ?? workspaceName ?? DEFAULT_TITLE;
  }, [organizationName, workspaceName]);
}
