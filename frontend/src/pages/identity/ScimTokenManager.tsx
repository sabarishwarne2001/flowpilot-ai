import React, { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Copy,
  Key,
  Loader2,
  RotateCw,
  Trash2,
  X,
} from "lucide-react";

import {
  createScimKey,
  listDirectory,
  listIdpConfigs,
  listScimKeys,
  revokeScimKey,
  rotateScimKey,
} from "@/services/api/identity";
import { identityKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import type { ScimKeyIssued, ScimKeyRead } from "@/types/identity";

export const ScimTokenManager: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const queryClient = useQueryClient();
  const isOwner = String(organizationRole).toUpperCase() === "OWNER";

  const [issued, setIssued] = useState<ScimKeyIssued | null>(null);
  const [creatingFor, setCreatingFor] = useState<string | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

  const keysQuery = useQuery({
    queryKey: identityKeys.scimKeys(organizationId),
    queryFn: () => listScimKeys(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const configsQuery = useQuery({
    queryKey: identityKeys.idpConfigs(organizationId),
    queryFn: () => listIdpConfigs(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const directoryQuery = useQuery({
    queryKey: identityKeys.directory(organizationId),
    queryFn: () => listDirectory(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const invalidate = useCallback(
    () =>
      queryClient.invalidateQueries({
        queryKey: identityKeys.scimKeys(organizationId),
      }),
    [queryClient, organizationId],
  );

  const create = useMutation({
    mutationFn: (idpConfigId: string) =>
      createScimKey(organizationId, {
        idp_config_id: idpConfigId,
        display_name: "SCIM",
      }),
    onSuccess: async (result) => {
      setIssued(result);
      setCreatingFor(null);
      await invalidate();
    },
  });

  const rotate = useMutation({
    mutationFn: (keyId: string) => rotateScimKey(organizationId, keyId),
    onSuccess: async (result) => {
      setIssued(result);
      await invalidate();
    },
  });

  const revoke = useMutation({
    mutationFn: (keyId: string) => revokeScimKey(organizationId, keyId),
    onSuccess: async () => {
      setConfirmRevoke(null);
      await invalidate();
    },
  });

  const keys = keysQuery.data ?? [];
  const configs = configsQuery.data ?? [];
  const directory = directoryQuery.data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">SCIM directory sync</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Tokens your identity provider uses to create and deactivate members
            automatically.
          </p>
        </div>

        {isOwner && configs.length > 0 && (
          <div className="flex gap-2">
            <select
              value={creatingFor ?? ""}
              onChange={(e) => setCreatingFor(e.target.value || null)}
              aria-label="Identity provider for new token"
              className="rounded-md border border-border bg-background px-2 py-1.5 text-xs"
            >
              <option value="">Choose a provider…</option>
              {configs.map((config) => (
                <option key={config.id} value={config.id}>
                  {config.display_name}
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={() => creatingFor && create.mutate(creatingFor)}
              disabled={!creatingFor || create.isPending}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {create.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Issue token
            </button>
          </div>
        )}
      </div>

      {keysQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading tokens…</p>
      ) : keys.length === 0 ? (
        <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          No SCIM tokens yet. Issue one to let your identity provider manage
          membership.
        </p>
      ) : (
        <ul className="space-y-2">
          {keys.map((key) => (
            <ScimKeyRow
              key={key.id}
              scimKey={key}
              isOwner={isOwner}
              rotating={rotate.isPending}
              onRotate={() => rotate.mutate(key.id)}
              onRevoke={() => setConfirmRevoke(key.id)}
            />
          ))}
        </ul>
      )}

      <section>
        <h3 className="text-sm font-medium">Directory</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Identities your provider has created or deactivated.
        </p>

        {directory.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">
            Nothing has been synced yet.
          </p>
        ) : (
          <div className="mt-2 overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40">
                <tr className="text-left text-xs text-muted-foreground">
                  <th scope="col" className="px-3 py-2 font-medium">User</th>
                  <th scope="col" className="px-3 py-2 font-medium">Source</th>
                  <th scope="col" className="px-3 py-2 font-medium">Status</th>
                  <th scope="col" className="px-3 py-2 font-medium">Last sync</th>
                </tr>
              </thead>
              <tbody>
                {directory.slice(0, 50).map((identity) => (
                  <tr key={identity.id} className="border-t border-border">
                    <td className="px-3 py-2">
                      <span className="block truncate">{identity.user_name}</span>
                      <span className="block truncate font-mono text-[11px] text-muted-foreground">
                        {identity.external_id}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {identity.provisioned_via}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {identity.active ? (
                        <span className="text-emerald-700">Active</span>
                      ) : (
                        <span className="text-muted-foreground">
                          Deactivated
                          {identity.deprovision_reason &&
                            ` · ${identity.deprovision_reason}`}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {identity.last_synced_at
                        ? new Date(identity.last_synced_at).toLocaleString()
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {issued && (
        <SecretDialog issued={issued} onClose={() => setIssued(null)} />
      )}

      {confirmRevoke && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="revoke-title"
            className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-2xl"
          >
            <h3 id="revoke-title" className="text-base font-semibold">
              Revoke this token?
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Directory sync stops immediately. Your identity provider will no
              longer be able to add members —{" "}
              <strong className="font-medium text-foreground">
                or deactivate them
              </strong>
              . Someone removed in your IdP after this point will keep their
              access here until you remove them manually.
            </p>

            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmRevoke(null)}
                disabled={revoke.isPending}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => revoke.mutate(confirmRevoke)}
                disabled={revoke.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-sm text-destructive-foreground hover:opacity-90 disabled:opacity-50"
              >
                {revoke.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Revoke
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

interface ScimKeyRowProps {
  readonly scimKey: ScimKeyRead;
  readonly isOwner: boolean;
  readonly rotating: boolean;
  readonly onRotate: () => void;
  readonly onRevoke: () => void;
}

const ScimKeyRow: React.FC<ScimKeyRowProps> = ({
  scimKey,
  isOwner,
  rotating,
  onRotate,
  onRevoke,
}) => {
  const revoked = scimKey.revoked_at !== null;
  const overlapActive =
    scimKey.previous_secret_expires_at !== null &&
    Date.parse(scimKey.previous_secret_expires_at) > Date.now();

  return (
    <li className="rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-3">
        <Key className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{scimKey.display_name}</span>
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
              {scimKey.key_prefix}…
            </span>
            {revoked && (
              <span className="rounded bg-destructive/15 px-1.5 py-0.5 text-[11px] text-destructive">
                Revoked
              </span>
            )}
          </div>

          <p className="mt-0.5 text-xs text-muted-foreground">
            {scimKey.last_used_at
              ? `Last used ${new Date(scimKey.last_used_at).toLocaleString()}`
              : "Never used"}
            {scimKey.scopes.length > 0 && ` · ${scimKey.scopes.join(", ")}`}
          </p>
        </div>

        {isOwner && !revoked && (
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              onClick={onRotate}
              disabled={rotating}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
            >
              {rotating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCw className="h-3.5 w-3.5" />
              )}
              Rotate
            </button>

            <button
              type="button"
              onClick={onRevoke}
              aria-label={`Revoke ${scimKey.display_name}`}
              className="rounded-md border border-border p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      {overlapActive && (
        <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-2.5">
          <p className="flex items-start gap-1.5 text-xs text-amber-800">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              <strong className="font-medium">
                The previous secret still works
              </strong>{" "}
              until{" "}
              {new Date(
                scimKey.previous_secret_expires_at as string,
              ).toLocaleString()}
              . Update your identity provider before then.
            </span>
          </p>

          <p className="mt-1.5 pl-5 text-[11px] text-amber-800/80">
            {scimKey.previous_last_used_at
              ? `The old secret was last used ${new Date(scimKey.previous_last_used_at).toLocaleString()}. Once this stops advancing, your provider has moved over.`
              : "The old secret has not been used since rotation — your provider may already be using the new one."}
          </p>
        </div>
      )}
    </li>
  );
};

const SecretDialog: React.FC<{
  issued: ScimKeyIssued;
  onClose: () => void;
}> = ({ issued, onClose }) => {
  const [copied, setCopied] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(issued.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Ignored
    }
  }, [issued.token]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="secret-title"
        className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3">
          <h3 id="secret-title" className="text-base font-semibold">
            Copy this token now
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted-foreground hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mt-1 text-sm text-muted-foreground">
          It is not retrievable after you close this dialog.
        </p>

        <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-background p-2.5">
          <code className="min-w-0 flex-1 break-all font-mono text-xs">
            {issued.token}
          </code>
          <button
            type="button"
            onClick={() => void copy()}
            aria-label="Copy token"
            className="shrink-0 rounded border border-border p-1.5 hover:bg-muted"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        <p className="mt-3 rounded-md border border-border bg-muted/40 p-3 text-xs leading-relaxed">
          {issued.note}
        </p>

        <label className="mt-3 flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border"
          />
          I have stored this token somewhere safe.
        </label>

        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={!acknowledged}
            className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};

export default ScimTokenManager;
