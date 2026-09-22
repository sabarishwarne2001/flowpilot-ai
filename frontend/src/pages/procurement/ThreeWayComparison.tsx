import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import CapabilityLockCard from "@/components/procurement/CapabilityLockCard";
import PdfViewer from "@/components/pdf/PdfViewer";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { ApiError } from "@/services/api/errors";
import { approveCase, disputeCase, getCase } from "@/services/api/procurement";
import { procurementKeys } from "@/services/api/queryKeys";
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  HINT,
  PAGE_TITLE,
  SURFACE,
  SURFACE_DIALOG,
  TEXTAREA,
} from "@/components/ui/primitives";
import type { CaseLine, EvidencePointer } from "@/types/procurement";
import { RED_OUTCOMES, outcomeLabel, outcomeTone } from "@/types/procurement";
import { formatTimestamp } from "@/utils/displayTime";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";

const RECONCILIATION_CAPABILITY = "capability.reconciliation";
const MINIMUM_DISPUTE_REASON = 10;

type Side = "po" | "receipt" | "invoice";

const formatMicros = (micros: number | null, currency: string): string =>
  micros === null
    ? "—"
    : new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(micros / 1_000_000);

const formatQuantity = (value: string | null): string => value ?? "—";

/**
 * ARCH-31 Step 4 — the three-way comparison grid.
 *
 * ONE ALIGNED GRID, NOT THREE PANES
 * =================================
 * Three side-by-side document views would make the reader do the alignment,
 * and the alignment is the entire product: the matcher already decided which
 * PO line corresponds to which invoice line, and a layout that hides that
 * decision throws the work away. Every row here is one matched line across
 * all three documents, and the Result column says what the engine concluded.
 *
 * COLOUR IS NEVER ALONE
 * =====================
 * Each result cell carries a sentence and a colour. This is a screen people
 * authorise payments from; a reviewer with a colour-vision deficiency reading
 * amber-vs-red cells with no text has no information at all.
 */
