/**
 * HARDENING-T3:D21 — author clause checks without a marketplace pack.
 *
 * Each clause check is a small rule with a persisted graph (trigger ->
 * assertion -> join) that runs on every processed document. The sentence and
 * threshold are edited with ClauseAssertionBlock (ARCH-33), which previews the
 * compiled check and saves it through the existing definition route. A check
 * cannot be switched on until its clause is saved; failures go to the review
 * queue below.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, ShieldCheck, Trash2 } from "lucide-react";

import { ClauseAssertionBlock } from "@/components/assertions/ClauseAssertionBlock";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";
import {
  createClauseCheck,
  deleteClauseCheck,
  listClauseChecks,
  setClauseCheckActive,
  type ClauseCheck,
} from "@/services/api/clauseChecks";

interface ClauseChecksPanelProps {
  readonly workspaceId: string;
}

export const ClauseChecksPanel: React.FC<ClauseChecksPanelProps> = ({ workspaceId }) => {
  const queryClient = useQueryClient();
  const key = ["clause-checks", workspaceId] as const;
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);

  const checks = useQuery({ queryKey: key, queryFn: () => listClauseChecks(workspaceId), enabled: Boolean(workspaceId) });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: key });

  const create = useMutation({
    mutationFn: () => createClauseCheck(workspaceId, name.trim()),
    onSuccess: (check) => {
      setName("");
      setEditing(check.rule_id);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e, "The clause check could not be created.")),
  });
  const toggle = useMutation({
    mutationFn: (check: ClauseCheck) => setClauseCheckActive(workspaceId, check.rule_id, !check.is_active),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e, "The clause check could not be updated.")),
  });
  const remove = useMutation({
    mutationFn: (check: ClauseCheck) => deleteClauseCheck(workspaceId, check.rule_id),
    onSuccess: () => {
      toast.success("Clause check deleted.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e, "The clause check could not be deleted.")),
  });

  return (
    <section className="rounded-xl border border-border bg-card p-5" aria-labelledby="clause-checks-heading">
      <header className="mb-3">
        <h2 id="clause-checks-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" /> Clause checks
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Write a clause in plain language. Every processed document is checked against it, and documents that fail
          land in the review queue below.
        </p>
      </header>

      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) {
            create.mutate();
          }
        }}
      >
        <input
          aria-label="New clause check name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={120}
          placeholder="e.g. 30-day termination notice"
          className="flex-1 rounded-lg border border-border bg-background px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
        />
        <button
          type="submit"
          disabled={!name.trim() || create.isPending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Plus className="h-4 w-4" aria-hidden="true" />}
          New check
        </button>
      </form>

      {checks.isError ? (
        <ErrorState
          title="Clause checks could not be loaded"
          description={errorMessage(checks.error, "The server did not return the clause checks.")}
          onRetry={() => void checks.refetch()}
        />
      ) : checks.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading clause checks…
        </div>
      ) : (checks.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">No clause checks yet.</p>
      ) : (
        <ul className="space-y-3">
          {(checks.data ?? []).map((check) => (
            <li key={check.rule_id} className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-medium text-foreground">{check.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {check.definition ? `“${check.definition.sentence}”` : "No clause saved yet"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setEditing(editing === check.rule_id ? null : check.rule_id)}
                    className="rounded-lg border border-border px-2.5 py-1 text-xs font-semibold hover:bg-muted"
                  >
                    {editing === check.rule_id ? "Close" : "Edit clause"}
                  </button>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={check.is_active}
                    onClick={() => toggle.mutate(check)}
                    disabled={toggle.isPending || (!check.is_active && !check.definition)}
                    title={!check.definition ? "Save the clause first" : undefined}
                    className={`rounded-full px-2.5 py-1 text-xs font-semibold disabled:opacity-50 ${
                      check.is_active ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {check.is_active ? "On" : "Off"}
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete ${check.name}`}
                    onClick={() => remove.mutate(check)}
                    disabled={remove.isPending}
                    className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive disabled:opacity-50"
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </div>
              {editing === check.rule_id && (
                <div className="mt-3 border-t border-border pt-3">
                  <ClauseAssertionBlock
                    workspaceId={workspaceId}
                    ruleId={check.rule_id}
                    nodeKey={check.node_key}
                    existing={check.definition}
                    onSaved={() => {
                      toast.success("Clause saved. Switch the check on when ready.");
                      refresh();
                    }}
                    onCancel={() => setEditing(null)}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default ClauseChecksPanel;
