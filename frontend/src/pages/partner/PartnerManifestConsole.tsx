import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, Package, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { ErrorState } from "@/components/common/ErrorState";
import {
  BUTTON_DESTRUCTIVE,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  HINT,
  INPUT,
  INPUT_MONO,
  SCROLL_X,
  SECTION_TITLE,
  SELECT,
  SURFACE,
  TABLE_HEAD,
  TABLE_ROW,
  TEXTAREA,
} from "@/components/ui/primitives";
import { errorMessage } from "@/services/api/errors";
import { partnerApi } from "@/services/api/partner";
import { partnerKeys } from "@/services/api/queryKeys";
import type {
  ManifestDigest,
  ManifestGraph,
  MarketplaceItem,
  MarketplaceItemStatus,
  MarketplaceVisibility,
  SigningKey,
} from "@/types/partner";
import { formatTimestampDate } from "@/utils/displayTime";

/**
 * HM-S1:partner-console — the partner administrator's manifest console.
 *
 * Signing keys, catalog items and signed workflow manifests, on the ARCH-27
 * endpoints plus three added for this console (digest preview, manifest
 * history, item lifecycle). The PRIVATE key never reaches the browser or the
 * platform: the console computes the canonical digest server-side, the partner
 * signs it offline, and pastes the base64 signature back. Publishing verifies
 * the signature against a registered, active public key before anything is
 * stored.
 */

const SAMPLE_GRAPH = `{
  "nodes": [
    { "node_key": "start", "node_type": "trigger", "config": { "event": "document.processed" } },
    { "node_key": "notify", "node_type": "action", "config": { "action": "notification.send" } }
  ],
  "edges": [{ "from_node_key": "start", "to_node_key": "notify", "branch": "default" }]
}`;

const statusTone = (status: string): string =>
  status === "ACTIVE" || status === "PUBLISHED"
    ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
    : status === "DRAFT"
      ? "bg-muted text-muted-foreground"
      : "bg-amber-500/10 text-amber-700 dark:text-amber-300";