export const ThreeWayComparison: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const { caseId = "" } = useParams<{ caseId: string }>();
  const queryClient = useQueryClient();
  const currency = "INR";

  const capability = useCapabilityAccess(organizationId, RECONCILIATION_CAPABILITY);

  const [cursor, setCursor] = useState(0);
  const [evidence, setEvidence] = useState<{ side: Side; pointer: EvidencePointer } | null>(
    null,
  );
  const [dialog, setDialog] = useState<"approve" | "dispute" | null>(null);
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const rowRefs = useRef<Array<HTMLTableRowElement | null>>([]);

  const caseQuery = useQuery({
    queryKey: procurementKeys.case(workspaceId, caseId),
    queryFn: () => getCase(workspaceId, caseId),
    enabled: Boolean(workspaceId && caseId) && capability.granted,
    staleTime: 15_000,
  });

  const detail = caseQuery.data ?? null;
  const lines = useMemo<readonly CaseLine[]>(() => detail?.lines ?? [], [detail]);
  const activeLine = lines[cursor] ?? null;
  const redLineCount = useMemo(
    () => lines.filter((line) => RED_OUTCOMES.has(line.outcome)).length,
    [lines],
  );

  const onSettled = useCallback(async () => {
    setDialog(null);
    setReason("");
    setActionError(null);
    await queryClient.invalidateQueries({
      queryKey: procurementKeys.all(workspaceId),
    });
  }, [queryClient, workspaceId]);

  /**
   * Refusals are read through ApiError's structured envelope, never
   * error.response. The backend returns {code, message, details} and the
   * code is the branching contract: OVERRIDE_REASON_REQUIRED reopens the
   * dialog with the reason field focused, CASE_NOT_LIVE means somebody else
   * resolved it while this tab was open.
   */
  const describeFailure = useCallback((error: unknown): string => {
    if (error instanceof ApiError) {
      if (error.is("OVERRIDE_REASON_REQUIRED")) {
        return "This case has exceptions. Write why you're approving it anyway.";
      }
      if (error.is("CASE_NOT_LIVE")) {
        return "This case was already resolved or re-scored. Reload to see the current one.";
      }
      if (error.is("CAPABILITY_REQUIRED")) {
        return "Procurement matching isn't included on your current plan.";
      }
      return error.message;
    }
    return "Something went wrong. Try again.";
  }, []);

  const approve = useMutation({
    mutationFn: (override?: string) => approveCase(workspaceId, caseId, override),
    onSuccess: onSettled,
    onError: (error) => {
      setActionError(describeFailure(error));
      if (error instanceof ApiError && error.is("OVERRIDE_REASON_REQUIRED")) {
        setDialog("approve");
      }
    },
  });

  const dispute = useMutation({
    mutationFn: (text: string) => disputeCase(workspaceId, caseId, text),
    onSuccess: onSettled,
    onError: (error) => setActionError(describeFailure(error)),
  });

  const openEvidence = useCallback(
    (line: CaseLine | null) => {
      if (!line) {
        return;
      }
      const side: Side =
        line.evidence.invoice ? "invoice" : line.evidence.po ? "po" : "receipt";
      const pointer = line.evidence[side];
      if (pointer) {
        setEvidence({ side, pointer });
      }
    },
    [],
  );

  const startApprove = useCallback(() => {
    setActionError(null);
    if (redLineCount > 0) {
      setDialog("approve");
      return;
    }
    approve.mutate(undefined);
  }, [approve, redLineCount]);

  /** j/k move, e evidence, a approve, d dispute. */
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (dialog !== null) {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      switch (event.key) {
        case "j":
          event.preventDefault();
          setCursor((value) => Math.min(value + 1, Math.max(lines.length - 1, 0)));
          break;
        case "k":
          event.preventDefault();
          setCursor((value) => Math.max(value - 1, 0));
          break;
        case "e":
          event.preventDefault();
          openEvidence(activeLine);
          break;
        case "a":
          event.preventDefault();
          startApprove();
          break;
        case "d":
          event.preventDefault();
          setActionError(null);
          setDialog("dispute");
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeLine, dialog, lines.length, openEvidence, startApprove]);

  useEffect(() => {
    rowRefs.current[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (capability.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading…
      </div>
    );
  }

  if (!capability.granted) {
    return (
      <div className="p-6">
        <CapabilityLockCard canChangePlan={workspace?.role === "ADMIN"} />
      </div>
    );
  }

  // HARDENING-T2:D26. A failed request rendered as a blank or permanent spinner.
  if (caseQuery.isError) {
    return (
      <ErrorState
        title="This case could not be loaded"
        description={errorMessage(caseQuery.error, "The server did not return this case. Check your connection and try again.")}
        onRetry={() => void caseQuery.refetch()}
      />
    );
  }

  if (caseQuery.isLoading || !detail) {
    return <p className={`${HINT} p-6`}>Loading case…</p>;
  }

  const resolved = detail.status === "APPROVED" || detail.status === "DISPUTED";

  return (
    <div className="space-y-4 p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className={PAGE_TITLE}>Three-way match</h1>
          <p className={HINT}>
            {detail.line_count} lines · {redLineCount} needing attention · scored{" "}
            {formatTimestamp(detail.created_at)} under policy {detail.policy_version}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={resolved || approve.isPending}
            onClick={startApprove}
          >
            Approve match
          </button>
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={resolved || dispute.isPending}
            onClick={() => {
              setActionError(null);
              setDialog("dispute");
            }}
          >
            Flag dispute
          </button>
        </div>
      </header>

      {detail.header_findings.length > 0 ? (
        <section className={`${SURFACE} space-y-1 border-amber-500/40 p-4`}>
          <h2 className="text-sm font-semibold">Before comparing documents</h2>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {detail.header_findings.map((finding: any, index: number) => (
              <li key={`${finding.code}-${index}`}>
                <span className="font-mono">{finding.side ?? "—"}</span> ·{" "}
                {finding.detail ?? finding.code}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {actionError ? (
        <p role="alert" className="text-sm text-destructive">
          {actionError}
        </p>
      ) : null}

      <div className={evidence ? "grid gap-4 lg:grid-cols-2" : ""}>
        <div className={`${SURFACE} overflow-x-auto`}>
          <table className="w-full text-sm">
            <caption className="sr-only">
              Purchase order, goods receipt and invoice compared line by line.
              Press j and k to move, e to open evidence, a to approve, d to
              dispute.
            </caption>
            <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left">Line</th>
                <th className="px-3 py-2 text-left">Purchase order</th>
                <th className="px-3 py-2 text-left">Goods receipt</th>
                <th className="px-3 py-2 text-left">Invoice</th>
                <th className="px-3 py-2 text-left">Result</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line, index) => (
                <tr
                  key={line.id}
                  ref={(node) => {
                    rowRefs.current[index] = node;
                  }}
                  aria-selected={index === cursor}
                  onClick={() => {
                    setCursor(index);
                    openEvidence(line);
                  }}
                  className={`cursor-pointer border-t border-border/60 ${
                    index === cursor ? "bg-primary/5" : "hover:bg-muted/40"
                  }`}
                >
                  <td className="px-3 py-2 align-top">
                    <div className="font-medium">{line.description ?? "—"}</div>
                    <div className="font-mono text-xs text-muted-foreground">
                      {line.sku ?? ""}
                    </div>
                  </td>
                  <td className="px-3 py-2 align-top tabular-nums">
                    {formatQuantity(line.po_quantity)} ×{" "}
                    {formatMicros(line.po_unit_price_micros, currency)}
                  </td>
                  <td className="px-3 py-2 align-top tabular-nums">
                    {formatQuantity(line.receipt_quantity)}
                  </td>
                  <td className="px-3 py-2 align-top tabular-nums">
                    {formatQuantity(line.invoice_quantity)} ×{" "}
                    {formatMicros(line.invoice_unit_price_micros, currency)}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <span
                      className={`inline-block rounded border px-2 py-0.5 text-xs ${outcomeTone(
                        line.outcome,
                      )}`}
                    >
                      {outcomeLabel(line)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {evidence ? (
          <aside className={`${SURFACE} p-2`} aria-label="Source document">
            <div className="flex items-center justify-between px-2 pb-2">
              <span className="text-xs uppercase text-muted-foreground">
                {evidence.side}
              </span>
              <button
                type="button"
                className="text-xs text-muted-foreground hover:underline"
                onClick={() => setEvidence(null)}
              >
                Close
              </button>
            </div>
            <PdfViewer
              workspaceId={workspaceId}
              workItemId={evidence.pointer.work_item_id}
              sources={
                evidence.pointer.bbox
                  ? [
                      {
                        work_item_id: evidence.pointer.work_item_id,
                        original_filename: "",
                        chunk_id: `case-line-${evidence.pointer.line_index}`,
                        chunk_index: evidence.pointer.line_index,
                        page_number: evidence.pointer.page ?? null,
                        bbox: evidence.pointer.bbox as never,
                        page_start_char: evidence.pointer.char_start ?? null,
                        page_end_char: evidence.pointer.char_end ?? null,
                        snippet: "",
                        similarity_score: 1,
                        rank: 1,
                      },
                    ]
                  : []
              }
              activeChunkId={`case-line-${evidence.pointer.line_index}`}
            />
          </aside>
        ) : null}
      </div>

      <p className={HINT}>
        j / k move between lines · e opens the source document · a approves · d
        disputes
      </p>

      {dialog === "approve" ? (
        <div className={`${SURFACE_DIALOG} fixed inset-x-0 bottom-0 z-50 m-4 space-y-3 p-4 sm:mx-auto sm:max-w-lg`}>
          <h2 className="text-sm font-semibold">
            Approve with {redLineCount} unresolved line
            {redLineCount === 1 ? "" : "s"}
          </h2>
          <p className={HINT}>
            Write why this is being approved anyway. An auditor reads this when
            asking why the invoice was paid despite the exception.
          </p>
          <textarea
            className={TEXTAREA}
            value={reason}
            autoFocus
            onChange={(event) => setReason(event.target.value)}
            aria-label="Override reason"
          />
          <div className="flex justify-end gap-2">
            <button type="button" className={BUTTON_SECONDARY} onClick={() => setDialog(null)}>
              Cancel
            </button>
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={reason.trim().length === 0 || approve.isPending}
              onClick={() => approve.mutate(reason.trim())}
            >
              Approve
            </button>
          </div>
        </div>
      ) : null}

      {dialog === "dispute" ? (
        <div className={`${SURFACE_DIALOG} fixed inset-x-0 bottom-0 z-50 m-4 space-y-3 p-4 sm:mx-auto sm:max-w-lg`}>
          <h2 className="text-sm font-semibold">Flag a dispute</h2>
          <p className={HINT}>
            At least {MINIMUM_DISPUTE_REASON} characters. This text goes to the
            supplier, so &ldquo;wrong&rdquo; starts a phone call rather than
            ending one.
          </p>
          <textarea
            className={TEXTAREA}
            value={reason}
            autoFocus
            onChange={(event) => setReason(event.target.value)}
            aria-label="Dispute reason"
          />
          <div className="flex justify-end gap-2">
            <button type="button" className={BUTTON_SECONDARY} onClick={() => setDialog(null)}>
              Cancel
            </button>
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={
                reason.trim().length < MINIMUM_DISPUTE_REASON || dispute.isPending
              }
              onClick={() => dispute.mutate(reason.trim())}
            >
              Flag dispute
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ThreeWayComparison;
