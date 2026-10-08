import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCircle2, Keyboard, Loader2, Pencil } from "lucide-react";

import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import {
  getVerification,
  listVerifications,
  resolveVerification,
} from "@/services/api/verification";
import { verificationKeys } from "@/services/api/queryKeys";
import { DocumentEvidence } from "@/components/review/DocumentEvidence";
import {
  documentLabel,
  formatFieldValue,
  parseScore,
} from "@/types/verification";
import type {
  VerificationFieldResponse,
  VerificationSummaryResponse,
} from "@/types/verification";
import { formatMicros } from "@/types/billing";
import { diffSegments } from "@/utils/textDiff";

/**
 * ARCH40-S2:workbench-focus. The field-level extraction workbench.
 *
 * Standalone it lists DISAGREED verifications, as it always has. With
 * `focusVerificationId` it opens exactly one verification — any status the
 * review hub can show, including calibration holds and rule escalations that
 * are PENDING rather than DISAGREED — and calls `onResolved` when a reviewer
 * clears it. The unified review hub mounts it that way for EXTRACTION items,
 * so there is one field-diff UI in the product, not two.
 */
export interface VerificationReviewQueueProps {
  readonly focusVerificationId?: string;
  readonly onResolved?: () => void;
}

export const VerificationReviewQueue: React.FC<VerificationReviewQueueProps> = ({
  focusVerificationId,
  onResolved,
}) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const queryClient = useQueryClient();

  const [cursor, setCursor] = useState(0);
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  // PHASE 4: which field the page evidence shows (null = the first under review).
  const [evidenceField, setEvidenceField] = useState<string | null>(null);

  const listRef = useRef<HTMLUListElement>(null);

  const listQuery = useQuery({
    queryKey: verificationKeys.list(workspaceId, "DISAGREED"),
    queryFn: () =>
      listVerifications(workspaceId, { status: "DISAGREED", limit: 100 }),
    enabled: Boolean(workspaceId) && !focusVerificationId,
    staleTime: 15_000,
  });

  const listed = useMemo<VerificationSummaryResponse[]>(
    () => listQuery.data ?? [],
    [listQuery.data],
  );

  const activeId = focusVerificationId ?? listed[cursor]?.id ?? "";
  useEffect(() => setEvidenceField(null), [activeId]);

  const detailQuery = useQuery({
    queryKey: verificationKeys.detail(workspaceId, activeId),
    queryFn: () => getVerification(workspaceId, activeId),
    enabled: Boolean(workspaceId && activeId),
    staleTime: 30_000,
  });

  const detail = detailQuery.data ?? null;

  const items = useMemo<VerificationSummaryResponse[]>(
    () => (focusVerificationId ? (detail ? [detail] : []) : listed),
    [focusVerificationId, detail, listed],
  );

  const active = items[cursor] ?? null;

  // ARCH35-S3:review-all-fields. A verification calibrated autonomy held back
  // asks about EVERY extracted field, and an audit sample was one the platform
  // would have approved on its own. Assertion fields are never answered here:
  // they are resolved in the clause review queue, and the server refuses a
  // value for them on this endpoint.
  const calibrationDetails = (detail?.details?.["calibration"] ?? null) as {
    readonly review_all_fields?: boolean;
    readonly audit_sample?: boolean;
  } | null;
  // ARCH40-S2:escalation-review-all. ARCH-37's `review.escalate` marks
  // `escalation.review_all_fields`, which the backend's resolve honours. Every
  // escalated field is stored agreed, so without this the workbench showed
  // an escalated document with nothing to review.
  const escalationDetails = (detail?.details?.["escalation"] ?? null) as {
    readonly review_all_fields?: boolean;
  } | null;
  // ARCH41-S3:memory-review-all. An extraction-memory hold (trial or
  // recalibration) asks for every field; the backend's resolve honours it.
  const memoryDetails = (detail?.details?.["extraction_memory"] ?? null) as {
    readonly review_all_fields?: boolean;
  } | null;
  const reviewAll =
    Boolean(calibrationDetails?.review_all_fields) ||
    Boolean(escalationDetails?.review_all_fields) ||
    Boolean(memoryDetails?.review_all_fields);
  const isAudit = Boolean(calibrationDetails?.audit_sample);
  const isReviewable = useCallback(
    (field: VerificationFieldResponse): boolean =>
      !field.field_path.startsWith("assertion:") && (reviewAll || !field.agreed),
    [reviewAll],
  );

  const resolve = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      resolveVerification(workspaceId, active?.id as string, { values }),
    onSuccess: async () => {
      setEditing(false);
      setEdits({});
      onResolved?.();
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
      if (isReviewable(field)) {
        values[field.field_path] = field.consensus_value;
      }
    });
    resolve.mutate(values);
  }, [detail, resolve, isReviewable]);

  const submitEdits = useCallback(() => {
    if (!detail || resolve.isPending) {
      return;
    }
    const values: Record<string, unknown> = {};
    detail.fields.forEach((field) => {
      if (!isReviewable(field)) {
        return;
      }
      values[field.field_path] =
        field.field_path in edits
          ? edits[field.field_path]
          : field.consensus_value;
    });
    resolve.mutate(values);
  }, [detail, edits, resolve, isReviewable]);

  useEffect(() => {
    // F-178: mounted in the review hub the hub owns the keyboard. Its "a" is "assign to me" and its
    // "e" collapses the item; heard here as well, the same key accepted every value or started
    // editing. Standalone, the workbench keeps its own shortcuts.
    if (focusVerificationId) {
      return undefined;
    }
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
  }, [items.length, acceptConsensus, focusVerificationId]);

  useEffect(() => {
    if (cursor > 0 && cursor >= items.length) {
      setCursor(Math.max(items.length - 1, 0));
    }
  }, [items.length, cursor]);

  useEffect(() => {
    const node = listRef.current?.children[cursor] as HTMLElement | undefined;
    node?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (focusVerificationId ? detailQuery.isLoading : listQuery.isLoading) {
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
      {/* In the hub (focus mode) the item is already chosen: no one-item list beside it. */}
      {focusVerificationId ? null : (
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
                  <span className="block truncate text-sm" title={documentLabel(item)}>
                    {documentLabel(item)}
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
      )}

      <section
        className={`min-w-0 flex-1 overflow-y-auto ${focusVerificationId ? "" : "p-4"}`}
        aria-label="Extraction workbench"
      >
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
                <h2 className="break-words text-sm font-medium">
                  {documentLabel(detail)}
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
                  className="fp-btn-primary inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
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

            {reviewAll ? (
              <p className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                {isAudit
                  ? "Accuracy audit: this document would have been approved automatically. Confirm or correct every field — audits are how the error limit stays checkable."
                  : "Held for review by calibrated autonomy. Confirm or correct every field, including the ones the agents agreed on."}
              </p>
            ) : null}

            {detail.fields.filter(isReviewable).length > 0 && (
              <div className="mt-3">
                <DocumentEvidence
                  workspaceId={workspaceId}
                  workItemId={detail.work_item_id}
                  fields={detail.fields.filter(isReviewable)}
                  fieldPath={evidenceField}
                  onSelectField={setEvidenceField}
                />
              </div>
            )}

            {detail.fields.filter(isReviewable).length > 0 && (
              <FieldTable
                fields={detail.fields.filter(isReviewable)}
                activeField={evidenceField}
                onSelect={setEvidenceField}
                editing={editing}
                edits={edits}
                onChange={(fieldPath, value) =>
                  setEdits((current) => ({ ...current, [fieldPath]: value }))
                }
              />
            )}

            {detail.fields.filter(isReviewable).length === 0 && (
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
                  className="fp-btn-primary inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
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

const DISAGREEMENT: Record<string, { label: string; copy: string; tone: string }> = {
  MISSING: {
    label: "Not found by all",
    copy: "At least one agent found nothing here — often a scan-quality issue.",
    tone: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  },
  CONFLICT: {
    label: "Different values",
    copy: "The agents read different values. This one needs a decision.",
    tone: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  },
  FORMAT: {
    label: "Formatting only",
    copy: "Same value, different formatting. Usually safe to accept.",
    tone: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  },
};

interface FieldTableProps {
  readonly fields: readonly VerificationFieldResponse[];
  readonly activeField: string | null;
  readonly onSelect: (fieldPath: string) => void;
  readonly editing: boolean;
  readonly edits: Readonly<Record<string, string>>;
  readonly onChange: (fieldPath: string, value: string) => void;
}

/**
 * Phase 2 — the fields to decide as one comparison grid: a row per field, a column per agent and
 * the value proposed. In each agent's reading the characters that differ from the proposal are
 * highlighted, so "GB94 BARC 1020" against "GB94 BARC 1O20" shows the one letter at a glance; an
 * agent that found nothing says so in words.
 */
const DECISION_ORDER: Readonly<Record<string, number>> = { CONFLICT: 0, MISSING: 1, FORMAT: 2 };

const FieldTable: React.FC<FieldTableProps> = ({ fields: given, activeField, onSelect, editing, edits, onChange }) => {
  const agents = Math.max(1, ...given.map((field) => field.agent_values.length));
  // What needs a decision first: different values, then missing ones, then formatting, then the
  // least confident; the rest keep the server's order.
  const fields = given
    .map((field, index) => ({ field, index }))
    .sort(
      (a, b) =>
        (DECISION_ORDER[a.field.disagreement_kind ?? ""] ?? 3) - (DECISION_ORDER[b.field.disagreement_kind ?? ""] ?? 3) ||
        (parseScore(a.field.confidence) ?? 1) - (parseScore(b.field.confidence) ?? 1) ||
        a.index - b.index,
    )
    .map(({ field }) => field);
  return (
    <div className="mt-3">
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-xs font-semibold text-foreground">
          {fields.length} field{fields.length === 1 ? "" : "s"} to decide
        </h3>
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <mark className="rounded-sm bg-red-500/20 px-1 font-mono text-red-700 dark:text-red-300">abc</mark>
          differs from the proposed value · click a field to see it on the page
        </p>
      </div>
      <div className="overflow-x-auto overscroll-x-contain rounded-lg border border-border" role="region" aria-label="Field comparison">
        <table className="w-full min-w-[36rem] border-collapse text-xs">
          <thead className="bg-muted/40 text-left text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2">Field</th>
              {Array.from({ length: agents }, (_, index) => (
                <th key={index} scope="col" className="px-3 py-2">Agent {index + 1}</th>
              ))}
              <th scope="col" className="px-3 py-2">{editing ? "Your value" : "Proposed"}</th>
              <th scope="col" className="w-24 px-3 py-2 text-right">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <FieldRow
                key={field.field_path}
                field={field}
                agents={agents}
                active={activeField === field.field_path}
                onSelect={() => onSelect(field.field_path)}
                editing={editing}
                value={edits[field.field_path]}
                onChange={(value) => onChange(field.field_path, value)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

interface FieldRowProps {
  readonly field: VerificationFieldResponse;
  readonly agents: number;
  readonly active: boolean;
  readonly onSelect: () => void;
  readonly editing: boolean;
  readonly value: string | undefined;
  readonly onChange: (value: string) => void;
}

const FieldRow: React.FC<FieldRowProps> = ({ field, agents, active, onSelect, editing, value, onChange }) => {
  const confidence = parseScore(field.confidence);
  const proposed = field.consensus_value === null || field.consensus_value === undefined ? null : formatFieldValue(field.consensus_value);
  const kind = field.disagreement_kind ? DISAGREEMENT[field.disagreement_kind] : undefined;
  return (
    <tr
      className={`border-t border-border align-top ${active ? "bg-primary/[0.04] shadow-[inset_2px_0_0_hsl(var(--primary))]" : "hover:bg-muted/30"}`}
      onFocusCapture={onSelect}
      data-field={field.field_path}
    >
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <button
          type="button"
          onClick={onSelect}
          title="Show this field on the page"
          className="font-mono text-xs font-medium text-foreground underline-offset-2 [overflow-wrap:anywhere] hover:underline"
        >
          {field.field_path}
        </button>
        {kind ? (
          <span
            className={`mt-1 inline-flex items-center whitespace-nowrap rounded border px-1.5 py-px text-[10.5px] font-medium ${kind.tone}`}
            title={kind.copy}
          >
            {kind.label}
            <span className="sr-only">: {kind.copy}</span>
          </span>
        ) : null}
      </th>
      {Array.from({ length: agents }, (_, index) => {
        const raw = field.agent_values[index];
        if (index >= field.agent_values.length) {
          return <td key={index} className="px-3 py-2 text-muted-foreground">–</td>;
        }
        if (raw === null || raw === undefined) {
          return (
            <td key={index} className="px-3 py-2 italic text-muted-foreground">
              not found
            </td>
          );
        }
        const text = formatFieldValue(raw);
        const matches = proposed !== null && text === proposed;
        return (
          <td key={index} className="px-3 py-2">
            <span className="inline-flex max-w-full items-start gap-1">
              {matches ? (
                <CheckCircle2 className="mt-px h-3.5 w-3.5 shrink-0 text-emerald-600" aria-label="Matches the proposed value" />
              ) : null}
              <span className="font-mono [overflow-wrap:anywhere]">
                {proposed === null
                  ? text
                  : diffSegments(text, proposed).map((segment, i) =>
                      segment.same ? (
                        <React.Fragment key={i}>{segment.text}</React.Fragment>
                      ) : (
                        <mark key={i} className="rounded-sm bg-red-500/20 text-red-700 dark:text-red-300">
                          {segment.text}
                        </mark>
                      ),
                    )}
              </span>
            </span>
          </td>
        );
      })}
      <td className="px-3 py-2">
        {editing ? (
          <input
            value={value ?? formatFieldValue(field.consensus_value)}
            onChange={(e) => onChange(e.target.value)}
            aria-label={`Value for ${field.field_path}`}
            className="w-full min-w-[10rem] rounded border border-primary/50 bg-background px-2 py-1 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        ) : (
          <span className="inline-block max-w-full rounded bg-muted/60 px-1.5 py-0.5 font-mono font-semibold text-foreground [overflow-wrap:anywhere]">
            {formatFieldValue(field.consensus_value)}
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right">
        {confidence !== null ? (
          <span className="inline-flex items-center gap-1.5" title={`${Math.round(confidence * 100)}% confident`}>
            <span className="relative h-1.5 w-10 overflow-hidden rounded-full bg-muted">
              <span
                className={`absolute inset-y-0 left-0 rounded-full ${confidence >= 0.8 ? "bg-emerald-500" : confidence >= 0.5 ? "bg-amber-500" : "bg-red-500"}`}
                style={{ width: `${Math.round(confidence * 100)}%` }}
              />
            </span>
            <span className="tabular-nums text-muted-foreground">{Math.round(confidence * 100)}%</span>
          </span>
        ) : (
          <span className="text-muted-foreground">–</span>
        )}
      </td>
    </tr>
  );
};

export default VerificationReviewQueue;
