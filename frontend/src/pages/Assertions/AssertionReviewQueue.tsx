import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ThumbsDown, ThumbsUp, Target } from "lucide-react";

import {
  listAssertionReviews,
  resolveAssertionReview,
} from "@/services/api/assertions";
import { ApiError } from "@/services/api/errors";
import AssertionLockCard from "@/components/assertions/AssertionLockCard";
import {
  BUTTON_GHOST,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  HINT,
  PAGE_TITLE,
  SURFACE,
  SURFACE_INSET,
  TEXTAREA,
} from "@/components/ui/primitives";
import { formatTimestamp } from "@/utils/displayTime";
import { FAMILY_LABELS } from "@/types/assertions";
import type { AssertionReviewItem } from "@/types/assertions";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";
import { ClauseChecksPanel } from "@/components/assertions/ClauseChecksPanel";

/**
 * ARCH-33 §4.6 — the review queue for triaged assertions.
 *
 * WHAT THE REVIEWER IS SHOWN, AND IN WHAT ORDER
 * =============================================
 *
 * The administrator's SENTENCE first, verbatim. Not the compiled plan:
 * `payment_terms.days <= 30` is a correct description of the check and a
 * useless description of the requirement, and the person reading the contract
 * is checking the requirement. The compiled form is shown underneath, smaller,
 * because a reviewer who disagrees with the verdict often disagrees with the
 * interpretation rather than with the reading.
 *
 * Then the extracted value. Then the paragraph, quoted, with its page.
 *
 * WHY "WRONG PARAGRAPH" STILL ASKS FOR A VERDICT
 * ==============================================
 *
 * It is tempting to make it a third verdict. It is not one: a reviewer who
 * says the engine read the wrong paragraph still knows whether the document
 * passes, and a queue item closed without a verdict teaches ARCH-35 nothing
 * and leaves the execution with no edge to resume on.
 *
 * So the action reveals a box for the correct paragraph and then asks for
 * pass or fail. The paragraph teaches retrieval; the verdict resumes the
 * workflow and labels the calibrator. Both, or neither.
 */

export interface AssertionReviewQueueProps {
  readonly workspaceId: string;
  /** False renders the lock card instead. */
  readonly hasCapability: boolean;
  readonly canChangePlan?: boolean;
  readonly workItemId?: string | undefined;
}

const confidenceLabel = (item: AssertionReviewItem): string => {
  if (!item.calibrated_probability) {
    return "not enough reviewed documents yet to estimate confidence";
  }
  return `${Math.round(Number(item.calibrated_probability) * 100)}% confidence`;
};

