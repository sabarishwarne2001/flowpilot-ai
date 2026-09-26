/**
 * ARCH47-S2:lookup-tables — the code tables mappings translate through (vendor
 * name → ERP vendor id, product code / role → GL account, settings). Each
 * target gets its own <prefix>_vendors, _accounts, _items and _settings when
 * it is created. Entries are edited as "key = value" lines; a key with no mapping is
 * an error at render time, never a silent default.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SURFACE, TEXTAREA } from "@/components/ui/primitives";
import { createLookup, deleteLookup, erpKeys, listLookups, updateLookup } from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import type { LookupRow } from "@/types/erp";
import { formatTimestamp } from "@/utils/displayTime";

export const toLines = (entries: Readonly<Record<string, string>>): string =>
  Object.entries(entries).map(([k, v]) => `${k} = ${v}`).join("\n");

export const parseLines = (text: string): { entries: Record<string, string>; problems: string[] } => {
  const entries: Record<string, string> = {};
  const problems: string[] = [];
  text.split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) {
      return;
    }
    const at = line.indexOf("=");
    if (at <= 0) {
      problems.push(`line ${index + 1}: write it as key = value`);
      return;
    }
    const key = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (key in entries) {
      problems.push(`line ${index + 1}: ${key} appears twice`);
    }
    entries[key] = value;
  });
  return { entries, problems };
};

const LookupEditor: React.FC<{ readonly workspaceId: string; readonly row: LookupRow; readonly isAdmin: boolean }> = ({ workspaceId, row, isAdmin }) => {
  const queryClient = useQueryClient();
  const [text, setText] = useState(toLines(row.entries));
  const [description, setDescription] = useState(row.description ?? "");
  const parsed = parseLines(text);
  const refresh = (): void => { void queryClient.invalidateQueries({ queryKey: erpKeys.lookups(workspaceId) }); };
  const save = useMutation({
    mutationFn: () => updateLookup(workspaceId, row.id, { description: description.trim() || null, entries: parsed.entries }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => deleteLookup(workspaceId, row.id), onSuccess: refresh });
  return (
    <article className={`${SURFACE} space-y-2 p-3`}>
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold">{row.name}</h3>
        <span className={HINT}>{row.entry_count} entries · revision {row.revision} · {formatTimestamp(row.updated_at)}</span>
        {row.used_by.length > 0 ? <span className={HINT}>used by {row.used_by.join(", ")}</span> : null}
      </header>
      <input className={INPUT} value={description} disabled={!isAdmin} maxLength={300} aria-label={`${row.name} description`}
        onChange={(e) => setDescription(e.target.value)} placeholder="What the keys and values are" />
      <textarea className={`${TEXTAREA} min-h-[7rem] font-mono text-xs`} value={text} disabled={!isAdmin} spellCheck={false}
        aria-label={`${row.name} entries`} onChange={(e) => setText(e.target.value)} placeholder={"Acme Supplies Ltd = 56\ndefault = 6100"} />
      {parsed.problems.length > 0 ? <p className="text-xs text-destructive">{parsed.problems.join("; ")}</p> : null}
      {save.isError ? <p className="text-xs text-destructive">{errorMessage(save.error, "Could not save.")}</p> : null}
      {remove.isError ? <p className="text-xs text-destructive">{errorMessage(remove.error, "Could not delete.")}</p> : null}
      {isAdmin ? (
        <div className="flex gap-2">
          <button type="button" className={BUTTON_PRIMARY} disabled={save.isPending || parsed.problems.length > 0} onClick={() => save.mutate()}>
            {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null} Save
          </button>
          <button type="button" className={`${BUTTON_DESTRUCTIVE} ml-auto`} disabled={remove.isPending || row.used_by.length > 0}
            title={row.used_by.length > 0 ? "A mapping still uses this table" : undefined}
            onClick={() => { if (window.confirm(`Delete ${row.name}?`)) { remove.mutate(); } }}>
            <Trash2 className="h-4 w-4" aria-hidden /> Delete
          </button>
        </div>
      ) : null}
    </article>
  );
};

export const LookupTables: React.FC<{ readonly workspaceId: string; readonly isAdmin: boolean }> = ({ workspaceId, isAdmin }) => {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: erpKeys.lookups(workspaceId), queryFn: () => listLookups(workspaceId), enabled: Boolean(workspaceId) });
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => createLookup(workspaceId, { name: name.trim(), entries: {} }),
    onSuccess: () => { setName(""); void queryClient.invalidateQueries({ queryKey: erpKeys.lookups(workspaceId) }); },
  });
  if (query.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (query.isError) {
    return <p className="text-sm text-destructive">{errorMessage(query.error, "Could not load lookup tables.")}</p>;
  }
  return (
    <div className="space-y-3">
      <p className={HINT}>
        Mappings translate FlowPilot&apos;s values into your ERP&apos;s codes through these tables: vendors to ERP vendor ids,
        product codes and roles (AP, TAX, CONTRA) to GL accounts, item codes to ERP items or materials. A value missing from a table stops the posting with the
        line and field named — it is never posted to a default account you did not choose.
      </p>
      {isAdmin ? (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <label className="space-y-1">
            <span className={FIELD_LABEL}>New table</span>
            <input className={`${INPUT} w-64 font-mono`} value={name} maxLength={64} onChange={(e) => setName(e.target.value)} placeholder="qbo_departments" />
          </label>
          <button type="submit" className={BUTTON_GHOST} disabled={!name.trim() || create.isPending}>
            <Plus className="h-4 w-4" aria-hidden /> Add
          </button>
          {create.isError ? <span className="text-xs text-destructive">{errorMessage(create.error, "Could not create.")}</span> : null}
        </form>
      ) : null}
      {(query.data?.items ?? []).map((row) => (
        <LookupEditor key={`${row.id}:${row.revision}`} workspaceId={workspaceId} row={row} isAdmin={isAdmin} />
      ))}
    </div>
  );
};

export default LookupTables;
