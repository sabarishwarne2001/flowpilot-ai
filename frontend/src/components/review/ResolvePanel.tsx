/**
 * ARCH40-S2:resolve-panel. Resolving one clause or anomaly from the hub.
 *
 * Extraction items do not come here: they open the field-level workbench
 * (pages/Verification/VerificationReviewQueue in focus mode), because a
 * verification is resolved by choosing a value per field, not by a verdict.
 *
 * Each kind keeps its own semantics. A clause takes PASS / FAIL /
 * UNDETERMINED and an optional corrected quote. An anomaly is confirmed, or
 * dismissed with a reason of at least ten characters — the reason is what the
 * next reviewer reads instead of re-deciding, and the server refuses less.
 */

import React, { useState } from "react";
import { Loader2 } from "lucide-react";

import type {
  AnomalyVerdict,
  AssertionVerdict,
  ReviewItem,
  ReviewResolveRequest,
} from "@/types/review";

interface ResolvePanelProps {
  readonly item: ReviewItem;
  readonly pending: boolean;
  readonly onResolve: (body: ReviewResolveRequest) => void;
  readonly onCancel: () => void;
}

const MIN_DISMISS_REASON = 10;

export const ResolvePanel: React.FC<ResolvePanelProps> = ({ item, pending, onResolve, onCancel }) => {
  const [quote, setQuote] = useState("");
  const [reason, setReason] = useState("");
  const [ttlDays, setTtlDays] = useState("");

  if (item.kind === "ASSERTION") {
    const verdict = (value: AssertionVerdict): void => {
      const trimmed = quote.trim();
      onResolve(trimmed ? { reviewer_verdict: value, corrected_quote: trimmed } : { reviewer_verdict: value });
    };
    return (
      <div className="space-y-3">
        <p className="text-sm">
          Does the document satisfy <span className="font-semibold">“{item.headline}”</span>?
        </p>
        <label htmlFor="resolve-quote" className="block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
          Corrected quote (optional)
        </label>
        <textarea
          id="resolve-quote"
          rows={3}
          value={quote}
          onChange={(event) => setQuote(event.target.value)}
          placeholder="Paste the clause text that decides it. Retrieval learns from it."
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap gap-2">
          {(["PASS", "FAIL", "UNDETERMINED"] as const).map((value) => (
            <button
              key={value}
              type="button"
              disabled={pending}
              onClick={() => verdict(value)}
              className={`rounded-lg px-3 py-2 text-sm font-bold disabled:opacity-50 ${
                value === "PASS"
                  ? "bg-emerald-600 text-white hover:bg-emerald-700"
                  : value === "FAIL"
                    ? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
                    : "border border-border hover:bg-muted"
              }`}
            >
              {value === "UNDETERMINED" ? "Can't tell" : value === "PASS" ? "Passes" : "Fails"}
            </button>
          ))}
          <button type="button" onClick={onCancel} className="ml-auto rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-muted">
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (item.kind === "ANOMALY") {
    const dismissReady = reason.trim().length >= MIN_DISMISS_REASON;
    const send = (value: AnomalyVerdict): void => {
      const ttl = Number(ttlDays);
      const body: ReviewResolveRequest = {
        anomaly_verdict: value,
        ...(reason.trim() ? { note: reason.trim() } : {}),
        ...(value === "DISMISS" && Number.isInteger(ttl) && ttl > 0 ? { ttl_days: ttl } : {}),
      };
      onResolve(body);
    };
    return (
      <div className="space-y-3">
        <p className="text-sm font-semibold">{item.headline}</p>
        <label htmlFor="resolve-reason" className="block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
          Note (required to dismiss, at least {MIN_DISMISS_REASON} characters)
        </label>
        <textarea
          id="resolve-reason"
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={() => send("CONFIRM")}
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-bold text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
          >
            Confirm finding
          </button>
          <button
            type="button"
            disabled={pending || !dismissReady}
            onClick={() => send("DISMISS")}
            className="rounded-lg border border-border px-3 py-2 text-sm font-bold hover:bg-muted disabled:opacity-50"
          >
            Dismiss
          </button>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Suppress for
            <input
              aria-label="Suppress for days"
              inputMode="numeric"
              value={ttlDays}
              onChange={(event) => setTtlDays(event.target.value)}
              placeholder="∞"
              className="w-14 rounded border border-border bg-background px-1.5 py-1 text-xs"
            />
            days
          </label>
          {pending && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          <button type="button" onClick={onCancel} className="ml-auto rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-muted">
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return null;
};

export default ResolvePanel;