const Pill: React.FC<{ readonly value: string }> = ({ value }) => (
  <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${statusTone(value)}`}>
    {value}
  </span>
);

const parseGraph = (text: string): ManifestGraph => {
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || !Array.isArray((parsed as { nodes?: unknown }).nodes)) {
    throw new Error('The manifest must be a JSON object with a "nodes" array (and an optional "edges" array).');
  }
  const graph = parsed as { nodes: ManifestGraph["nodes"]; edges?: ManifestGraph["edges"] };
  return { nodes: graph.nodes, edges: graph.edges ?? [] };
};

// ---------------------------------------------------------------------------
// Signing keys
// ---------------------------------------------------------------------------

const SigningKeysPanel: React.FC<{ readonly partnerId: string }> = ({ partnerId }) => {
  const queryClient = useQueryClient();
  const keysQuery = useQuery({
    queryKey: partnerKeys.signingKeys(partnerId),
    queryFn: () => partnerApi.listSigningKeys(partnerId),
  });
  const [keyId, setKeyId] = useState("");
  const [algorithm, setAlgorithm] = useState<"ED25519" | "RSA_PSS_SHA256">("ED25519");
  const [pem, setPem] = useState("");
  const [revoking, setRevoking] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const refresh = () => queryClient.invalidateQueries({ queryKey: partnerKeys.signingKeys(partnerId) });

  const register = useMutation({
    mutationFn: () =>
      partnerApi.registerSigningKey(partnerId, { key_id: keyId.trim(), algorithm, public_key_pem: pem.trim() }),
    onSuccess: async () => {
      setKeyId("");
      setPem("");
      await refresh();
      toast.success("Signing key registered.");
    },
    onError: (error) => toast.error(errorMessage(error, "The key could not be registered.")),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => partnerApi.revokeSigningKey(partnerId, id, reason.trim()),
    onSuccess: async () => {
      setRevoking(null);
      setReason("");
      await refresh();
      toast.success("Signing key revoked. Manifests it signed no longer verify.");
    },
    onError: (error) => toast.error(errorMessage(error, "The key could not be revoked.")),
  });

  return (
    <section className={`${SURFACE} space-y-4 p-5`} aria-labelledby="partner-keys">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 id="partner-keys" className={SECTION_TITLE}>
          Signing keys
        </h2>
      </div>
      <p className={HINT}>
        Register the PUBLIC half of each key you sign manifests with. Private keys are never uploaded.
      </p>

      {keysQuery.isError ? (
        <ErrorState
          description={errorMessage(keysQuery.error, "Signing keys couldn't be loaded.")}
          onRetry={() => void keysQuery.refetch()}
        />
      ) : keysQuery.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading keys…
        </p>
      ) : (keysQuery.data?.length ?? 0) === 0 ? (
        <p className="text-sm text-muted-foreground">No signing keys yet.</p>
      ) : (
        <div className={SCROLL_X}>
          <table className="w-full text-sm">
            <thead className={TABLE_HEAD}>
              <tr>
                <th className="py-2 pr-3">Key id</th>
                <th className="py-2 pr-3">Algorithm</th>
                <th className="py-2 pr-3">Fingerprint</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Added</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {(keysQuery.data ?? []).map((key: SigningKey) => (
                <tr key={key.id} className={TABLE_ROW}>
                  <td className="py-2 pr-3 font-mono text-xs">{key.key_id}</td>
                  <td className="py-2 pr-3 text-xs">{key.algorithm}</td>
                  <td className="max-w-[14rem] truncate py-2 pr-3 font-mono text-xs" title={key.fingerprint}>
                    {key.fingerprint}
                  </td>
                  <td className="py-2 pr-3">
                    <Pill value={key.status} />
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted-foreground">{formatTimestampDate(key.created_at)}</td>
                  <td className="py-2 text-right">
                    {key.status === "ACTIVE" ? (
                      revoking === key.key_id ? (
                        <span className="inline-flex flex-wrap items-center justify-end gap-2">
                          <input
                            aria-label="Reason for revoking"
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            placeholder="Reason (required)"
                            className={`${INPUT} w-44`}
                          />
                          <button
                            type="button"
                            className={BUTTON_DESTRUCTIVE}
                            disabled={!reason.trim() || revoke.isPending}
                            onClick={() => revoke.mutate(key.key_id)}
                          >
                            Confirm revoke
                          </button>
                          <button type="button" className={BUTTON_SECONDARY} onClick={() => setRevoking(null)}>
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button type="button" className={BUTTON_DESTRUCTIVE} onClick={() => setRevoking(key.key_id)}>
                          Revoke
                        </button>
                      )
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form
        className="grid gap-3 border-t border-border/60 pt-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          register.mutate();
        }}
      >
        <label className="space-y-1 text-sm">
          <span className="font-medium">Key id</span>
          <input value={keyId} onChange={(event) => setKeyId(event.target.value)} className={INPUT} placeholder="release-2026" />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Algorithm</span>
          <select
            value={algorithm}
            onChange={(event) => setAlgorithm(event.target.value as "ED25519" | "RSA_PSS_SHA256")}
            className={SELECT}
          >
            <option value="ED25519">Ed25519</option>
            <option value="RSA_PSS_SHA256">RSA-PSS SHA-256</option>
          </select>
        </label>
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="font-medium">Public key (PEM)</span>
          <textarea
            value={pem}
            onChange={(event) => setPem(event.target.value)}
            className={`${TEXTAREA} font-mono text-xs`}
            placeholder={"-----BEGIN PUBLIC KEY-----\n…\n-----END PUBLIC KEY-----"}
          />
        </label>
        <div className="sm:col-span-2">
          <button type="submit" className={BUTTON_PRIMARY} disabled={!keyId.trim() || !pem.trim() || register.isPending}>
            {register.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            Register public key
          </button>
        </div>
      </form>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Manifests of one catalog item
// ---------------------------------------------------------------------------

const ManifestPanel: React.FC<{
  readonly partnerId: string;
  readonly item: MarketplaceItem;
  readonly activeKeys: readonly SigningKey[];
}> = ({ partnerId, item, activeKeys }) => {
  const queryClient = useQueryClient();
  const manifestsQuery = useQuery({
    queryKey: partnerKeys.manifests(partnerId, item.id),
    queryFn: () => partnerApi.listManifests(partnerId, item.id),
  });
  const [version, setVersion] = useState("1.0.0");
  const [graphText, setGraphText] = useState(SAMPLE_GRAPH);
  const [digest, setDigest] = useState<ManifestDigest | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [signingKeyId, setSigningKeyId] = useState("");
  const [signature, setSignature] = useState("");

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: partnerKeys.manifests(partnerId, item.id) }),
      queryClient.invalidateQueries({ queryKey: partnerKeys.catalog(partnerId) }),
    ]);
  };

  const computeDigest = useMutation({
    mutationFn: (graph: ManifestGraph) => partnerApi.previewManifestDigest(partnerId, graph),
    onSuccess: (result) => setDigest(result),
    onError: (error) => setGraphError(errorMessage(error, "The digest could not be computed.")),
  });

  const publish = useMutation({
    mutationFn: (graph: ManifestGraph) =>
      partnerApi.publishManifest(partnerId, item.id, {
        ...graph,
        version: version.trim(),
        signing_key_id: signingKeyId,
        signature: signature.trim(),
      }),
    onSuccess: async (manifest) => {
      setSignature("");
      setDigest(null);
      await refresh();
      toast.success(`Version ${manifest.version} published and signature verified.`);
    },
    onError: (error) => toast.error(errorMessage(error, "The manifest was not published.")),
  });

  const lifecycle = useMutation({
    mutationFn: (change: { status?: MarketplaceItemStatus; visibility?: MarketplaceVisibility }) =>
      partnerApi.updateCatalogItem(partnerId, item.id, change),
    onSuccess: async () => {
      await refresh();
      toast.success("Catalog item updated.");
    },
    onError: (error) => toast.error(errorMessage(error, "The item could not be updated.")),
  });

  const withGraph = (run: (graph: ManifestGraph) => void) => {
    setGraphError(null);
    try {
      run(parseGraph(graphText));
    } catch (error) {
      setGraphError(error instanceof Error ? error.message : "The manifest is not valid JSON.");
    }
  };

  return (
    <section className={`${SURFACE} space-y-4 p-5`} aria-labelledby="partner-manifests">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden />
          <h2 id="partner-manifests" className={SECTION_TITLE}>
            {item.name} — manifests
          </h2>
          <Pill value={item.status} />
        </div>
        <div className="flex flex-wrap gap-2">
          {item.status !== "PUBLISHED" ? (
            <button type="button" className={BUTTON_PRIMARY} disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ status: "PUBLISHED" })}>
              List in marketplace
            </button>
          ) : (
            <button type="button" className={BUTTON_SECONDARY} disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ status: "DEPRECATED" })}>
              Deprecate
            </button>
          )}
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={lifecycle.isPending}
            onClick={() => lifecycle.mutate({ visibility: item.visibility === "PUBLIC" ? "PARTNER_ONLY" : "PUBLIC" })}
          >
            Make {item.visibility === "PUBLIC" ? "partner-only" : "public"}
          </button>
          {item.status !== "WITHDRAWN" ? (
            <button type="button" className={BUTTON_DESTRUCTIVE} disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ status: "WITHDRAWN" })}>
              Withdraw
            </button>
          ) : null}
        </div>
      </div>

      {manifestsQuery.isError ? (
        <ErrorState
          description={errorMessage(manifestsQuery.error, "Manifests couldn't be loaded.")}
          onRetry={() => void manifestsQuery.refetch()}
        />
      ) : manifestsQuery.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading manifests…
        </p>
      ) : (manifestsQuery.data?.length ?? 0) === 0 ? (
        <p className="text-sm text-muted-foreground">No manifest published for this item yet.</p>
      ) : (
        <div className={SCROLL_X}>
          <table className="w-full text-sm">
            <thead className={TABLE_HEAD}>
              <tr>
                <th className="py-2 pr-3">Version</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Digest</th>
                <th className="py-2 pr-3">Graph</th>
                <th className="py-2 pr-3">Signed with</th>
                <th className="py-2">Published</th>
              </tr>
            </thead>
            <tbody>
              {(manifestsQuery.data ?? []).map((manifest) => (
                <tr key={manifest.id} className={TABLE_ROW}>
                  <td className="py-2 pr-3 font-mono text-xs">{manifest.version}</td>
                  <td className="py-2 pr-3">
                    <Pill value={manifest.status} />
                  </td>
                  <td className="max-w-[12rem] truncate py-2 pr-3 font-mono text-xs" title={manifest.content_digest}>
                    {manifest.content_digest}
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    {manifest.node_count} nodes · {manifest.edge_count} edges
                  </td>
                  <td className="max-w-[10rem] truncate py-2 pr-3 font-mono text-xs">
                    {manifest.signatures.map((sig) => sig.signing_key_fingerprint ?? sig.algorithm).join(", ") || "—"}
                  </td>
                  <td className="py-2 text-xs text-muted-foreground">
                    {manifest.published_at ? formatTimestampDate(manifest.published_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="space-y-3 border-t border-border/60 pt-4">
        <h3 className="text-sm font-semibold text-foreground">Publish a new version</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1 text-sm">
            <span className="font-medium">Version</span>
            <input value={version} onChange={(event) => setVersion(event.target.value)} className={INPUT_MONO} />
          </label>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span className="font-medium">Signing key</span>
            <select value={signingKeyId} onChange={(event) => setSigningKeyId(event.target.value)} className={SELECT}>
              <option value="">Choose an active key…</option>
              {activeKeys.map((key) => (
                <option key={key.id} value={key.key_id}>
                  {key.key_id} ({key.algorithm})
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Workflow graph (JSON)</span>
          <textarea
            value={graphText}
            onChange={(event) => {
              setGraphText(event.target.value);
              setDigest(null);
            }}
            className={`${TEXTAREA} min-h-[10rem] font-mono text-xs`}
            spellCheck={false}
          />
        </label>
        {graphError ? (
          <p role="alert" className="text-sm text-destructive">
            {graphError}
          </p>
        ) : null}
        <button
          type="button"
          className={BUTTON_SECONDARY}
          disabled={computeDigest.isPending}
          onClick={() => withGraph((graph) => computeDigest.mutate(graph))}
        >
          1. Compute the digest to sign
        </button>
        {digest ? (
          <div className="space-y-2 rounded-lg border border-border/60 bg-muted/40 p-3">
            <p className="text-xs text-muted-foreground">
              Sign exactly this text ({digest.node_count} nodes, {digest.edge_count} edges) with the private key, offline:
            </p>
            <code className="block break-all rounded bg-background p-2 font-mono text-xs">{digest.signing_input}</code>
            <p className="text-xs text-muted-foreground">
              Ed25519 with OpenSSL 3:{" "}
              <code className="break-all font-mono">
                printf %s &apos;{digest.signing_input}&apos; | openssl pkeyutl -sign -inkey private.pem -rawin | base64 -w0
              </code>
            </p>
          </div>
        ) : null}
        <label className="block space-y-1 text-sm">
          <span className="font-medium">2. Signature (base64)</span>
          <textarea
            value={signature}
            onChange={(event) => setSignature(event.target.value)}
            className={`${TEXTAREA} font-mono text-xs`}
            spellCheck={false}
          />
        </label>
        <button
          type="button"
          className={BUTTON_PRIMARY}
          disabled={!digest || !signingKeyId || !signature.trim() || !version.trim() || publish.isPending}
          onClick={() => withGraph((graph) => publish.mutate(graph))}
        >
          {publish.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
          3. Verify signature and publish
        </button>
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Console
// ---------------------------------------------------------------------------

export const PartnerManifestConsole: React.FC<{ readonly partnerId: string }> = ({ partnerId }) => {
  const queryClient = useQueryClient();
  const catalogQuery = useQuery({
    queryKey: partnerKeys.catalog(partnerId),
    queryFn: () => partnerApi.listCatalog(partnerId),
  });
  const keysQuery = useQuery({
    queryKey: partnerKeys.signingKeys(partnerId),
    queryFn: () => partnerApi.listSigningKeys(partnerId),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [visibility, setVisibility] = useState<MarketplaceVisibility>("PARTNER_ONLY");

  const create = useMutation({
    mutationFn: () =>
      partnerApi.createCatalogItem(partnerId, {
        slug: slug.trim(),
        name: name.trim(),
        visibility,
        ...(summary.trim() ? { summary: summary.trim() } : {}),
      }),
    onSuccess: async (item) => {
      setSlug("");
      setName("");
      setSummary("");
      setSelectedId(item.id);
      await queryClient.invalidateQueries({ queryKey: partnerKeys.catalog(partnerId) });
      toast.success(`${item.name} created. Publish a signed manifest to list it.`);
    },
    onError: (error) => toast.error(errorMessage(error, "The item could not be created.")),
  });

  const items = catalogQuery.data ?? [];
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const activeKeys = (keysQuery.data ?? []).filter((key) => key.status === "ACTIVE");

  return (
    <div className="space-y-6">
      <SigningKeysPanel partnerId={partnerId} />

      <section className={`${SURFACE} space-y-4 p-5`} aria-labelledby="partner-catalog">
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-muted-foreground" aria-hidden />
          <h2 id="partner-catalog" className={SECTION_TITLE}>
            Catalog items
          </h2>
        </div>
        {catalogQuery.isError ? (
          <ErrorState
            description={errorMessage(catalogQuery.error, "The catalog couldn't be loaded.")}
            onRetry={() => void catalogQuery.refetch()}
          />
        ) : catalogQuery.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading catalog…
          </p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No catalog items yet. Create one below.</p>
        ) : (
          <div className={SCROLL_X}>
            <table className="w-full text-sm">
              <thead className={TABLE_HEAD}>
                <tr>
                  <th className="py-2 pr-3">Item</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Visibility</th>
                  <th className="py-2 pr-3">Latest version</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className={`${TABLE_ROW} ${item.id === selectedId ? "bg-muted/40" : ""}`}>
                    <td className="py-2 pr-3">
                      <span className="font-medium text-foreground">{item.name}</span>
                      <span className="ml-2 font-mono text-xs text-muted-foreground">{item.slug}</span>
                    </td>
                    <td className="py-2 pr-3">
                      <Pill value={item.status} />
                    </td>
                    <td className="py-2 pr-3 text-xs">{item.visibility}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{item.latest_version ?? "—"}</td>
                    <td className="py-2 text-right">
                      <button type="button" className={BUTTON_SECONDARY} onClick={() => setSelectedId(item.id)}>
                        Manage manifests
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <form
          className="grid gap-3 border-t border-border/60 pt-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label className="space-y-1 text-sm">
            <span className="font-medium">Name</span>
            <input value={name} onChange={(event) => setName(event.target.value)} className={INPUT} placeholder="Invoice triage" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Slug</span>
            <input value={slug} onChange={(event) => setSlug(event.target.value)} className={INPUT_MONO} placeholder="invoice-triage" />
          </label>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span className="font-medium">Summary</span>
            <input value={summary} onChange={(event) => setSummary(event.target.value)} className={INPUT} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Visibility</span>
            <select
              value={visibility}
              onChange={(event) => setVisibility(event.target.value as MarketplaceVisibility)}
              className={SELECT}
            >
              <option value="PARTNER_ONLY">Partner only</option>
              <option value="PUBLIC">Public</option>
            </select>
          </label>
          <div className="flex items-end">
            <button type="submit" className={BUTTON_PRIMARY} disabled={!slug.trim() || !name.trim() || create.isPending}>
              Create catalog item
            </button>
          </div>
        </form>
      </section>

      {selected ? <ManifestPanel partnerId={partnerId} item={selected} activeKeys={activeKeys} /> : null}
    </div>
  );
};

export default PartnerManifestConsole;
