/**
 * ARCH45-S2:new-comparison — choose 2 to 5 processed documents (in the order
 * they should appear; the first is the baseline the viewer diffs against),
 * optional rules in plain sentences (ARCH-33's compiler reads them), whether
 * to apply the workspace's saved assertion rules, and the materiality
 * threshold. Submitting returns the cached answer at once when the documents
 * have not changed since the last identical comparison.
 */
import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, GitCompare, Loader2, Search, X } from "lucide-react";

import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, INPUT, SURFACE, TEXTAREA } from "@/components/ui/primitives";
import { corroborationKeys, getWorkspaceRules, requestRun } from "@/services/api/corroboration";
import { errorMessage } from "@/services/api/errors";
import { getWorkItems } from "@/services/api/workItem";
import { MAX_DOCUMENTS, MIN_DOCUMENTS, type RequestResult } from "@/types/corroboration";

interface Chosen {
  readonly id: string;
  readonly label: string;
}

interface NewComparisonProps {
  readonly workspaceId: string;
  readonly initial?: readonly Chosen[];
  readonly onStarted: (result: RequestResult) => void;
  readonly onCancel: () => void;
}

export const NewComparison: React.FC<NewComparisonProps> = ({ workspaceId, initial = [], onStarted, onCancel }) => {
  const [chosen, setChosen] = useState<readonly Chosen[]>(initial);
  const [search, setSearch] = useState("");
  const [rulesText, setRulesText] = useState("");
  const [useWorkspaceRules, setUseWorkspaceRules] = useState(true);
  const [threshold, setThreshold] = useState(0.5);
  const documents = useQuery({
    queryKey: ["corroboration", workspaceId, "picker", search],
    queryFn: () => getWorkItems(workspaceId, { page: 1, pageSize: 25, status: "COMPLETED", ...(search.trim() ? { search } : {}) }),
    enabled: Boolean(workspaceId),
  });
  const rules = useQuery({
    queryKey: corroborationKeys.rules(workspaceId),
    queryFn: () => getWorkspaceRules(workspaceId),
    enabled: Boolean(workspaceId),
  });
  const start = useMutation({
    mutationFn: () => requestRun(workspaceId, {
      work_item_ids: chosen.map((c) => c.id),
      rules: rulesText.split("\n").map((r) => r.trim()).filter(Boolean),
      use_workspace_rules: useWorkspaceRules,
      materiality_threshold: threshold,
    }),
    onSuccess: onStarted,
  });
  const toggle = (id: string, label: string): void => {
    setChosen((current) => (current.some((c) => c.id === id)
      ? current.filter((c) => c.id !== id)
      : current.length >= MAX_DOCUMENTS ? current : [...current, { id, label }]));
  };
  const shift = (index: number, delta: number): void => {
    setChosen((current) => {
      const next = [...current];
      const target = index + delta;
      if (target < 0 || target >= next.length) {
        return current;
      }
      [next[index], next[target]] = [next[target]!, next[index]!];
      return next;
    });
  };
  const ready = chosen.length >= MIN_DOCUMENTS && chosen.length <= MAX_DOCUMENTS;
  return (
    <section className={`${SURFACE} space-y-4 p-4`} aria-labelledby="new-comparison-title">
      <header className="flex items-center gap-2">
        <GitCompare className="h-4 w-4" aria-hidden />
        <h2 id="new-comparison-title" className="text-sm font-semibold">New comparison</h2>
        <button type="button" onClick={onCancel} className="ml-auto rounded p-1 hover:bg-muted" aria-label="Close">
          <X className="h-4 w-4" />
        </button>
      </header>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <label htmlFor="corroboration-search" className="text-xs font-semibold">Processed documents</label>
          <div className="relative">
            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
            <input id="corroboration-search" className={`${INPUT} pl-8`} value={search} placeholder="Search by file name"
              onChange={(event) => setSearch(event.target.value)} />
          </div>
          <ul className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-border/60 p-1" aria-label="Documents to choose from">
            {documents.isLoading ? <li className="p-2"><Loader2 className="h-4 w-4 animate-spin" /></li> : null}
            {(documents.data?.items ?? []).map((item) => {
              const on = chosen.some((c) => c.id === item.id);
              return (
                <li key={item.id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-muted/40">
                    <input type="checkbox" checked={on} disabled={!on && chosen.length >= MAX_DOCUMENTS}
                      onChange={() => toggle(item.id, item.original_filename)} />
                    <span className="truncate">{item.original_filename}</span>
                  </label>
                </li>
              );
            })}
          </ul>
          <p className={HINT}>Choose {MIN_DOCUMENTS} to {MAX_DOCUMENTS}: a contract and its amendment, a PO, invoice and delivery note, a policy and a claim.</p>
        </div>
        <div className="space-y-2">
          <p className="text-xs font-semibold">Order ({chosen.length}/{MAX_DOCUMENTS}) — the first is the baseline</p>
          <ol className="space-y-1">
            {chosen.map((c, index) => (
              <li key={c.id} className="flex items-center gap-2 rounded border border-border/60 px-2 py-1 text-xs">
                <span className="w-4 font-bold">{index + 1}</span>
                <span className="min-w-0 flex-1 truncate">{c.label}</span>
                <button type="button" aria-label={`Move ${c.label} up`} onClick={() => shift(index, -1)} className="rounded p-0.5 hover:bg-muted"><ArrowUp className="h-3.5 w-3.5" /></button>
                <button type="button" aria-label={`Move ${c.label} down`} onClick={() => shift(index, 1)} className="rounded p-0.5 hover:bg-muted"><ArrowDown className="h-3.5 w-3.5" /></button>
                <button type="button" aria-label={`Remove ${c.label}`} onClick={() => toggle(c.id, c.label)} className="rounded p-0.5 hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
              </li>
            ))}
          </ol>
          <label htmlFor="corroboration-rules" className="block pt-2 text-xs font-semibold">Rules every document must meet (optional, one per line)</label>
          <textarea id="corroboration-rules" className={TEXTAREA} rows={3} value={rulesText}
            placeholder={"Payment terms must not exceed 30 days\nGoverning law must be India"}
            onChange={(event) => setRulesText(event.target.value)} />
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={useWorkspaceRules} onChange={(event) => setUseWorkspaceRules(event.target.checked)} />
            Also apply this workspace's clause assertions
            {rules.data ? ` (${rules.data.rules.length}${rules.data.skipped.length ? `, ${rules.data.skipped.length} model-checked skipped` : ""})` : ""}
          </label>
          <label htmlFor="corroboration-threshold" className="block pt-1 text-xs font-semibold">
            Material from {threshold.toFixed(2)}
          </label>
          <input id="corroboration-threshold" type="range" min={0.3} max={0.9} step={0.05} value={threshold}
            onChange={(event) => setThreshold(Number(event.target.value))} className="w-full" />
          <p className={HINT}>At 0.50 every changed value, flipped obligation, missing clause or line and different party is material; rewording is not.</p>
        </div>
      </div>
      {start.isError ? <p className="text-sm text-destructive">{errorMessage(start.error, "The comparison could not be started.")}</p> : null}
      <div className="flex items-center gap-2">
        <button type="button" className={BUTTON_PRIMARY} disabled={!ready || start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompare className="h-4 w-4" />} Compare
        </button>
        <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
};

export default NewComparison;
