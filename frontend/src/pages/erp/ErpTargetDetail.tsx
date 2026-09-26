/**
 * ARCH47-S2:page-erp-target — one ERP target: its settings (workspace admins
 * edit them), its credential (set or replace; only the fingerprint is ever
 * shown), "Test connection" (signs in and reads, never posts), a mapping per
 * object it takes (versioned, validated, previewable), and its postings.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, KeyRound, Loader2, PlugZap, Save, Trash2 } from "lucide-react";

import { ErpLocked, canAdminister } from "@/components/erp/common";
import { CredentialFields } from "@/components/erp/CredentialFields";
import { MappingEditor } from "@/components/erp/MappingEditor";
import { PostingTable } from "@/components/erp/PostingTable";
import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, PAGE_TITLE, SECTION_TITLE,
  SELECT, SURFACE, TEXTAREA,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { erpPath } from "@/routes/tenantPaths";
import {
  deleteTarget, erpKeys, getErpCatalog, getTarget, listPostings, setTargetCredential, testTarget, updateTarget,
} from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import {
  ACK_LABELS, AUTH_LABELS, FORMAT_LABELS, OBJECT_LABELS, PRESET_LABELS, SOURCE_KINDS, SOURCE_LABELS, TRANSPORT_LABELS,
  type AckMode, type AuthMode, type ObjectKind, type SourceKind, type TargetDetail, type TestResult,
} from "@/types/erp";
import { formatTimestamp } from "@/utils/displayTime";

const Settings: React.FC<{ readonly workspaceId: string; readonly detail: TargetDetail; readonly ackModes: readonly AckMode[]; readonly authModes: readonly AuthMode[]; readonly isAdmin: boolean }> = ({
  workspaceId, detail, ackModes, authModes, isAdmin,
}) => {
  const queryClient = useQueryClient();
  const t = detail.target;
  const [name, setName] = useState(t.name);
  const [ackMode, setAck] = useState<AckMode>(t.ack_mode);
  const [authMode, setAuth] = useState<AuthMode>(t.auth_mode);
  const [configText, setConfigText] = useState(JSON.stringify(detail.config, null, 2));
  const [autoPost, setAutoPost] = useState(t.auto_post);
  const [sources, setSources] = useState<readonly SourceKind[]>(t.auto_sources);
  const [objects, setObjects] = useState<readonly ObjectKind[]>(t.auto_objects);
  const [maxAttempts, setMaxAttempts] = useState(String(t.max_attempts));
  const [ackHours, setAckHours] = useState(String(t.ack_timeout_hours));
  const [localError, setLocalError] = useState("");
  const save = useMutation({
    mutationFn: (config: Record<string, unknown>) => updateTarget(workspaceId, t.id, {
      name: name.trim(), ack_mode: ackMode, auth_mode: authMode, config, auto_post: autoPost, auto_sources: sources,
      auto_objects: objects, max_attempts: Number(maxAttempts) || null, ack_timeout_hours: Number(ackHours) || null,
    }),
    onSuccess: (data) => {
      queryClient.setQueryData(erpKeys.target(workspaceId, t.id), data);
      void queryClient.invalidateQueries({ queryKey: erpKeys.all(workspaceId) });
    },
  });
  const status = useMutation({
    mutationFn: () => updateTarget(workspaceId, t.id, { status: t.status === "ACTIVE" ? "DISABLED" : "ACTIVE" }),
    onSuccess: (data) => {
      queryClient.setQueryData(erpKeys.target(workspaceId, t.id), data);
      void queryClient.invalidateQueries({ queryKey: erpKeys.all(workspaceId) });
    },
  });
  const submit = (event: React.FormEvent): void => {
    event.preventDefault();
    setLocalError("");
    try {
      const config: unknown = JSON.parse(configText);
      if (!config || typeof config !== "object" || Array.isArray(config)) {
        setLocalError("The configuration is a JSON object.");
        return;
      }
      if (authMode !== t.auth_mode && t.credential_set && !window.confirm("Changing the sign-in method removes the stored credential. Continue?")) {
        return;
      }
      save.mutate(config as Record<string, unknown>);
    } catch {
      setLocalError("The configuration is not valid JSON.");
    }
  };
  const toggle = <T extends string>(list: readonly T[], item: T, setter: (v: readonly T[]) => void): void =>
    setter(list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  return (
    <form className={`${SURFACE} space-y-3 p-4`} onSubmit={submit} aria-labelledby="target-settings">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="target-settings" className={SECTION_TITLE}>Settings</h2>
        <span className={HINT}>
          {t.preset !== "NONE" ? PRESET_LABELS[t.preset] : FORMAT_LABELS[t.format]} · {TRANSPORT_LABELS[t.transport]}
          {detail.lookup_tables.length ? ` · lookup tables ${detail.lookup_tables.join(", ")}` : ""}
        </span>
        {isAdmin ? (
          <button type="button" className={`${t.status === "ACTIVE" ? BUTTON_SECONDARY : BUTTON_PRIMARY} ml-auto`} disabled={status.isPending} onClick={() => status.mutate()}>
            {t.status === "ACTIVE" ? "Disable" : "Enable"}
          </button>
        ) : <span className={`${HINT} ml-auto`}>{t.status === "ACTIVE" ? "Active" : "Disabled"}</span>}
      </div>
      {status.isError ? <p className="text-sm text-destructive">{errorMessage(status.error, "Could not change the status.")}</p> : null}
      <fieldset disabled={!isAdmin} className="space-y-3">
        <div className="grid gap-3 md:grid-cols-3">
          <label className="space-y-1"><span className={FIELD_LABEL}>Name</span>
            <input className={INPUT} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></label>
          <label className="space-y-1"><span className={FIELD_LABEL}>Done when</span>
            <select className={SELECT} value={ackMode} onChange={(e) => setAck(e.target.value as AckMode)}>
              {ackModes.map((m) => <option key={m} value={m}>{ACK_LABELS[m]}</option>)}
            </select></label>
          <label className="space-y-1"><span className={FIELD_LABEL}>Sign-in</span>
            <select className={SELECT} value={authMode} onChange={(e) => setAuth(e.target.value as AuthMode)}>
              {authModes.map((m) => <option key={m} value={m}>{AUTH_LABELS[m]}</option>)}
            </select></label>
          <label className="space-y-1"><span className={FIELD_LABEL}>Attempts before failing</span>
            <input className={INPUT} type="number" min={1} max={10} value={maxAttempts} onChange={(e) => setMaxAttempts(e.target.value)} /></label>
          <label className="space-y-1"><span className={FIELD_LABEL}>Wait for acknowledgement (hours)</span>
            <input className={INPUT} type="number" min={1} max={720} value={ackHours} onChange={(e) => setAckHours(e.target.value)} /></label>
        </div>
        <label className="block space-y-1"><span className={FIELD_LABEL}>Configuration (JSON)</span>
          <textarea className={`${TEXTAREA} min-h-[9rem] font-mono text-xs`} value={configText} spellCheck={false} onChange={(e) => setConfigText(e.target.value)} /></label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={autoPost} onChange={(e) => setAutoPost(e.target.checked)} />
          Post new approved outcomes automatically
          {t.auto_post_since ? <span className={HINT}>(since {formatTimestamp(t.auto_post_since)})</span> : null}
        </label>
        {autoPost ? (
          <div className="flex flex-wrap gap-3">
            {SOURCE_KINDS.map((k) => (
              <label key={k} className="flex items-center gap-1 text-xs">
                <input type="checkbox" checked={sources.includes(k)} onChange={() => toggle(sources, k, setSources)} /> {SOURCE_LABELS[k]}
              </label>
            ))}
            {t.objects.map((k) => (
              <label key={k} className="flex items-center gap-1 text-xs">
                <input type="checkbox" checked={objects.includes(k)} onChange={() => toggle(objects, k, setObjects)} /> {OBJECT_LABELS[k]}
              </label>
            ))}
          </div>
        ) : null}
        {localError ? <p className="text-sm text-destructive">{localError}</p> : null}
        {save.isError ? <p className="text-sm text-destructive">{errorMessage(save.error, "Could not save the target.")}</p> : null}
        {isAdmin ? (
          <button type="submit" className={BUTTON_PRIMARY} disabled={save.isPending}>
            {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Save className="h-4 w-4" aria-hidden />} Save settings
          </button>
        ) : null}
      </fieldset>
    </form>
  );
};

const ErpTargetDetail: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "", targetId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; targetId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.erpPosting);
  const isAdmin = canAdminister(workspace?.role);
  const enabled = Boolean(workspaceId && targetId && capability.granted);
  const query = useQuery({ queryKey: erpKeys.target(workspaceId, targetId), queryFn: () => getTarget(workspaceId, targetId), enabled });
  const catalog = useQuery({ queryKey: erpKeys.catalog(workspaceId), queryFn: () => getErpCatalog(workspaceId), enabled, staleTime: 3_600_000 });
  const postings = useQuery({
    queryKey: erpKeys.postings(workspaceId, { target_id: targetId, limit: 25 }),
    queryFn: () => listPostings(workspaceId, { target_id: targetId, limit: 25 }),
    enabled,
  });
  const [kind, setKind] = useState<ObjectKind | "">("");
  const [credential, setCredential] = useState<Record<string, string>>({});
  const [editingCredential, setEditingCredential] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const saveCredential = useMutation({
    mutationFn: () => setTargetCredential(workspaceId, targetId, { credential }),
    onSuccess: () => {
      setCredential({}); setEditingCredential(false);
      void queryClient.invalidateQueries({ queryKey: erpKeys.target(workspaceId, targetId) });
      void queryClient.invalidateQueries({ queryKey: erpKeys.targets(workspaceId) });
    },
  });
  const probe = useMutation({ mutationFn: () => testTarget(workspaceId, targetId), onSuccess: setTest });
  const remove = useMutation({
    mutationFn: () => deleteTarget(workspaceId, targetId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: erpKeys.all(workspaceId) });
      navigate(erpPath(orgSlug, workspaceSlug));
    },
  });

  if (!capability.granted) {
    return <ErpLocked />;
  }
  if (query.isLoading) {
    return <div className="p-4"><Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /></div>;
  }
  if (query.isError || !query.data) {
    return <p className="p-4 text-sm text-destructive">{errorMessage(query.error, "Could not load the target.")}</p>;
  }
  const detail = query.data;
  const t = detail.target;
  const activeKind: ObjectKind | undefined = (kind || t.objects[0]) as ObjectKind | undefined;
  const presetInfo = catalog.data?.presets.find((p) => p.key === t.preset);
  const ackModes = (catalog.data?.ack_modes[t.transport] ?? [t.ack_mode]).filter((m) => m !== "X12_997" || t.format === "X12");
  const authModes = (catalog.data?.auth_modes[t.transport] ?? [t.auth_mode]).filter((m) => !presetInfo || presetInfo.auth_modes.includes(m));

  return (
    <div className="space-y-4 p-4">
      <Link to={erpPath(orgSlug, workspaceSlug)} className={BUTTON_GHOST}><ArrowLeft className="h-4 w-4" aria-hidden /> ERP posting</Link>
      <header className="flex flex-wrap items-center gap-2">
        <h1 className={PAGE_TITLE}>{t.name}</h1>
        {t.host ? <span className={HINT}>{t.host}</span> : null}
        <span className={HINT}>{t.postings} posting(s){t.open_exceptions ? ` · ${t.open_exceptions} need a decision` : ""}</span>
        {isAdmin ? (
          <div className="ml-auto flex gap-2">
            <button type="button" className={BUTTON_SECONDARY} disabled={probe.isPending} onClick={() => probe.mutate()}>
              {probe.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <PlugZap className="h-4 w-4" aria-hidden />} Test connection
            </button>
            <button type="button" className={BUTTON_DESTRUCTIVE} disabled={remove.isPending || t.postings > 0}
              title={t.postings > 0 ? "A target with postings is part of the ledger: disable it instead" : undefined}
              onClick={() => { if (window.confirm(`Delete ${t.name}?`)) { remove.mutate(); } }}>
              <Trash2 className="h-4 w-4" aria-hidden /> Delete
            </button>
          </div>
        ) : null}
      </header>
      {test ? (
        <p className={`rounded-lg border p-2 text-sm ${test.ok ? "border-emerald-500/40 bg-emerald-500/10" : "border-destructive/40 bg-destructive/10"}`}>
          {test.ok ? "Connected." : "Not connected."} {test.message}
        </p>
      ) : null}
      {probe.isError ? <p className="text-sm text-destructive">{errorMessage(probe.error, "The test could not run.")}</p> : null}
      {remove.isError ? <p className="text-sm text-destructive">{errorMessage(remove.error, "Could not delete.")}</p> : null}

      <Settings key={`${t.id}:${t.updated_at}`} workspaceId={workspaceId} detail={detail} ackModes={ackModes} authModes={authModes} isAdmin={isAdmin} />

      {t.auth_mode !== "NONE" ? (
        <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="target-credential">
          <div className="flex flex-wrap items-center gap-2">
            <KeyRound className="h-4 w-4" aria-hidden />
            <h2 id="target-credential" className={SECTION_TITLE}>Credential</h2>
            <span className={HINT}>
              {AUTH_LABELS[t.auth_mode]} ·{" "}
              {t.credential_set ? `fingerprint ${t.credential_fingerprint ?? "—"} · set ${formatTimestamp(t.credential_updated_at)}` : "not set"}
            </span>
            {isAdmin && !editingCredential ? (
              <button type="button" className={`${BUTTON_GHOST} ml-auto`} onClick={() => setEditingCredential(true)}>
                {t.credential_set ? "Replace" : "Set credential"}
              </button>
            ) : null}
          </div>
          {editingCredential ? (
            <div className="space-y-2">
              <CredentialFields authMode={t.auth_mode} value={credential} onChange={setCredential} />
              {saveCredential.isError ? <p className="text-sm text-destructive">{errorMessage(saveCredential.error, "Could not store the credential.")}</p> : null}
              <div className="flex gap-2">
                <button type="button" className={BUTTON_PRIMARY} disabled={saveCredential.isPending} onClick={() => saveCredential.mutate()}>Store encrypted</button>
                <button type="button" className={BUTTON_GHOST} onClick={() => { setCredential({}); setEditingCredential(false); }}>Cancel</button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="target-mappings">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="target-mappings" className={SECTION_TITLE}>Mappings</h2>
          <div className="flex flex-wrap gap-1" role="tablist" aria-label="Object">
            {t.objects.map((k) => (
              <button key={k} type="button" role="tab" aria-selected={activeKind === k} onClick={() => setKind(k)}
                className={`rounded-md px-2 py-1 text-xs font-semibold ${activeKind === k ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"}`}>
                {OBJECT_LABELS[k]}
              </button>
            ))}
          </div>
        </div>
        {activeKind ? (
          <MappingEditor key={activeKind} workspaceId={workspaceId} targetId={t.id} kind={activeKind} catalog={catalog.data} isAdmin={isAdmin} />
        ) : <p className={HINT}>This target takes no objects.</p>}
      </section>

      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="target-postings">
        <h2 id="target-postings" className={SECTION_TITLE}>Recent postings</h2>
        {postings.isError ? <p className="text-sm text-destructive">{errorMessage(postings.error, "Could not load postings.")}</p> : null}
        {postings.data ? <PostingTable rows={postings.data.items} showTarget={false} /> : null}
      </section>
    </div>
  );
};

export default ErpTargetDetail;