export const AssertionReviewQueue: React.FC<AssertionReviewQueueProps> = ({
  workspaceId,
  hasCapability,
  canChangePlan = false,
  workItemId,
}) => {
  const queryClient = useQueryClient();
  const [correcting, setCorrecting] = React.useState<string | null>(null);
  const [quote, setQuote] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const reviews = useQuery<AssertionReviewItem[]>({
    queryKey: ["assertion-reviews", workspaceId, workItemId ?? "all"],
    queryFn: () => listAssertionReviews(workspaceId, { workItemId }),
    enabled: hasCapability,
  });

  const resolve = useMutation({
    mutationFn: (input: {
      readonly evaluationId: string;
      readonly verdict: "PASS" | "FAIL";
      readonly correctedQuote?: string | undefined;
    }) =>
      resolveAssertionReview(workspaceId, input.evaluationId, {
        reviewerVerdict: input.verdict,
        correctedQuote: input.correctedQuote,
      }),
    onSuccess: async () => {
      setError(null);
      setCorrecting(null);
      setQuote("");
      await queryClient.invalidateQueries({
        queryKey: ["assertion-reviews", workspaceId],
      });
    },
    onError: (exc: unknown) => {
      setError(
        exc instanceof ApiError
          ? exc.message
          : "That review could not be resolved.",
      );
    },
  });

  if (!hasCapability) {
    return <AssertionLockCard canChangePlan={canChangePlan} />;
  }

  const items = reviews.data ?? [];

  return (
    <div className="space-y-4">
      {/* HARDENING-T3:D21. Author the checks where their failures are reviewed. */}
      {hasCapability && !workItemId && <ClauseChecksPanel workspaceId={workspaceId} />}
      <div className="flex items-baseline justify-between">
        <h1 className={PAGE_TITLE}>Clauses to review</h1>
        <span className={HINT}>
          {items.length} waiting
        </span>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {reviews.isError ? (
        <ErrorState
          title="Clause reviews could not be loaded"
          description={errorMessage(reviews.error, "The server did not return clause reviews. Check your connection and try again.")}
          onRetry={() => void reviews.refetch()}
        />
      ) : reviews.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Loading the queue…
        </div>
      ) : null}

      {!reviews.isLoading && items.length === 0 ? (
        <p className={HINT}>
          Nothing is waiting. Documents that clearly pass continue on their
          own; anything uncertain or failing lands here.
        </p>
      ) : null}

      {items.map((item) => {
        const evidence = item.evidence[0];
        const isCorrecting = correcting === item.evaluation_id;
        const busy = resolve.isPending;

        return (
          <article
            key={item.evaluation_id}
            className={`${SURFACE} space-y-3 p-5`}
            aria-labelledby={`assertion-${item.evaluation_id}`}
          >
            <header className="space-y-1">
              <h2
                id={`assertion-${item.evaluation_id}`}
                className="text-base font-semibold text-foreground"
              >
                {item.sentence}
              </h2>
              <p className={HINT}>
                {FAMILY_LABELS[item.family]} · {item.understood_as} ·{" "}
                {confidenceLabel(item)}
              </p>
              <p className={HINT}>
                {item.document_name ?? "Document"} ·{" "}
                {formatTimestamp(item.created_at)}
              </p>
            </header>

            <div className={`${SURFACE_INSET} space-y-2 p-3`}>
              <p className="text-sm text-foreground">
                <span className={HINT}>Read as: </span>
                <span className="font-mono">
                  {item.extracted_value?.value
                    ? `${item.extracted_value.value}${
                        item.extracted_value.unit
                          ? ` ${item.extracted_value.unit}`
                          : ""
                      }`
                    : item.extracted_value?.literal ?? "nothing found"}
                </span>
                <span className={`${HINT} ml-2`}>({item.verdict})</span>
              </p>
              {evidence?.quote ? (
                <blockquote className="border-l-2 border-primary/60 pl-3 text-sm italic text-muted-foreground">
                  {evidence.quote}
                  {evidence.page_number ? (
                    <span className={`${HINT} not-italic`}>
                      {" "}
                      (page {evidence.page_number})
                    </span>
                  ) : null}
                </blockquote>
              ) : (
                <p className={HINT}>
                  No paragraph was found for this clause in the document.
                </p>
              )}
            </div>

            {isCorrecting ? (
              <div className="space-y-2">
                <label
                  className={HINT}
                  htmlFor={`quote-${item.evaluation_id}`}
                >
                  Paste or type the paragraph that should have been read. The
                  wording is used to find it next time; the rule itself is not
                  changed.
                </label>
                <textarea
                  id={`quote-${item.evaluation_id}`}
                  className={TEXTAREA}
                  value={quote}
                  maxLength={4000}
                  onChange={(event) => setQuote(event.target.value)}
                />
              </div>
            ) : null}

            <footer className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={BUTTON_PRIMARY}
                disabled={busy || (isCorrecting && !quote.trim())}
                onClick={() =>
                  resolve.mutate({
                    evaluationId: item.evaluation_id,
                    verdict: "PASS",
                    correctedQuote: isCorrecting ? quote : undefined,
                  })
                }
              >
                <ThumbsUp className="h-3.5 w-3.5" aria-hidden />
                It passes
              </button>
              <button
                type="button"
                className={BUTTON_SECONDARY}
                disabled={busy || (isCorrecting && !quote.trim())}
                onClick={() =>
                  resolve.mutate({
                    evaluationId: item.evaluation_id,
                    verdict: "FAIL",
                    correctedQuote: isCorrecting ? quote : undefined,
                  })
                }
              >
                <ThumbsDown className="h-3.5 w-3.5" aria-hidden />
                It fails
              </button>
              <button
                type="button"
                className={BUTTON_GHOST}
                disabled={busy}
                onClick={() => {
                  setCorrecting(isCorrecting ? null : item.evaluation_id);
                  setQuote("");
                }}
              >
                <Target className="h-3.5 w-3.5" aria-hidden />
                {isCorrecting ? "Cancel correction" : "Wrong paragraph"}
              </button>
            </footer>
          </article>
        );
      })}
    </div>
  );
};

export default AssertionReviewQueue;
