import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight } from "lucide-react";

import { useTenant } from "@/hooks/useTenant";
import { getPlanAllowance } from "@/services/api/billing";
import { usageKeys } from "@/services/api/queryKeys";
import { organizationBillingPath } from "@/routes/tenantPaths";
import type { AllowanceMeter, PlanAllowanceResponse } from "@/types/billing";
import { formatTimestampDate } from "@/utils/displayTime";

const NOUNS: Readonly<Record<string, { one: string; many: string }>> = {
  "document.upload": { one: "document", many: "documents" },
  "ocr.page": { one: "OCR page", many: "OCR pages" },
  "assistant.message": { one: "assistant message", many: "assistant messages" },
};

const noun = (key: string, count: number): string => {
  const words = NOUNS[key];
  if (!words) {return key;}
  return count === 1 ? words.one : words.many;
};

export interface PlanAllowanceProps {
  readonly workspaceId: string;
  /** Which meters this screen is about (an upload screen: documents and pages). */
  readonly meters: readonly string[];
  readonly className?: string;
}

/**
 * Campaign session 1 — where the organization stands on its plan, on the screen
 * where the allowance is spent (uploads, the assistant).
 *
 * Every member sees it (the numbers are the server's: the same ones the 402 uses).
 * At 80% it warns; at 100% it says the next one will be refused and when the
 * allowance resets; an owner or billing manager gets an "Upgrade" link straight to
 * the plan picker, everyone else the names of the people who can.
 */
export const PlanAllowance: React.FC<PlanAllowanceProps> = ({ workspaceId, meters, className = "" }) => {
  const { state } = useTenant();
  const { data } = useQuery({
    queryKey: usageKeys.allowance(workspaceId),
    queryFn: () => getPlanAllowance(workspaceId),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
    retry: false,
  });
  if (!data) {return null;}

  const shown = data.meters.filter((meter) => meters.includes(meter.key));
  if (shown.length === 0) {return null;}
  const blocked = shown.filter((meter) => meter.hard_stop && (meter.state === "REACHED" || meter.state === "OVER"));
  const near = shown.filter((meter) => meter.state === "NEAR");
  const slug = state.status === "ready" ? state.organization.organization_slug : undefined;

  return (
    <div
      className={`rounded-md border px-3 py-2 text-xs ${
        blocked.length > 0
          ? "border-destructive/40 bg-destructive/5"
          : near.length > 0
            ? "border-amber-500/40 bg-amber-500/5"
            : "border-border bg-muted/30"
      } ${className}`}
      data-testid="plan-allowance"
      role={blocked.length > 0 || near.length > 0 ? "status" : undefined}
    >
      <p className="text-muted-foreground">
        <span className="font-medium text-foreground">{data.plan_name ?? "Your"} plan</span>
        {": "}
        {shown.map((meter, index) => (
          <React.Fragment key={meter.key}>
            {index > 0 && " · "}
            <MeterText meter={meter} />
          </React.Fragment>
        ))}
        {meters.includes("document.upload") &&
          ` · files up to ${data.max_file_mb} MB, ${data.max_pages_per_document} pages`}
      </p>
      {blocked.length > 0 ? (
        <Reached data={data} meters={blocked} slug={slug} />
      ) : near.length > 0 ? (
        <p className="mt-1 flex items-center gap-1 font-medium text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3 w-3" aria-hidden />
          {near.map((meter) => `${Math.round((meter.used / meter.limit) * 100)}% of this month's ${noun(meter.key, 2)} used`).join("; ")}.
        </p>
      ) : null}
    </div>
  );
};

const MeterText: React.FC<{ readonly meter: AllowanceMeter }> = ({ meter }) => (
  <span className="tabular-nums">
    {meter.used.toLocaleString()} of {meter.limit.toLocaleString()} {noun(meter.key, meter.limit)} this month
  </span>
);

const Reached: React.FC<{
  readonly data: PlanAllowanceResponse;
  readonly meters: readonly AllowanceMeter[];
  readonly slug: string | undefined;
}> = ({ data, meters, slug }) => {
  const first = meters[0];
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      <p className="flex items-center gap-1 font-medium text-destructive">
        <AlertTriangle className="h-3 w-3" aria-hidden />
        No {noun(first?.key ?? "", 2)} left this month; more will be refused until{" "}
        {first ? formatTimestampDate(first.resets_at) : "the next period"}.
      </p>
      {data.can_upgrade && slug ? (
        <Link
          to={`${organizationBillingPath(slug)}#plans`}
          className="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 font-semibold text-primary-foreground hover:bg-primary/90"
        >
          Upgrade
          <ArrowUpRight className="h-3 w-3" aria-hidden />
        </Link>
      ) : data.ask.length > 0 ? (
        <span className="text-muted-foreground">Ask {data.ask.join(" or ")} to upgrade the plan.</span>
      ) : null}
    </div>
  );
};

export default PlanAllowance;
