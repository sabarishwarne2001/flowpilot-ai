/**
 * ARCH40-S2:review-hub. One review queue for everything that waits on a human.
 *
 * Before ARCH-40 a reviewer worked in three places: the extraction queue, the
 * clause review queue and the anomaly radar, each with its own list, its own
 * resolve action and — for two of the three — no audit row at all. This page
 * reads GET /workspaces/{id}/review, a projection over all three sources, and
 * resolves through POST .../review/{kind}/{id}/resolve, which dispatches to
 * the owning service. The hub adds no semantics of its own.
 *
 * TABS
 *   All · Extraction · Clauses · Anomalies   by source; a source the plan
 *                                            excludes is not shown at all
 *   Autonomy audits                          ARCH-35 holds and accuracy-audit
 *                                            samples (reason filter)
 *   History                                  resolved items
 *
 * ORDER is the server's: severity, then age, so the oldest high-severity item
 * is always first. The page never re-sorts.
 *
 * KEYBOARD (ignored while typing)
 *   j / k   next / previous item          x   toggle selection
 *   a       assign to me (again: unassign) r   resolve
 *   e       expand / collapse             Esc close the open panel
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Keyboard,
  Loader2,
  Lock,
  Square,
  UserRound,
} from "lucide-react";

import VerificationReviewQueue from "./VerificationReviewQueue";
import ResolvePanel from "@/components/review/ResolvePanel";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useResolvedTenant } from "@/routes/TenantContext";
import { ApiError } from "@/services/api/client";
import { reviewKeys, verificationKeys } from "@/services/api/queryKeys";
import {
  assignReview,
  bulkReview,
  listReviewAssignees,
  listReviews,
  resolveReview,
  unassignReview,
} from "@/services/api/review";
import {
  AUTONOMY_REASONS,
  KIND_LABELS,
  REASON_LABELS,
  REVIEW_SEVERITIES,
  formatAge,
  type ReviewItem,
  type ReviewKind,
  type ReviewQueueFilters,
  type ReviewResolveRequest,
  type ReviewSeverity,
} from "@/types/review";

type TabId = "ALL" | ReviewKind | "AUTONOMY" | "HISTORY";

interface TabConfig {
  readonly id: TabId;
  readonly label: string;
  readonly kind?: ReviewKind;
}

const TABS: readonly TabConfig[] = [
  { id: "ALL", label: "All" },
  { id: "EXTRACTION", label: "Extraction", kind: "EXTRACTION" },
  { id: "ASSERTION", label: "Clause assertions", kind: "ASSERTION" },
  { id: "ANOMALY", label: "Anomalies", kind: "ANOMALY" },
  // ARCH42-S2:hub-merge-tab
  { id: "MERGE", label: "Entity merges", kind: "MERGE" },
  // ARCH43-S2:hub-split-tab
  { id: "SPLIT", label: "Packet splits", kind: "SPLIT" },
  // ARCH44-S2:hub-table-tab
  { id: "TABLE", label: "Tables", kind: "TABLE" },
  // ARCH45-S2:hub-corroboration-tab
  { id: "CORROBORATION", label: "Comparisons", kind: "CORROBORATION" },
  // ARCH46-S2:hub-obligation-tab
  { id: "OBLIGATION", label: "Obligations", kind: "OBLIGATION" },
  { id: "AUTONOMY", label: "Autonomy audits", kind: "EXTRACTION" },
  { id: "HISTORY", label: "History" },
];

const AGE_OPTIONS: readonly { readonly label: string; readonly seconds: number | undefined }[] = [
  { label: "Any age", seconds: undefined },
  { label: "Older than 1 day", seconds: 86_400 },
  { label: "Older than 3 days", seconds: 259_200 },
  { label: "Older than 7 days", seconds: 604_800 },
];

const SEVERITY_STYLES: Readonly<Record<ReviewSeverity, string>> = {
  CRITICAL: "bg-red-600 text-white",
  HIGH: "bg-destructive/15 text-destructive",
  MEDIUM: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  LOW: "bg-muted text-muted-foreground",
};

const PAGE_SIZE = 25;

const newKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `bulk-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const itemKey = (item: ReviewItem): string => `${item.kind}:${item.item_id}`;

export const ReviewHub: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { user } = useResolvedTenant();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<TabId>("ALL");
  const [severities, setSeverities] = useState<readonly ReviewSeverity[]>([]);
  const [assignee, setAssignee] = useState<string>("");
  const [tag, setTag] = useState("");
  const [minAge, setMinAge] = useState<number | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [cursor, setCursor] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [bulkReason, setBulkReason] = useState("");
  const listRef = useRef<HTMLUListElement>(null);

  const active = TABS.find((entry) => entry.id === tab) ?? TABS[0];

  const filters = useMemo<ReviewQueueFilters>(() => {
    const base: ReviewQueueFilters = {
      status: tab === "HISTORY" ? "RESOLVED" : "OPEN",
      page,
      page_size: PAGE_SIZE,
      ...(active?.kind ? { kind: [active.kind] } : {}),
      ...(tab === "AUTONOMY" ? { reason: AUTONOMY_REASONS } : {}),
      ...(severities.length ? { severity: severities } : {}),
      ...(tag.trim() ? { tag: tag.trim() } : {}),
      ...(minAge !== undefined ? { min_age_seconds: minAge } : {}),
    };
    if (assignee === "__me__") {
      return { ...base, assignee_user_id: user.id };
    }
    if (assignee === "__none__") {
      return { ...base, unassigned_only: true };
    }
    return assignee ? { ...base, assignee_user_id: assignee } : base;
  }, [tab, active, page, severities, tag, minAge, assignee, user.id]);

  const queueQuery = useQuery({
    queryKey: reviewKeys.queue(workspaceId, filters),
    queryFn: () => listReviews(workspaceId, filters),
    enabled: Boolean(workspaceId),
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
  const assigneesQuery = useQuery({
    queryKey: reviewKeys.assignees(workspaceId),
    queryFn: () => listReviewAssignees(workspaceId),
    enabled: Boolean(workspaceId),
    staleTime: 60_000,
  });

  const queue = queueQuery.data;
  const items = useMemo<readonly ReviewItem[]>(() => queue?.items ?? [], [queue]);
  const allowed = queue?.allowed_kinds ?? ["EXTRACTION"];
  const tabs = TABS.filter((entry) => !entry.kind || allowed.includes(entry.kind));

  // Reset position and selection whenever the question changes.
  useEffect(() => {
    setCursor(0);
    setSelected(new Set());
    setExpanded(null);
    setResolving(null);
  }, [filters]);

  useEffect(() => {
    if (cursor > 0 && cursor >= items.length) {
      setCursor(Math.max(items.length - 1, 0));
    }
  }, [items.length, cursor]);

  const refresh = useCallback(async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: reviewKeys.all(workspaceId) });
    await queryClient.invalidateQueries({ queryKey: verificationKeys.all(workspaceId) });
  }, [queryClient, workspaceId]);

  const failure = (fallback: string) => (error: unknown) => {
    toast.error(error instanceof ApiError ? error.message : fallback);
  };

  const resolve = useMutation({
    mutationFn: (args: { item: ReviewItem; body: ReviewResolveRequest }) =>
      resolveReview(workspaceId, args.item.kind, args.item.item_id, args.body),
    onSuccess: async (result) => {
      toast.success(`Resolved: ${result.resolution.toLowerCase()}.`);
      setResolving(null);
      await refresh();
    },
    onError: failure("That item could not be resolved."),
  });

  const assign = useMutation({
    mutationFn: async (args: { item: ReviewItem; userId: string | null }) =>
      args.userId
        ? assignReview(workspaceId, args.item.kind, args.item.item_id, args.userId)
        : unassignReview(workspaceId, args.item.kind, args.item.item_id),
    onSuccess: refresh,
    onError: failure("The assignment could not be changed."),
  });

  const bulk = useMutation({
    mutationFn: async (args: { action: "resolve" | "assign" | "unassign"; body?: ReviewResolveRequest; userId?: string }) => {
      const byKind = new Map<ReviewKind, string[]>();
      for (const item of items) {
        if (selected.has(itemKey(item))) {
          const list = byKind.get(item.kind) ?? [];
          list.push(item.item_id);
          byKind.set(item.kind, list);
        }
      }
      // One request per kind: ARCH-38's bulk contract carries one `kind`.
      const responses = [];
      for (const [kind, ids] of byKind) {
        if (args.action === "resolve" && kind === "EXTRACTION") {
          continue;
        }
        responses.push(
          await bulkReview(workspaceId, {
            action: args.action,
            kind,
            ids,
            idempotency_key: newKey(),
            ...(args.userId ? { assignee_user_id: args.userId } : {}),
            ...(args.body ? { payload: args.body } : {}),
          }),
        );
      }
      return responses;
    },
    onSuccess: async (responses) => {
      const ok = responses.reduce((n, r) => n + r.ok, 0);
      const refused = responses.flatMap((r) => r.results.filter((x) => x.outcome === "refused"));
      const skipped = responses.reduce((n, r) => n + r.skipped, 0);
      if (refused.length) {
        toast.warning(`${ok} done, ${refused.length} refused${skipped ? `, ${skipped} skipped` : ""}: ${refused[0]?.detail ?? refused[0]?.code ?? ""}`);
      } else {
        toast.success(`${ok} done${skipped ? `, ${skipped} skipped` : ""}.`);
      }
      setSelected(new Set());
      setBulkReason("");
      await refresh();
    },
    onError: failure("The bulk action could not be completed."),
  });

  const toggleSelected = useCallback((item: ReviewItem): void => {
    setSelected((previous) => {
      const next = new Set(previous);
      const key = itemKey(item);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const assignToMe = useCallback((item: ReviewItem): void => {
    if (assign.isPending) {
      return;
    }
    assign.mutate({ item, userId: item.assignee_user_id === user.id ? null : user.id });
  }, [assign, user.id]);

  const openResolve = useCallback((item: ReviewItem): void => {
    if (item.status === "RESOLVED") {
      return;
    }
    setExpanded(itemKey(item));
    setResolving(itemKey(item));
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      const typing =
        target !== null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      if (typing) {
        if (event.key === "Escape") {
          target.blur();
        }
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      const item = items[cursor];
      switch (event.key) {
        case "j":
          event.preventDefault();
          setCursor((c) => Math.min(c + 1, Math.max(items.length - 1, 0)));
          break;
        case "k":
          event.preventDefault();
          setCursor((c) => Math.max(c - 1, 0));
          break;
        case "x":
          if (item) {
            event.preventDefault();
            toggleSelected(item);
          }
          break;
        case "a":
          if (item) {
            event.preventDefault();
            assignToMe(item);
          }
          break;
        case "r":
          if (item) {
            event.preventDefault();
            openResolve(item);
          }
          break;
        case "e":
          if (item) {
            event.preventDefault();
            setExpanded((open) => (open === itemKey(item) ? null : itemKey(item)));
          }
          break;
        case "Escape":
          setResolving(null);
          setExpanded(null);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [items, cursor, toggleSelected, assignToMe, openResolve]);

  useEffect(() => {
    const row = listRef.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const selectedItems = items.filter((item) => selected.has(itemKey(item)));
  const selectedKinds = new Set(selectedItems.map((item) => item.kind));
  const onlyKind = selectedKinds.size === 1 ? selectedItems[0]?.kind : undefined;
  const counts = queue?.counts_by_kind ?? {};
  const totalPages = queue ? Math.max(1, Math.ceil(queue.total / queue.page_size)) : 1;

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Review</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Everything waiting on a person, oldest high-severity first.
          </p>
        </div>
        <dl className="flex gap-2" aria-label="Open items by source">
          {allowed.map((kind) => (
            <div key={kind} className="rounded-lg border border-border bg-card px-3 py-1.5 text-center">
              <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{KIND_LABELS[kind]}</dt>
              <dd className="text-lg font-black tabular-nums">{counts[kind] ?? 0}</dd>
            </div>
          ))}
        </dl>
      </header>

      <nav className="flex gap-1 overflow-x-auto border-b border-border no-scrollbar" role="tablist" aria-label="Review views">
        {tabs.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => {
              setTab(entry.id);
              setPage(1);
            }}
            className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm font-semibold ${
              tab === entry.id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      <div className="flex flex-wrap items-center gap-2" aria-label="Filters">
        {REVIEW_SEVERITIES.map((severity) => {
          const on = severities.includes(severity);
          return (
            <button
              key={severity}
              type="button"
              aria-pressed={on}
              onClick={() => {
                setSeverities((list) => (on ? list.filter((s) => s !== severity) : [...list, severity]));
                setPage(1);
              }}
              className={`rounded-full border px-2.5 py-1 text-xs font-bold ${
                on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {severity}
            </button>
          );
        })}
        <select
          aria-label="Assignee"
          value={assignee}
          onChange={(event) => {
            setAssignee(event.target.value);
            setPage(1);
          }}
          className="rounded-lg border border-border bg-background px-2 py-1 text-xs"
        >
          <option value="">Anyone</option>
          <option value="__me__">Assigned to me</option>
          <option value="__none__">Unassigned</option>
          {(assigneesQuery.data ?? []).map((person) => (
            <option key={person.user_id} value={person.user_id}>
              {person.email} ({person.open_items})
            </option>
          ))}
        </select>
        <select
          aria-label="Age"
          value={minAge ?? ""}
          onChange={(event) => {
            setMinAge(event.target.value ? Number(event.target.value) : undefined);
            setPage(1);
          }}
          className="rounded-lg border border-border bg-background px-2 py-1 text-xs"
        >
          {AGE_OPTIONS.map((option) => (
            <option key={option.label} value={option.seconds ?? ""}>{option.label}</option>
          ))}
        </select>
        <input
          aria-label="Tag"
          value={tag}
          onChange={(event) => {
            setTag(event.target.value);
            setPage(1);
          }}
          placeholder="Tag"
          className="w-28 rounded-lg border border-border bg-background px-2 py-1 text-xs"
        />
        <span className="ml-auto hidden items-center gap-1 text-[11px] text-muted-foreground md:inline-flex">
          <Keyboard className="h-3.5 w-3.5" aria-hidden="true" /> j/k move · x select · a assign · r resolve · e expand
        </span>
      </div>

      {selectedItems.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-primary/40 bg-primary/5 px-4 py-3" role="region" aria-label="Bulk actions">
          <span className="text-sm font-bold">{selectedItems.length} selected</span>
          <select
            aria-label="Assign selected to"
            value=""
            disabled={bulk.isPending}
            onChange={(event) => {
              if (event.target.value) {
                bulk.mutate({ action: "assign", userId: event.target.value });
              }
            }}
            className="rounded-lg border border-border bg-background px-2 py-1 text-xs"
          >
            <option value="">Assign to…</option>
            {(assigneesQuery.data ?? []).map((person) => (
              <option key={person.user_id} value={person.user_id}>{person.email}</option>
            ))}
          </select>
          <button
            type="button"
            disabled={bulk.isPending}
            onClick={() => bulk.mutate({ action: "unassign" })}
            className="rounded-lg border border-border px-2.5 py-1 text-xs font-semibold hover:bg-muted"
          >
            Unassign
          </button>
          {onlyKind === "ASSERTION" && (
            <>
              <button type="button" disabled={bulk.isPending} onClick={() => bulk.mutate({ action: "resolve", body: { reviewer_verdict: "PASS" } })}
                className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-bold text-white">All pass</button>
              <button type="button" disabled={bulk.isPending} onClick={() => bulk.mutate({ action: "resolve", body: { reviewer_verdict: "FAIL" } })}
                className="rounded-lg bg-destructive px-2.5 py-1 text-xs font-bold text-destructive-foreground">All fail</button>
            </>
          )}
          {onlyKind === "ANOMALY" && (
            <>
              <input
                aria-label="Dismissal reason"
                value={bulkReason}
                onChange={(event) => setBulkReason(event.target.value)}
                placeholder="Reason (10+ chars) to dismiss"
                className="w-56 rounded-lg border border-border bg-background px-2 py-1 text-xs"
              />
              <button type="button" disabled={bulk.isPending || bulkReason.trim().length < 10}
                onClick={() => bulk.mutate({ action: "resolve", body: { anomaly_verdict: "DISMISS", note: bulkReason.trim() } })}
                className="rounded-lg border border-border px-2.5 py-1 text-xs font-bold hover:bg-muted disabled:opacity-50">Dismiss all</button>
              <button type="button" disabled={bulk.isPending}
                onClick={() => bulk.mutate({ action: "resolve", body: { anomaly_verdict: "CONFIRM" } })}
                className="rounded-lg bg-destructive px-2.5 py-1 text-xs font-bold text-destructive-foreground">Confirm all</button>
            </>
          )}
          {/* ARCH45-S2:hub-corroboration-bulk */}
          {onlyKind === "CORROBORATION" && (
            <>
              <button type="button" disabled={bulk.isPending}
                onClick={() => bulk.mutate({ action: "resolve", body: { corroboration_verdict: "CONFIRM" } })}
                className="rounded-lg bg-primary px-2.5 py-1 text-xs font-bold text-primary-foreground">Confirm all</button>
              <button type="button" disabled={bulk.isPending}
                onClick={() => bulk.mutate({ action: "resolve", body: { corroboration_verdict: "DISMISS" } })}
                className="rounded-lg border border-border px-2.5 py-1 text-xs font-bold hover:bg-muted">Dismiss all</button>
            </>
          )}
          {/* ARCH46-S2:hub-obligation-bulk */}
          {onlyKind === "OBLIGATION" && (
            <>
              <button type="button" disabled={bulk.isPending}
                onClick={() => bulk.mutate({ action: "resolve", body: { obligation_verdict: "CONFIRM" } })}
                className="rounded-lg bg-primary px-2.5 py-1 text-xs font-bold text-primary-foreground">Confirm all</button>
              <button type="button" disabled={bulk.isPending}
                onClick={() => bulk.mutate({ action: "resolve", body: { obligation_verdict: "REJECT" } })}
                className="rounded-lg border border-border px-2.5 py-1 text-xs font-bold hover:bg-muted">Reject all</button>
            </>
          )}
          {selectedKinds.has("EXTRACTION") && (
            <span className="text-[11px] text-muted-foreground">
              Extraction items are resolved one at a time: each needs a value per field.
            </span>
          )}
          {selectedKinds.size > 1 && (
            <span className="text-[11px] text-muted-foreground">Select one type to resolve in bulk.</span>
          )}
          {bulk.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          <button type="button" onClick={() => setSelected(new Set())} className="ml-auto text-xs text-muted-foreground hover:underline">
            Clear
          </button>
        </div>
      )}

      {queueQuery.isLoading ? (
        <p className="flex items-center gap-2 p-8 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading the review queue…
        </p>
      ) : queueQuery.isError ? (
        <p className="rounded-xl border border-destructive/40 bg-destructive/5 p-6 text-sm text-destructive">
          The review queue could not be loaded.{" "}
          <button type="button" className="underline" onClick={() => void queueQuery.refetch()}>Retry</button>
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          {tab === "HISTORY" ? "Nothing resolved matches these filters." : "Nothing is waiting for review here."}
        </p>
      ) : (
        <ul ref={listRef} className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card" aria-label="Review items">
          {items.map((item, index) => {
            const key = itemKey(item);
            const isCurrent = index === cursor;
            const isOpen = expanded === key;
            const isSelected = selected.has(key);
            return (
              <li key={key} data-index={index} className={isCurrent ? "bg-primary/5" : ""}>
                <div className="flex items-start gap-3 px-4 py-3">
                  <button
                    type="button"
                    onClick={() => toggleSelected(item)}
                    aria-label={isSelected ? "Deselect" : "Select"}
                    aria-pressed={isSelected}
                    className="mt-0.5 text-muted-foreground hover:text-foreground"
                  >
                    {isSelected ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setCursor(index);
                      setExpanded(isOpen ? null : key);
                    }}
                    aria-expanded={isOpen}
                    className="flex min-w-0 flex-1 items-start gap-3 text-left"
                  >
                    {isOpen ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0" /> : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0" />}
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-black ${SEVERITY_STYLES[item.severity]}`}>{item.severity}</span>
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold text-muted-foreground">{KIND_LABELS[item.kind]}</span>
                        {item.review_reason && (
                          <span className="text-[10px] font-semibold uppercase text-muted-foreground">{REASON_LABELS[item.review_reason]}</span>
                        )}
                        {item.under_retention_hold && (
                          <span className="inline-flex items-center gap-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-bold text-sky-700 dark:text-sky-400" title="This document is under a retention hold: it cannot be deleted or exported while the hold stands.">
                            <Lock className="h-3 w-3" aria-hidden="true" /> Retention hold
                          </span>
                        )}
                      </span>
                      <span className="mt-1 block truncate text-sm font-semibold">{item.headline}</span>
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                        {item.document_name ?? "Document"} · {formatAge(item.age_seconds)} old
                        {item.confidence !== null ? ` · confidence ${(item.confidence * 100).toFixed(0)}%` : ""}
                        {item.tags.length ? ` · ${item.tags.join(", ")}` : ""}
                      </span>
                    </span>
                  </button>
                  <span className="flex shrink-0 items-center gap-2">
                    {item.assignee_email ? (
                      <span className="inline-flex max-w-[10rem] items-center gap-1 truncate rounded-full bg-muted px-2 py-0.5 text-[11px]">
                        <UserRound className="h-3 w-3" aria-hidden="true" /> {item.assignee_email}
                      </span>
                    ) : (
                      item.status === "OPEN" && (
                        <button type="button" onClick={() => assignToMe(item)} className="text-[11px] font-semibold text-primary hover:underline">
                          Take it
                        </button>
                      )
                    )}
                    {item.status === "OPEN" && (
                      <button
                        type="button"
                        onClick={() => {
                          setCursor(index);
                          openResolve(item);
                        }}
                        className="rounded-lg bg-primary px-2.5 py-1 text-xs font-bold text-primary-foreground hover:bg-primary/90"
                      >
                        Resolve
                      </button>
                    )}
                  </span>
                </div>
                {isOpen && (
                  <div className="border-t border-border/60 bg-background px-4 py-4">
                    {item.under_retention_hold && (
                      <p className="mb-3 flex items-start gap-2 rounded-lg border border-sky-500/40 bg-sky-500/5 px-3 py-2 text-xs text-sky-800 dark:text-sky-300">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        This document is under a retention hold. Resolving the review does not release it, and the document cannot be deleted or exported until the hold is lifted.
                      </p>
                    )}
                    {item.kind === "EXTRACTION" ? (
                      item.status === "OPEN" ? (
                        <VerificationReviewQueue
                          focusVerificationId={item.item_id}
                          onResolved={() => {
                            setExpanded(null);
                            void refresh();
                          }}
                        />
                      ) : (
                        <p className="text-sm text-muted-foreground">Resolved. The chosen values are on the document.</p>
                      )
                    ) : resolving === key && item.status === "OPEN" ? (
                      <ResolvePanel
                        item={item}
                        pending={resolve.isPending}
                        onResolve={(body) => resolve.mutate({ item, body })}
                        onCancel={() => setResolving(null)}
                      />
                    ) : (
                      <div className="flex items-center justify-between gap-3 text-sm">
                        <span className="text-muted-foreground">
                          {item.status === "RESOLVED" ? "Resolved." : "Press r, or use Resolve, to decide."}
                        </span>
                        {item.status === "OPEN" && (
                          <button type="button" onClick={() => setResolving(key)} className="text-xs font-semibold text-primary hover:underline">
                            Decide now
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {queue && queue.total > queue.page_size && (
        <nav className="flex items-center justify-between text-xs text-muted-foreground" aria-label="Pages">
          <span>
            {queue.total} item{queue.total === 1 ? "" : "s"} · page {queue.page} of {totalPages}
          </span>
          <span className="flex gap-2">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded border border-border px-2 py-1 disabled:opacity-40">Previous</button>
            <button type="button" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
              className="rounded border border-border px-2 py-1 disabled:opacity-40">Next</button>
          </span>
        </nav>
      )}
    </div>
  );
};

export default ReviewHub;
