import React, { useMemo } from "react";
import { Info } from "lucide-react";

export interface ContextPressureBarProps {
  readonly passagesIncluded: number;
  readonly passagesDroppedBudget: number;
  readonly passagesDroppedInjection?: number;
  readonly historyCompressed?: boolean;
  readonly warnings?: readonly string[];
  readonly className?: string;
}

const REFERENCE_MARKS = [
  { at: 60, label: "Retrieved passages" },
  { at: 90, label: "History" },
] as const;

export const ContextPressureBar: React.FC<ContextPressureBarProps> = ({
  passagesIncluded,
  passagesDroppedBudget,
  passagesDroppedInjection = 0,
  historyCompressed = false,
  warnings = [],
  className = "",
}) => {
  const totalCandidates =
    passagesIncluded + passagesDroppedBudget + passagesDroppedInjection;

  const includedPct = useMemo(() => {
    if (totalCandidates === 0) {
      return 0;
    }
    return (passagesIncluded / totalCandidates) * 100;
  }, [passagesIncluded, totalCandidates]);

  const droppedBudgetPct = useMemo(() => {
    if (totalCandidates === 0) {
      return 0;
    }
    return (passagesDroppedBudget / totalCandidates) * 100;
  }, [passagesDroppedBudget, totalCandidates]);

  const droppedInjectionPct = useMemo(() => {
    if (totalCandidates === 0) {
      return 0;
    }
    return (passagesDroppedInjection / totalCandidates) * 100;
  }, [passagesDroppedInjection, totalCandidates]);

  const underPressure = passagesDroppedBudget > 0 || historyCompressed;

  if (totalCandidates === 0 && !historyCompressed && warnings.length === 0) {
    return null;
  }

  return (
    <section
      className={`rounded-lg border border-border bg-card/50 p-3 ${className}`}
      aria-label="Context window usage"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-xs font-medium text-foreground">Context used</h3>
        <p className="text-xs text-muted-foreground">
          {passagesIncluded} of {totalCandidates}{" "}
          {totalCandidates === 1 ? "passage" : "passages"} sent to the model
        </p>
      </div>

      <div
        className="relative mt-2 h-2.5 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={
          `${passagesIncluded} passages included, ` +
          `${passagesDroppedBudget} dropped for space, ` +
          `${passagesDroppedInjection} removed by the safety filter.`
        }
      >
        <div className="flex h-full w-full">
          <div
            className="h-full bg-primary transition-[width] duration-300"
            style={{ width: `${includedPct}%` }}
          />
          <div
            className="h-full bg-amber-500/70 transition-[width] duration-300"
            style={{ width: `${droppedBudgetPct}%` }}
          />
          <div
            className="h-full bg-destructive/60 transition-[width] duration-300"
            style={{ width: `${droppedInjectionPct}%` }}
          />
        </div>

        {REFERENCE_MARKS.map((mark) => (
          <span
            key={mark.at}
            aria-hidden="true"
            title={`${mark.label} (nominal ${mark.at}%)`}
            className="absolute top-0 h-full border-l border-dashed border-background/70"
            style={{ left: `${mark.at}%` }}
          />
        ))}
      </div>

      <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <div className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2 w-2 rounded-full bg-primary"
          />
          <dt className="text-muted-foreground">Sent</dt>
          <dd className="font-medium">{passagesIncluded}</dd>
        </div>

        {passagesDroppedBudget > 0 && (
          <div className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2 w-2 rounded-full bg-amber-500/70"
            />
            <dt className="text-muted-foreground">Dropped for space</dt>
            <dd className="font-medium">{passagesDroppedBudget}</dd>
          </div>
        )}

        {passagesDroppedInjection > 0 && (
          <div className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2 w-2 rounded-full bg-destructive/60"
            />
            <dt className="text-muted-foreground">Removed by filter</dt>
            <dd className="font-medium">{passagesDroppedInjection}</dd>
          </div>
        )}
      </dl>

      {historyCompressed && (
        <p className="mt-2 text-xs text-muted-foreground">
          Earlier messages were summarised to make room. The model has the gist
          of them, not the wording.
        </p>
      )}

      {underPressure && passagesDroppedBudget > 0 && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            {passagesDroppedBudget}{" "}
            {passagesDroppedBudget === 1 ? "passage" : "passages"} didn&apos;t
            fit. Ask about one document at a time to give the model more room.
          </span>
        </p>
      )}

      {warnings.length > 0 && (
        <ul className="mt-2 space-y-1">
          {warnings.map((warning) => (
            <li key={warning} className="text-xs text-muted-foreground">
              {warning}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default ContextPressureBar;
