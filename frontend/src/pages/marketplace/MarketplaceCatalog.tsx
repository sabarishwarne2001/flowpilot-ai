/**
 * ARCH-27 — the tenant marketplace catalog.
 *
 * WHY THE INSTALL BUTTON IS DISABLED WHEN THE SIGNATURE DOES NOT VERIFY
 * ====================================================================
 *
 * Not because that is what stops an unsigned install — the service re-verifies
 * before writing, and `marketplace_installations.verified_signature_id` is NOT
 * NULL, so an unverified install is unrepresentable at the schema level.
 *
 * The disabled button exists so an administrator is never asked to consent to
 * something the platform would refuse anyway. Being told "no" after clicking
 * is a worse experience than being told why before.
 *
 * WHY THE DAG IS SHOWN BEFORE INSTALLING
 * ======================================
 *
 * Installing admits third-party workflow code into this tenant's own
 * automation engine. The administrator approving that should be able to read
 * what it does — which nodes, which actions, which recipients — rather than
 * approving a name and a publisher.
 *
 * `signature_verified` is computed server-side at request time against the
 * live key status. A verdict cached at publish time cannot know the publisher
 * rotated keys this morning.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  Fingerprint,
  Loader2,
  Package,
  ShieldCheck,
  ShieldX,
  Trash2,
} from "lucide-react";

import { useTenant } from "@/hooks/useTenant";
import { marketplaceApi } from "@/services/api/partner";
import { marketplaceKeys } from "@/services/api/queryKeys";
import type { ManifestNode, MarketplaceItem } from "@/types/partner";

const NODE_TONE: Record<ManifestNode["node_type"], string> = {
  trigger: "bg-blue-50 text-blue-700 ring-blue-200",
  condition: "bg-violet-50 text-violet-700 ring-violet-200",
  action: "bg-amber-50 text-amber-800 ring-amber-200",
  branch: "bg-slate-100 text-slate-700 ring-slate-200",
  join: "bg-slate-100 text-slate-700 ring-slate-200",
};

export default function MarketplaceCatalog() {
  // `TenantState` is a discriminated union on `status`, so the ids have to be
  // narrowed rather than reached for. That is the point of the union: this
  // page needs BOTH an organization and a workspace — an organization to know
  // what may be browsed, and a workspace for the installed manifest to
  // materialise into — and `status: "no_workspace"` is a real state a session
  // can be in. Optional-chaining a non-existent property would have compiled
  // only if the union were loose enough to hide that case.
  const { state } = useTenant();
  const organizationId =
    state.status === "ready" ? state.organization.organization_id : "";
  const workspaceId = state.status === "ready" ? state.workspace.id : "";
  const queryClient = useQueryClient();
  const [inspecting, setInspecting] = useState<MarketplaceItem | null>(null);

  const catalogQuery = useQuery({
    queryKey: marketplaceKeys.catalog(organizationId),
    queryFn: () => marketplaceApi.browse(organizationId),
    enabled: Boolean(organizationId),
  });

  const installationsQuery = useQuery({
    queryKey: marketplaceKeys.installations(organizationId),
    queryFn: () => marketplaceApi.listInstallations(organizationId),
    enabled: Boolean(organizationId),
  });

  const manifestId = inspecting?.latest_manifest_id ?? null;

  const detailQuery = useQuery({
    queryKey: manifestId
      ? marketplaceKeys.manifest(organizationId, manifestId)
      : ["marketplace", "none"],
    queryFn: () =>
      marketplaceApi.inspect(organizationId, manifestId as string),
    enabled: Boolean(manifestId),
  });

  const installMutation = useMutation({
    mutationFn: () =>
      marketplaceApi.install(organizationId, {
        manifest_id: manifestId as string,
        workspace_id: workspaceId,
      }),
    onSuccess: async () => {
      toast.success(
        "Installed. The workflow is created but not enabled — review it in Automation before turning it on.",
      );
      setInspecting(null);
      await queryClient.invalidateQueries({
        queryKey: marketplaceKeys.all(organizationId),
      });
    },
    onError: (error: unknown) => {
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Installation failed.";
      toast.error(detail);
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: (installationId: string) =>
      marketplaceApi.uninstall(organizationId, installationId),
    onSuccess: async () => {
      toast.success("Removed. The automation rule was deactivated, not deleted.");
      await queryClient.invalidateQueries({
        queryKey: marketplaceKeys.all(organizationId),
      });
    },
  });

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">
          Partner marketplace
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Signed automation workflows published by FlowPilot partners. Every
          manifest is cryptographically verified before it can be installed.
        </p>
      </header>

      {installationsQuery.data && installationsQuery.data.length > 0 ? (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-slate-900">Installed</h2>
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2">Workflow</th>
                  <th className="px-4 py-2">Version</th>
                  <th className="px-4 py-2">Installed</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {installationsQuery.data.map((installation) => (
                  <tr key={installation.id}>
                    <td className="px-4 py-2 font-medium text-slate-900">
                      {installation.item_name}
                    </td>
                    <td className="px-4 py-2 text-slate-500">
                      {installation.manifest_version}
                    </td>
                    <td className="px-4 py-2 text-slate-500">
                      {new Date(
                        installation.installed_at,
                      ).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={() =>
                          uninstallMutation.mutate(installation.id)
                        }
                        className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-900">Available</h2>
        {catalogQuery.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading catalog…
          </div>
        ) : (catalogQuery.data?.length ?? 0) === 0 ? (
          <p className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
            No published workflows are available to your organization yet.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {catalogQuery.data?.map((item) => (
              <article
                key={item.id}
                className="flex flex-col rounded-lg border border-slate-200 bg-white p-4"
              >
                <div className="flex items-start gap-3">
                  <Package className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                  <div className="min-w-0">
                    <h3 className="truncate font-medium text-slate-900">
                      {item.name}
                    </h3>
                    <p className="text-xs text-slate-500">
                      {item.partner_name}
                      {item.latest_version ? ` · v${item.latest_version}` : ""}
                    </p>
                  </div>
                </div>
                <p className="mt-3 flex-1 text-sm text-slate-600">
                  {item.summary ?? "No description provided."}
                </p>
                <div className="mt-4 flex items-center justify-between">
                  {item.visibility === "PARTNER_ONLY" ? (
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 ring-1 ring-inset ring-slate-200">
                      Private to your partner
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">Public</span>
                  )}
                  <button
                    type="button"
                    disabled={item.installed || !item.latest_manifest_id}
                    onClick={() => setInspecting(item)}
                    className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:bg-slate-200 disabled:text-slate-500"
                  >
                    {item.installed ? "Installed" : "Review & install"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {inspecting ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white shadow-xl">
            <div className="border-b border-slate-200 p-5">
              <h2 className="text-lg font-semibold text-slate-900">
                {inspecting.name}
              </h2>
              <p className="text-sm text-slate-500">
                {inspecting.partner_name} · v{inspecting.latest_version}
              </p>
            </div>

            <div className="space-y-4 p-5">
              {detailQuery.isLoading ? (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" /> Verifying
                  signature…
                </div>
              ) : detailQuery.data ? (
                <>
                  <div
                    className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
                      detailQuery.data.signature_verified
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border-red-200 bg-red-50 text-red-800"
                    }`}
                  >
                    {detailQuery.data.signature_verified ? (
                      <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                    ) : (
                      <ShieldX className="mt-0.5 h-4 w-4 shrink-0" />
                    )}
                    <div>
                      <p className="font-medium">
                        {detailQuery.data.signature_verified
                          ? "Signature verified against an active publisher key."
                          : "Signature does not verify. This workflow cannot be installed."}
                      </p>
                      {detailQuery.data.verified_key_fingerprint ? (
                        <p className="mt-1 flex items-center gap-1 font-mono text-xs">
                          <Fingerprint className="h-3 w-3" />
                          {detailQuery.data.verified_key_fingerprint}
                        </p>
                      ) : null}
                    </div>
                  </div>

                  <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                    <p>
                      Installing creates an automation rule in this workspace.
                      It is created <strong>disabled</strong>; review it in
                      Automation and enable it deliberately.
                    </p>
                  </div>

                  <div>
                    <h3 className="mb-2 text-sm font-semibold text-slate-900">
                      What this workflow does
                    </h3>
                    <ul className="space-y-2">
                      {detailQuery.data.nodes.map((node) => (
                        <li
                          key={node.node_key}
                          className="rounded-md border border-slate-200 p-2 text-sm"
                        >
                          <div className="flex items-center gap-2">
                            <span
                              className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                                NODE_TONE[node.node_type]
                              }`}
                            >
                              {node.node_type}
                            </span>
                            <span className="font-mono text-xs text-slate-600">
                              {node.node_key}
                            </span>
                          </div>
                          {Object.keys(node.config).length > 0 ? (
                            <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 text-xs text-slate-700">
                              {JSON.stringify(node.config, null, 2)}
                            </pre>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <p className="font-mono text-xs text-slate-400">
                    {detailQuery.data.manifest.content_digest}
                  </p>
                </>
              ) : null}
            </div>

            <div className="flex justify-end gap-2 border-t border-slate-200 p-4">
              <button
                type="button"
                onClick={() => setInspecting(null)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                /* Disabled because the platform would refuse anyway. The schema
                   is what actually prevents an unsigned install. */
                disabled={
                  !detailQuery.data?.signature_verified ||
                  !workspaceId ||
                  installMutation.isPending
                }
                onClick={() => installMutation.mutate()}
                className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-200 disabled:text-slate-500"
              >
                {installMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : null}
                Install
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
