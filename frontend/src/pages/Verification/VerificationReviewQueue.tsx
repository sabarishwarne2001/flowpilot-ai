import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Keyboard, Loader2, Pencil } from "lucide-react";

import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import {
  getVerification,
  listVerifications,
  resolveVerification,
} from "@/services/api/verification";
import { verificationKeys } from "@/services/api/queryKeys";
import {
  formatFieldValue,
  parseScore,
} from "@/types/verification";
import type {
  VerificationFieldResponse,
  VerificationSummaryResponse,
} from "@/types/verification";
import { formatMicros } from "@/types/billing";

export const VerificationReviewQueue: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const queryClient = useQueryClient();

  const [cursor, setCursor] = useState(0);
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});

  const listRef = useRef<HTMLUListElement>(null);

  const listQuery = useQuery({
    queryKey: verificationKeys.list(workspaceId, "DISAGREED"),
    queryFn: () =>
      listVerifications(workspaceId, { status: "DISAGREED", limit: 100 }),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
  });

  const items = useMemo<VerificationSummaryResponse[]>(
    () => listQuery.data ?? [],
    [listQuery.data],
  );

  const active = items[cursor] ?? null;

  const detailQuery = useQuery({
    queryKey: verificationKeys.detail(workspaceId, active?.id ?? ""),
    queryFn: () => getVerification(workspaceId, active?.id as string),
    enabled: Boolean(workspaceId && active?.id),
    staleTime: 30_000,
  });

  const detail = detailQuery.data ?? null;

  const resolve = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      resolveVerification(workspaceId, active?.id as string, { values }),
    onSuccess: async () => {
      setEditing(false);
      setEdits({});
      await queryClient.invalidateQueries({
        queryKey: verificationKeys.all(workspaceId),
      });
      setCursor((current) => current);
    },
  });

  const acceptConsensus = useCallback(() => {
    if (!detail || resolve.isPending) {
      return;
    }
    const values: Record<string, unknown> = {};
    detail.fields.forEach((field) => {
      if (!field.agreed) {
        values[field.field_path] = field.consensus_value;
      }
    });
    resolve.mutate(values);
  }, [detail, resolve]);

  const submitEdits = useCallback(() => {
    if (!detail || resolve.isPending) {
      return;
    }
    const values: Record<string, unknown> = {};
    detail.fields.forEach((field) => {
      if (field.agreed) {
        return;
      }
      values[field.field_path] =
        field.field_path in edits
          ? edits[field.field_path]
          : field.consensus_value;
    });
    resolve.mutate(values);
  }, [detail, edits, resolve]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      if (typing) {
        if (event.key === "Escape") {
          (target as HTMLElement).blur();
        }
        return;
      }

      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      switch (event.key) {
        case "j":
          event.preventDefault();
          setCursor((c) => Math.min(c + 1, Math.max(items.length - 1, 0)));
          setEditing(false);
          setEdits({});
          break;
        case "k":
          event.preventDefault();
          setCursor((c) => Math.max(c - 1, 0));
          setEditing(false);
          setEdits({});
          break;
        case "a":
          event.preventDefault();
          acceptConsensus();
          break;
        case "e":
          event.preventDefault();
          setEditing(true);
          break;
        default:
          break;
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [items.length, acceptConsensus]);

  useEffect(() => {
    if (cursor > 0 && cursor >= items.length) {
      setCursor(Math.max(items.length - 1, 0));
    }
  }, [items.length, cursor]);

  useEffect(() => {
    const node = listRef.current?.children[cursor] as HTMLElement | undefined;
    node?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (listQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading review queue…
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="p-6">
        <p className="text-sm font-medium">Nothing to review</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Every extraction the agents disagreed on has been resolved.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-72 shrink-0 flex-col border-r border-border">
        <div className="border-b border-border px-3 py-2">
          <h2 className="text-sm font-medium">
            Review queue
            <span className="ml-1.5 text-xs text-muted-foreground">
              {items.length}
            </span>
          </h2>
        </div>

        <ul ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {items.map((item, index) => {
            const score = parseScore(item.agreement_score);
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    setCursor(index);
                    setEditing(false);
                    setEdits({});
                  }}
                  aria-current={index === cursor ? "true" : undefined}
                  className={[
                    "w-full border-l-2 px-3 py-2 text-left",
                    index === cursor
                      ? "border-primary bg-primary/5"
                      : "border-transparent hover:bg-muted/50",
                  ].join(" ")}
                >
                  <span className="block truncate text-sm">
                    {item.work_item_id.slice(0, 8)}
                  </span>
                  <span className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                    {score !== null && <>{Math.round(score * 100)}% agreement</>}
                    <span>· {item.agent_count} agents</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="border-t border-border px-3 py-2">
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Keyboard className="h-3 w-3" aria-hidden="true" />
            <kbd className="font-mono">j</kbd>/<kbd className="font-mono">k</kbd>{" "}
            move · <kbd className="font-mono">a</kbd> accept ·{" "}
            <kbd className="font-mono">e</kbd> edit
          </p>
        </div>
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto p-4">
        {detailQuery.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading…
          </div>
        ) : !detail ? (
          <p className="text-sm text-muted-foreground">
            Select a document to review.
          </p>
        ) : (
          <>
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
              <div>
                <h2 className="text-sm font-medium">
                  Document {detail.work_item_id.slice(0, 8)}
                </h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {detail.agent_count} agents ·{" "}
                  {parseScore(detail.agreement_score) !== null &&
                    `${Math.round((parseScore(detail.agreement_score) ?? 0) * 100)}% agreement · `}
                  {formatMicros(detail.cost_micros)} to extract
                </p>
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={acceptConsensus}
                  disabled={resolve.isPending}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
                >
                  {resolve.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Check className="h-3.5 w-3.5" />
                  )}
                  Accept all
                </button>

                <button
                  type="button"
                  onClick={() => setEditing((current) => !current)}
                  aria-pressed={editing}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
                >
                  <Pencil className="h-3.5 w-3.5" />
                  {editing ? "Stop editing" : "Edit"}
                </button>
              </div>
            </header>

            <ul className="mt-3 space-y-3">
              {detail.fields
                .filter((field) => !field.agreed)
                .map((field) => (
                  <FieldDiff
                    key={field.field_path}
                    field={field}
                    editing={editing}
                    value={edits[field.field_path]}
                    onChange={(value) =>
                      setEdits((current) => ({
                        ...current,
                        [field.field_path]: value,
                      }))
                    }
                  />
                ))}
            </ul>

            {detail.fields.filter((f) => !f.agreed).length === 0 && (
              <p className="mt-3 text-sm text-muted-foreground">
                Every field agreed. Nothing needs a decision here.
              </p>
            )}

            {editing && (
              <div className="mt-4 flex justify-end border-t border-border pt-3">
                <button
                  type="button"
                  onClick={submitEdits}
                  disabled={resolve.isPending}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
                >
                  {resolve.isPending && (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  )}
                  Submit corrections
                </button>
              </div>
            )}

            {resolve.isError && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                That didn&apos;t save. Nothing was recorded.
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
};

const DISAGREEMENT_COPY: Record<string, string> = {
  MISSING: "At least one agent found nothing here — often a scan-quality issue.",
  CONFLICT: "The agents read different values. This one needs a decision.",
  FORMAT: "Same value, different formatting. Usually safe to accept.",
};

interface FieldDiffProps {
  readonly field: VerificationFieldResponse;
  readonly editing: boolean;
  readonly value: string | undefined;
  readonly onChange: (value: string) => void;
}

const FieldDiff: React.FC<FieldDiffProps> = ({
  field,
  editing,
  value,
  onChange,
}) => {
  const confidence = parseScore(field.confidence);

  return (
    <li className="rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-xs font-medium">
          {field.field_path}
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {field.disagreement_kind && (
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-800">
              {field.disagreement_kind}
            </span>
          )}
          {confidence !== null && <>{Math.round(confidence * 100)}% confident</>}
        </span>
      </div>

      {field.disagreement_kind &&
        DISAGREEMENT_COPY[field.disagreement_kind] && (
          <p className="mt-1 text-xs text-muted-foreground">
            {DISAGREEMENT_COPY[field.disagreement_kind]}
          </p>
        )}

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {field.agent_values.map((agentValue, index) => (
          <div
            key={index}
            className="rounded border border-border bg-background p-2"
          >
            <p className="text-[11px] text-muted-foreground">
              Agent {index + 1}
            </p>
            <p className="mt-0.5 break-words font-mono text-xs">
              {formatFieldValue(agentValue)}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-2">
        <p className="text-[11px] text-muted-foreground">
          {editing ? "Your value" : "Proposed"}
        </p>
        {editing ? (
          <input
            value={value ?? formatFieldValue(field.consensus_value)}
            onChange={(e) => onChange(e.target.value)}
            aria-label={`Value for ${field.field_path}`}
            className="mt-0.5 w-full rounded border border-primary/50 bg-background px-2 py-1.5 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        ) : (
          <p className="mt-0.5 break-words rounded bg-muted/50 px-2 py-1.5 font-mono text-xs">
            {formatFieldValue(field.consensus_value)}
          </p>
        )}
      </div>
    </li>
  );
};

export default VerificationReviewQueue;
