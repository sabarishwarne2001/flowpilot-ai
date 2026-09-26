/**
 * ARCH47-S2:new-target — register where postings go (workspace admins). The
 * choices come from the server's catalog: a format, how it travels, which ERP
 * preset reads it, how the ERP acknowledges it, and how FlowPilot signs in.
 * The credential is sent once and stored encrypted; it is never shown again.
 * Every URL and host is checked by the server before the target is saved.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { CredentialFields } from "@/components/erp/CredentialFields";
import { BUTTON_GHOST, BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SELECT, SURFACE, TEXTAREA } from "@/components/ui/primitives";
import { createTarget, erpKeys } from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import {
  ACK_LABELS, AUTH_LABELS, OBJECT_LABELS, SOURCE_KINDS, SOURCE_LABELS, TRANSPORT_LABELS, configTemplate,
  type AckMode, type AuthMode, type ErpCatalog, type ObjectKind, type PostingFormat, type PostingPreset,
  type PostingTransport, type SourceKind, type TargetDetail,
} from "@/types/erp";

interface NewTargetProps {
  readonly workspaceId: string;
  readonly catalog: ErpCatalog;
  readonly onCreated: (detail: TargetDetail) => void;
  readonly onCancel: () => void;
}

const isOAuth = (mode: string): boolean => mode === "OAUTH2_REFRESH_TOKEN" || mode === "OAUTH2_CLIENT_CREDENTIALS";

export const NewTarget: React.FC<NewTargetProps> = ({ workspaceId, catalog, onCreated, onCancel }) => {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [format, setFormat] = useState<PostingFormat>("CSV");
  const formatInfo = catalog.formats.find((f) => f.key === format);
  const transports = formatInfo?.transports ?? [];
  const [transportPick, setTransport] = useState<PostingTransport>("DOWNLOAD");
  const transport = transports.includes(transportPick) ? transportPick : (transports[0] ?? "DOWNLOAD");
  const [presetPick, setPreset] = useState<PostingPreset>("QUICKBOOKS_ONLINE");
  const preset: PostingPreset = format === "JSON" ? presetPick : "NONE";
  const presetInfo = catalog.presets.find((p) => p.key === preset);
  const ackModes = (catalog.ack_modes[transport] ?? []).filter((m) => m !== "X12_997" || format === "X12");
  const [ackPick, setAck] = useState<AckMode | "">("");
  const ackMode = ackModes.includes(ackPick as AckMode) ? (ackPick as AckMode) : (ackModes[0] ?? "MANUAL");
  const authModes = (catalog.auth_modes[transport] ?? []).filter((m) => !presetInfo || presetInfo.auth_modes.includes(m));
  const [authPick, setAuth] = useState<AuthMode | "">("");
  const authMode = authModes.includes(authPick as AuthMode) ? (authPick as AuthMode) : (authModes[0] ?? "NONE");
  const objects = format === "JSON" ? (presetInfo?.objects ?? []) : (formatInfo?.objects ?? []);

  const template = useMemo(() => {
    const cfg = configTemplate(format, transport, preset);
    if (isOAuth(authMode)) {
      cfg.oauth = { token_url: "https://" };
    }
    return JSON.stringify(cfg, null, 2);
  }, [format, transport, preset, authMode]);
  const [configText, setConfigText] = useState<string | null>(null);
  const configShown = configText ?? template;
  const [credential, setCredential] = useState<Record<string, string>>({});
  const [prefix, setPrefix] = useState("");
  const [autoPost, setAutoPost] = useState(false);
  const [autoSources, setAutoSources] = useState<readonly SourceKind[]>([]);
  const [autoObjects, setAutoObjects] = useState<readonly ObjectKind[]>([]);
  const [maxAttempts, setMaxAttempts] = useState("");
  const [ackHours, setAckHours] = useState("");
  const [localError, setLocalError] = useState("");

  const create = useMutation({
    mutationFn: (config: Record<string, unknown>) => createTarget(workspaceId, {
      name: name.trim(), format, transport, preset, ack_mode: ackMode, auth_mode: authMode, config,
      credential: authMode === "NONE" || Object.keys(credential).length === 0 ? null : credential,
      lookup_prefix: prefix.trim() || null, auto_post: autoPost, auto_sources: autoSources,
      auto_objects: autoObjects.filter((k) => objects.includes(k)),
      max_attempts: maxAttempts ? Number(maxAttempts) : null, ack_timeout_hours: ackHours ? Number(ackHours) : null,
    }),
    onSuccess: (detail) => {
      void queryClient.invalidateQueries({ queryKey: erpKeys.all(workspaceId) });
      onCreated(detail);
    },
  });
  const submit = (event: React.FormEvent): void => {
    event.preventDefault();
    setLocalError("");
    let config: unknown;
    try {
      config = JSON.parse(configShown);
    } catch {
      setLocalError("The configuration is not valid JSON.");
      return;
    }
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      setLocalError("The configuration is a JSON object.");
      return;
    }
    create.mutate(config as Record<string, unknown>);
  };
  const resetConfig = (): void => setConfigText(null);
  const toggle = <T extends string>(list: readonly T[], item: T, setter: (v: readonly T[]) => void): void =>
    setter(list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);

  return (
    <form className={`${SURFACE} space-y-4 p-4`} onSubmit={submit} aria-labelledby="new-target-title">
      <h2 id="new-target-title" className="text-base font-semibold">New ERP target</h2>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Name</span>
          <input className={INPUT} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} placeholder="QuickBooks — main company" />
        </label>
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Format</span>
          <select className={SELECT} value={format} onChange={(e) => { setFormat(e.target.value as PostingFormat); resetConfig(); }}>
            {catalog.formats.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
        </label>
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Delivery</span>
          <select className={SELECT} value={transport} onChange={(e) => { setTransport(e.target.value as PostingTransport); resetConfig(); }}>
            {transports.map((t) => <option key={t} value={t}>{TRANSPORT_LABELS[t]}</option>)}
          </select>
        </label>
        {format === "JSON" ? (
          <label className="space-y-1">
            <span className={FIELD_LABEL}>ERP</span>
            <select className={SELECT} value={preset} onChange={(e) => { setPreset(e.target.value as PostingPreset); resetConfig(); }}>
              {catalog.presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </label>
        ) : null}
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Done when</span>
          <select className={SELECT} value={ackMode} onChange={(e) => setAck(e.target.value as AckMode)}>
            {ackModes.map((m) => <option key={m} value={m}>{ACK_LABELS[m]}</option>)}
          </select>
        </label>
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Sign-in</span>
          <select className={SELECT} value={authMode} onChange={(e) => { setAuth(e.target.value as AuthMode); setCredential({}); resetConfig(); }}>
            {authModes.map((m) => <option key={m} value={m}>{AUTH_LABELS[m]}</option>)}
          </select>
        </label>
      </div>
      <p className={HINT}>
        Takes: {objects.map((k) => OBJECT_LABELS[k]).join(", ") || "—"}.
        {presetInfo?.idempotency ? ` Duplicate protection at the ERP: ${presetInfo.idempotency}.` : ""}
        {presetInfo && presetInfo.probe_objects.length > 0 ? " FlowPilot checks the ERP before any resend." : ""}
      </p>
      <label className="block space-y-1">
        <span className={FIELD_LABEL}>Configuration (JSON)</span>
        <textarea className={`${TEXTAREA} min-h-[10rem] font-mono text-xs`} value={configShown} spellCheck={false}
          onChange={(e) => setConfigText(e.target.value)} aria-describedby="new-target-config-hint" />
        <span id="new-target-config-hint" className={HINT}>
          Hosts and URLs only — never a password or token (those go in the credential below). SFTP needs the server&apos;s
          pinned key fingerprint as ssh-keygen -l prints it.
        </span>
      </label>
      <CredentialFields authMode={authMode} value={credential} onChange={setCredential} />
      <details className="space-y-2">
        <summary className="cursor-pointer text-sm font-semibold">Automation and retries</summary>
        <div className="grid gap-3 pt-2 md:grid-cols-3">
          <label className="space-y-1">
            <span className={FIELD_LABEL}>Lookup table prefix</span>
            <input className={INPUT} value={prefix} maxLength={25} onChange={(e) => setPrefix(e.target.value)} placeholder="from the name" />
          </label>
          <label className="space-y-1">
            <span className={FIELD_LABEL}>Attempts before failing</span>
            <input className={INPUT} type="number" min={1} max={10} value={maxAttempts} onChange={(e) => setMaxAttempts(e.target.value)} placeholder="6" />
          </label>
          <label className="space-y-1">
            <span className={FIELD_LABEL}>Wait for acknowledgement (hours)</span>
            <input className={INPUT} type="number" min={1} max={720} value={ackHours} onChange={(e) => setAckHours(e.target.value)} placeholder="72" />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={autoPost} onChange={(e) => setAutoPost(e.target.checked)} />
          Post new approved outcomes automatically (only those approved after this is turned on)
        </label>
        {autoPost ? (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {SOURCE_KINDS.map((k) => (
                <label key={k} className="flex items-center gap-1 text-xs">
                  <input type="checkbox" checked={autoSources.includes(k)} onChange={() => toggle(autoSources, k, setAutoSources)} />
                  {SOURCE_LABELS[k]}
                </label>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              {objects.map((k) => (
                <label key={k} className="flex items-center gap-1 text-xs">
                  <input type="checkbox" checked={autoObjects.includes(k)} onChange={() => toggle(autoObjects, k, setAutoObjects)} />
                  {OBJECT_LABELS[k]}
                </label>
              ))}
            </div>
          </div>
        ) : null}
      </details>
      {localError ? <p className="text-sm text-destructive">{localError}</p> : null}
      {create.isError ? <p className="text-sm text-destructive">{errorMessage(create.error, "Could not create the target.")}</p> : null}
      <div className="flex gap-2">
        <button type="submit" className={BUTTON_PRIMARY} disabled={create.isPending || !name.trim()}>
          {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null} Create target
        </button>
        <button type="button" className={BUTTON_GHOST} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
};

export default NewTarget;
