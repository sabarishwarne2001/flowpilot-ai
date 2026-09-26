/**
 * ARCH47-S2:mapping-editor — one target's mapping for one object kind, in the
 * fp-map/1 language: a JSON document of field paths and a closed list of
 * transforms. It is data, never code — the server refuses anything that looks
 * like a template or an expression — and every save makes a new version (the
 * old one is kept, and postings record the version they were rendered with).
 * "Check" validates without saving; "Preview" renders an approved outcome with
 * the draft, without posting.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Eye, Loader2, RotateCcw, Save } from "lucide-react";

import { PreviewPanel } from "@/components/erp/PreviewPanel";
import { BUTTON_GHOST, BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SELECT, TEXTAREA } from "@/components/ui/primitives";
import {
  erpKeys, getMappings, listOutcomes, previewPosting, restoreDefaultMapping, saveMapping, validateMapping,
} from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import type { ErpCatalog, MappingCheck, MappingVersions, ObjectKind, PreviewResponse } from "@/types/erp";
import { formatTimestamp } from "@/utils/displayTime";

interface MappingEditorProps {
  readonly workspaceId: string;
  readonly targetId: string;
  readonly kind: ObjectKind;
  readonly catalog: ErpCatalog | undefined;
  readonly isAdmin: boolean;
}

const pretty = (value: unknown): string => JSON.stringify(value, null, 2);

const Draft: React.FC<MappingEditorProps & { readonly versions: MappingVersions }> = ({
  workspaceId, targetId, kind, catalog, isAdmin, versions,
}) => {
  const queryClient = useQueryClient();
  const [text, setText] = useState(pretty(versions.active?.spec ?? versions.default_spec));
  const [note, setNote] = useState("");
  const [check, setCheck] = useState<MappingCheck | null>(null);
  const [parseError, setParseError] = useState("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [outcome, setOutcome] = useState("");
  const outcomes = useQuery({ queryKey: erpKeys.outcomes(workspaceId), queryFn: () => listOutcomes(workspaceId), enabled: isAdmin });
  const candidates = (outcomes.data?.items ?? []).filter((o) => o.objects.includes(kind));
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: erpKeys.mappings(workspaceId, targetId, kind) });
    void queryClient.invalidateQueries({ queryKey: erpKeys.target(workspaceId, targetId) });
  };
  const spec = (): Record<string, unknown> | null => {
    setParseError("");
    try {
      const value: unknown = JSON.parse(text);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        return value as Record<string, unknown>;
      }
      setParseError("A mapping is a JSON object.");
    } catch (err) {
      setParseError(`Not valid JSON: ${err instanceof Error ? err.message : "parse error"}`);
    }
    return null;
  };
  const validate = useMutation({
    mutationFn: (body: Record<string, unknown>) => validateMapping(workspaceId, targetId, kind, { spec: body }),
    onSuccess: setCheck,
  });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => saveMapping(workspaceId, targetId, kind, { spec: body, note: note.trim() || null }),
    onSuccess: () => { setNote(""); setCheck(null); refresh(); },
  });
  const restore = useMutation({
    mutationFn: () => restoreDefaultMapping(workspaceId, targetId, kind),
    onSuccess: (row) => { setText(pretty(row.spec)); setCheck(null); refresh(); },
  });
  const look = useMutation({
    mutationFn: (body: Record<string, unknown>) => {
      const chosen = candidates.find((o) => `${o.kind}:${o.id}` === outcome) ?? candidates[0];
      if (!chosen) {
        throw new Error("no outcome");
      }
      return previewPosting(workspaceId, { target_id: targetId, source_kind: chosen.kind, source_id: chosen.id, object_kind: kind, spec: body });
    },
    onSuccess: setPreview,
  });
  const run = (fn: (body: Record<string, unknown>) => void): void => {
    const body = spec();
    if (body) {
      fn(body);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {versions.active ? (
          <span className={HINT}>
            Active: version {versions.active.version} · {formatTimestamp(versions.active.created_at)}
            {versions.active.note ? ` · “${versions.active.note}”` : ""} · <span className="font-mono">{versions.active.spec_sha.slice(0, 12)}</span>
          </span>
        ) : <span className={HINT}>No active mapping: this object cannot be posted to this target yet.</span>}
        {catalog ? <span className={HINT}>Language {catalog.language}</span> : null}
      </div>
      <textarea className={`${TEXTAREA} min-h-[18rem] font-mono text-xs`} value={text} readOnly={!isAdmin} spellCheck={false}
        aria-label={`Mapping for ${kind}`} onChange={(e) => { setText(e.target.value); setCheck(null); }} />
      {parseError ? <p className="text-sm text-destructive">{parseError}</p> : null}
      {check ? (
        check.valid ? (
          <p className="flex items-center gap-1 text-sm text-emerald-700 dark:text-emerald-400"><CheckCircle2 className="h-4 w-4" aria-hidden /> Valid.</p>
        ) : (
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-destructive">{check.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        )
      ) : null}
      {[validate, save, restore, look].map((m, i) => (m.isError ? (
        <p key={i} className="text-sm text-destructive">{errorMessage(m.error, "That did not work.")}</p>
      ) : null))}
      {isAdmin ? (
        <div className="flex flex-wrap items-end gap-2">
          <button type="button" className={BUTTON_GHOST} disabled={validate.isPending} onClick={() => run((b) => validate.mutate(b))}>
            {validate.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <CheckCircle2 className="h-4 w-4" aria-hidden />} Check
          </button>
          <label className="space-y-1">
            <span className={FIELD_LABEL}>Note for this version</span>
            <input className={`${INPUT} w-64`} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="Map freight to 6150" />
          </label>
          <button type="button" className={BUTTON_PRIMARY} disabled={save.isPending} onClick={() => run((b) => save.mutate(b))}>
            {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Save className="h-4 w-4" aria-hidden />} Save as new version
          </button>
          <button type="button" className={BUTTON_GHOST} disabled={restore.isPending}
            onClick={() => { if (window.confirm("Replace the active mapping with the default for this format?")) { restore.mutate(); } }}>
            <RotateCcw className="h-4 w-4" aria-hidden /> Restore default
          </button>
          {candidates.length > 0 ? (
            <>
              <select className={`${SELECT} w-64`} aria-label="Outcome to preview" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
                {candidates.map((o) => <option key={`${o.kind}:${o.id}`} value={`${o.kind}:${o.id}`}>{o.label}</option>)}
              </select>
              <button type="button" className={BUTTON_GHOST} disabled={look.isPending} onClick={() => run((b) => look.mutate(b))}>
                {look.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />} Preview draft
              </button>
            </>
          ) : <span className={HINT}>No approved outcome yields this object yet, so there is nothing to preview.</span>}
        </div>
      ) : null}
      {preview ? <PreviewPanel preview={preview} /> : null}
      {versions.versions.length > 1 ? (
        <details>
          <summary className="cursor-pointer text-sm font-semibold">Earlier versions ({versions.versions.length - 1})</summary>
          <ul className="mt-2 space-y-1 text-xs">
            {versions.versions.filter((m) => m.status !== "ACTIVE").map((m) => (
              <li key={m.id} className="flex flex-wrap items-center gap-2">
                <span>v{m.version} · {formatTimestamp(m.created_at)}{m.note ? ` · “${m.note}”` : ""}</span>
                {isAdmin ? (
                  <button type="button" className="text-primary hover:underline" onClick={() => { setText(pretty(m.spec)); setCheck(null); }}>
                    Load into the editor
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {catalog ? (
        <details>
          <summary className="cursor-pointer text-sm font-semibold">Fields and transforms you can use</summary>
          <div className="mt-2 grid gap-3 text-xs md:grid-cols-3">
            <div>
              <p className="font-semibold">Header</p>
              <ul className="font-mono">{Object.entries(catalog.header_paths).map(([k, d]) => <li key={k} title={d}>{k}</li>)}</ul>
            </div>
            <div>
              <p className="font-semibold">Each line</p>
              <ul className="font-mono">{Object.entries(catalog.line_paths).map(([k, d]) => <li key={k} title={d}>{k}</li>)}</ul>
            </div>
            <div>
              <p className="font-semibold">Transforms</p>
              <ul className="font-mono">{catalog.transforms.map((t) => <li key={t}>{t}</li>)}</ul>
            </div>
          </div>
        </details>
      ) : null}
    </div>
  );
};

export const MappingEditor: React.FC<MappingEditorProps> = (props) => {
  const { workspaceId, targetId, kind } = props;
  const query = useQuery({
    queryKey: erpKeys.mappings(workspaceId, targetId, kind),
    queryFn: () => getMappings(workspaceId, targetId, kind),
    enabled: Boolean(workspaceId && targetId),
  });
  if (query.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (query.isError || !query.data) {
    return <p className="text-sm text-destructive">{errorMessage(query.error, "Could not load the mapping.")}</p>;
  }
  return <Draft key={query.data.active?.id ?? "none"} {...props} versions={query.data} />;
};

export default MappingEditor;
