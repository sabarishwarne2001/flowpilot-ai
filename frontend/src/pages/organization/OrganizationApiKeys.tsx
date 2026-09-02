import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Trash2,
} from "lucide-react";

import {
  createApiKey,
  listApiKeys,
  revokeApiKey,
  rotateApiKey,
} from "@/services/api/apiKeys";
import { apiKeyKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { canManageMembers } from "@/permissions/organizationPermissions";
import { API_KEY_SCOPES } from "@/types/apiKey";
import type { ApiKeyRead, ApiKeyScope } from "@/types/apiKey";
import type { OrganizationRole } from "@/types/tenancy";
import { ApiError } from "@/services/api/client";

const SCOPE_BLURB: Readonly<Record<string, string>> = {
  "organizations:read": "Read organization details.",
  "workspaces:read": "List and read workspaces.",
  "workspaces:write": "Create and modify workspaces.",
  "members:read": "Read the member directory.",
  "work_items:read": "Read documents and their extracted content.",
  "work_items:write": "Upload and modify documents.",
  "audit_logs:read": "Read the audit log.",
  "files:read": "Download stored files.",
  "files:write": "Upload files.",
  "webhooks:read": "Read webhook endpoints and deliveries.",
  "webhooks:write": "Create and modify webhook endpoints.",
  "webhooks:admin": "Rotate webhook secrets and redeliver events.",
  "billing:read": "Read invoices, plan, and usage. Read-only by design.",
};

export const OrganizationApiKeys: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();
  const queryClient = useQueryClient();

  const canManage = canManageMembers(
    String(organizationRole).toUpperCase() as OrganizationRole,
  );

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<ApiKeyScope>>(new Set());
  const [expiresAt, setExpiresAt] = useState("");

  const [revealed, setRevealed] = useState<{ token: string; name: string } | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  const [confirmingRevoke, setConfirmingRevoke] = useState<string | null>(null);
  const [overlapConflict, setOverlapConflict] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: apiKeyKeys.list(organizationId),
    queryFn: () => listApiKeys(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: apiKeyKeys.all(organizationId) });

  const resetForm = () => {
    setCreating(false);
    setName("");
    setScopes(new Set());
    setExpiresAt("");
  };

  const create = useMutation({
    mutationFn: () =>
      createApiKey(organizationId, {
        name: name.trim(),
        scopes: [...scopes],
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      }),
    onSuccess: (result) => {
      setActionError(null);
      setRevealed({ token: result.token, name: result.api_key.name });
      resetForm();
      void invalidate();
    },
    onError: () =>
      setActionError(
        "That key couldn't be created. Check the name and that at least one " +
          "permission is selected.",
      ),
  });

  const rotate = useMutation({
    mutationFn: ({ keyId, force }: { keyId: string; force: boolean }) =>
      rotateApiKey(organizationId, keyId, force),
    onSuccess: (result) => {
      setActionError(null);
      setOverlapConflict(null);
      setRevealed({ token: result.token, name: result.api_key.name });
      void invalidate();
    },
    onError: (error: unknown, variables) => {
      const status = (error as ApiError)?.status ?? (error as any)?.response?.status;
      if (status === 409 && !variables.force) {
        setOverlapConflict(variables.keyId);
        return;
      }
      setActionError("That key couldn't be rotated. Please try again.");
    },
  });

  const revoke = useMutation({
    mutationFn: (keyId: string) => revokeApiKey(organizationId, keyId),
    onSuccess: () => {
      setActionError(null);
      setConfirmingRevoke(null);
      void invalidate();
    },
    onError: () =>
      setActionError("That key couldn't be revoked. It may already be revoked."),
  });

  const keys = data ?? [];
  const { active, revoked } = useMemo(
    () => ({
      active: keys.filter((k) => k.deactivated_at === null),
      revoked: keys.filter((k) => k.deactivated_at !== null),
    }),
    [keys],
  );

  const toggleScope = (scope: ApiKeyScope) =>
    setScopes((current) => {
      const next = new Set(current);
      if (next.has(scope)) {next.delete(scope);}
      else {next.add(scope);}
      return next;
    });

  const copyToken = async () => {
    if (!revealed) {return;}
    try {
      await navigator.clipboard.writeText(revealed.token);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading API keys…
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          API keys couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  const renderKey = (key: ApiKeyRead) => {
    const isRevoked = key.deactivated_at !== null;
    const expired =
      key.expires_at !== null && new Date(key.expires_at) < new Date();
    const overlapActive =
      key.previous_secret_expires_at !== null &&
      new Date(key.previous_secret_expires_at) > new Date();

    return (
      <li key={key.id} className={`p-4 ${isRevoked ? "opacity-60" : ""}`}>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
              {key.name}
              {isRevoked && (
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  Revoked
                </span>
              )}
              {!isRevoked && expired && (
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  Expired
                </span>
              )}
            </p>

            <p className="mt-0.5 text-xs text-muted-foreground">
              Created {new Date(key.created_at).toLocaleDateString()} · last
              used{" "}
              {key.last_used_at
                ? new Date(key.last_used_at).toLocaleDateString()
                : "never"}
              {key.expires_at &&
                ` · expires ${new Date(key.expires_at).toLocaleDateString()}`}
              {isRevoked &&
                key.deactivated_reason &&
                ` · ${key.deactivated_reason.toLowerCase()}`}
            </p>

            <div className="mt-1.5 flex flex-wrap gap-1">
              {key.scopes.map((scope) => (
                <span
                  key={scope}
                  title={SCOPE_BLURB[scope] ?? scope}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground"
                >
                  {scope}
                </span>
              ))}
            </div>

            {overlapActive && (
              <p className="mt-1.5 text-xs text-muted-foreground">
                The previous secret still works until{" "}
                {new Date(key.previous_secret_expires_at!).toLocaleString()} —
                migrate your integration before then.
              </p>
            )}
          </div>

          {canManage && !isRevoked && (
            <div className="flex flex-shrink-0 flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  setActionError(null);
                  rotate.mutate({ keyId: key.id, force: false });
                }}
                disabled={rotate.isPending}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted disabled:opacity-60"
              >
                <RefreshCw className="h-3 w-3" />
                Rotate
              </button>

              {confirmingRevoke === key.id ? (
                <>
                  <button
                    type="button"
                    onClick={() => revoke.mutate(key.id)}
                    disabled={revoke.isPending}
                    className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground disabled:opacity-60"
                  >
                    {revoke.isPending && (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    )}
                    Confirm revoke
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmingRevoke(null)}
                    className="rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground"
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setActionError(null);
                    setConfirmingRevoke(key.id);
                  }}
                  aria-label={`Revoke ${key.name}`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted"
                >
                  <Trash2 className="h-3 w-3" />
                  Revoke
                </button>
              )}
            </div>
          )}
        </div>

        {overlapConflict === key.id && (
          <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 p-3">
            <p className="text-sm font-medium text-destructive">
              A previous secret is still active
            </p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              This key was rotated recently and the old secret is still valid
              until{" "}
              {key.previous_secret_expires_at
                ? new Date(key.previous_secret_expires_at).toLocaleString()
                : "shortly"}
              . Rotating again now will stop the old secret working
              immediately, and anything still using it will start failing.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => rotate.mutate({ keyId: key.id, force: true })}
                disabled={rotate.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground disabled:opacity-60"
              >
                {rotate.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                Rotate anyway
              </button>
              <button
                type="button"
                onClick={() => setOverlapConflict(null)}
                className="rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground"
              >
                Wait
              </button>
            </div>
          </div>
        )}
      </li>
    );
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-foreground">API keys</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {organization.organization_name}
            </p>
          </div>

          {canManage && !creating && (
            <button
              type="button"
              onClick={() => {
                setActionError(null);
                setCreating(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              <KeyRound className="h-3.5 w-3.5" />
              New key
            </button>
          )}
        </header>

        {actionError && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {actionError}
          </p>
        )}

        {revealed && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="New API key"
            className="rounded-lg border-2 border-primary bg-card p-4"
          >
            <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <AlertTriangle className="h-4 w-4 text-primary" />
              Copy this key now — it won&apos;t be shown again
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              This is the only time <strong>{revealed.name}</strong> can be
              read. It isn&apos;t stored anywhere you can retrieve it. If you
              lose it, rotate the key to get a new one.
            </p>

            <div className="mt-3 flex items-center gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto rounded bg-muted p-2 font-mono text-xs text-foreground">
                {revealed.token}
              </code>
              <button
                type="button"
                onClick={() => void copyToken()}
                className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-md border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground hover:bg-muted"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>

            <button
              type="button"
              onClick={() => {
                setRevealed(null);
                setCopied(false);
              }}
              className="mt-3 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              I&apos;ve saved it
            </button>
          </div>
        )}

        {creating && (
          <div className="space-y-4 rounded-lg border border-border bg-card p-4">
            <div>
              <label htmlFor="key-name" className="text-sm font-medium text-foreground">
                Name
              </label>
              <input
                id="key-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
                placeholder="CI pipeline"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Name it after what will use it, so a future reader can tell
                what breaks if it&apos;s revoked.
              </p>
            </div>

            <fieldset>
              <legend className="text-sm font-medium text-foreground">Permissions</legend>
              <p className="mb-2 text-xs text-muted-foreground">
                Grant only what this key needs. Ownership and member-write
                permissions cannot be granted to a key at all.
              </p>
              <div className="grid gap-1.5 sm:grid-cols-2">
                {API_KEY_SCOPES.map((scope) => (
                  <label
                    key={scope}
                    title={SCOPE_BLURB[scope]}
                    className="flex items-start gap-2 text-sm text-foreground cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={scopes.has(scope)}
                      onChange={() => toggleScope(scope)}
                      className="mt-0.5"
                    />
                    <span className="font-mono text-xs">{scope}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div>
              <label htmlFor="key-expiry" className="text-sm font-medium text-foreground">
                Expires <span className="text-muted-foreground">(optional)</span>
              </label>
              <input
                id="key-expiry"
                type="date"
                value={expiresAt}
                onChange={(event) => setExpiresAt(event.target.value)}
                className="mt-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                A key with no expiry lives until someone revokes it.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
              <button
                type="button"
                onClick={() => create.mutate()}
                disabled={
                  create.isPending || name.trim().length === 0 || scopes.size === 0
                }
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {create.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Create key
              </button>
              <button
                type="button"
                onClick={resetForm}
                disabled={create.isPending}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              >
                Cancel
              </button>
              {scopes.size === 0 && (
                <span className="text-xs text-muted-foreground">
                  Select at least one permission.
                </span>
              )}
            </div>
          </div>
        )}

        {active.length === 0 && !creating ? (
          <div className="rounded-lg border border-border bg-card p-6 text-center">
            <KeyRound className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium text-foreground">No API keys</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              API keys let scripts and integrations act on this organization
              without a person signing in.
            </p>
          </div>
        ) : (
          active.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border bg-card">
              {active.map(renderKey)}
            </ul>
          )
        )}

        {revoked.length > 0 && (
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Revoked
            </h2>
            <ul className="divide-y divide-border rounded-lg border border-border bg-card">
              {revoked.map(renderKey)}
            </ul>
          </div>
        )}

        {!canManage && (
          <p className="border-t border-border pt-4 text-xs text-muted-foreground">
            Creating, rotating, and revoking API keys requires an organization
            owner or administrator.
          </p>
        )}
      </div>
    </div>
  );
};

export default OrganizationApiKeys;
