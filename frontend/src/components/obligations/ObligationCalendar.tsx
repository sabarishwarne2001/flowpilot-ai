/**
 * ARCH46-S2:obligation-calendar — a month of obligations. Due dates are LOCAL
 * dates in the workspace's zone (the server says which day "today" is there),
 * so the grid is built from YYYY-MM-DD strings, never from instants a browser
 * in another zone would shift. Future occurrences of a series are shown too,
 * marked as projected.
 */
import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

import { BUTTON_GHOST, HINT, SURFACE } from "@/components/ui/primitives";
import { obligationPath } from "@/routes/tenantPaths";
import { getCalendar, obligationKeys } from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";
import { KIND_LABELS, STATE_TONE, WEEKDAY_LABELS, type CalendarOccurrence } from "@/types/obligations";
import { formatCalendarMonth } from "@/utils/displayTime";

const pad = (n: number): string => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number): string => `${y}-${pad(m)}-${pad(d)}`;

/** Day arithmetic on calendar dates (UTC is used only as an arithmetic frame, never shown). */
const shiftDay = (value: string, days: number): string => {
  const [y, m, d] = value.split("-").map((x) => Number.parseInt(x, 10));
  const t = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + days));
  return iso(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
};

const isoWeekday = (value: string): number => {
  const [y, m, d] = value.split("-").map((x) => Number.parseInt(x, 10));
  const w = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).getUTCDay();
  return w === 0 ? 7 : w;
};

interface Props {
  readonly workspaceId: string;
  readonly orgSlug: string;
  readonly workspaceSlug: string;
  readonly mine: boolean;
}

export const ObligationCalendar: React.FC<Props> = ({ workspaceId, orgSlug, workspaceSlug, mine }) => {
  const now = new Date();
  const [month, setMonth] = useState<{ y: number; m: number }>({ y: now.getFullYear(), m: now.getMonth() + 1 });
  const first = iso(month.y, month.m, 1);
  const gridStart = shiftDay(first, -(isoWeekday(first) - 1));
  const gridEnd = shiftDay(gridStart, 41);
  const owner = mine ? "me" : "";
  const query = useQuery({
    queryKey: obligationKeys.calendar(workspaceId, gridStart, gridEnd, owner),
    queryFn: () => getCalendar(workspaceId, gridStart, gridEnd, owner || undefined),
    enabled: Boolean(workspaceId),
  });
  const byDay = useMemo(() => {
    const out = new Map<string, CalendarOccurrence[]>();
    for (const item of query.data?.items ?? []) {
      const list = out.get(item.due_date) ?? [];
      list.push(item);
      out.set(item.due_date, list);
    }
    return out;
  }, [query.data]);
  const days = Array.from({ length: 42 }, (_, i) => shiftDay(gridStart, i));
  const today = query.data?.today ?? "";
  const monthName = formatCalendarMonth(month.y, month.m);
  const move = (delta: number): void => {
    const total = month.y * 12 + (month.m - 1) + delta;
    setMonth({ y: Math.floor(total / 12), m: (total % 12) + 1 });
  };

  return (
    <section className={`${SURFACE} space-y-3 p-3`} aria-label="Obligations calendar">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={BUTTON_GHOST} onClick={() => move(-1)} aria-label="Previous month">
          <ChevronLeft className="h-4 w-4" aria-hidden />
        </button>
        <h2 className="min-w-40 text-center text-base font-semibold">{monthName}</h2>
        <button type="button" className={BUTTON_GHOST} onClick={() => move(1)} aria-label="Next month">
          <ChevronRight className="h-4 w-4" aria-hidden />
        </button>
        <button type="button" className={BUTTON_GHOST}
          onClick={() => setMonth({ y: now.getFullYear(), m: now.getMonth() + 1 })}>This month</button>
        {query.isFetching ? <Loader2 className="h-4 w-4 animate-spin" aria-label="Loading" /> : null}
        {query.data ? <span className={`${HINT} ml-auto`}>Dates in {query.data.timezone}</span> : null}
      </div>
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "The calendar could not be loaded.")}</p> : null}
      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-border/60 bg-border/60 text-xs" role="grid">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="bg-muted/60 p-1.5 text-center font-semibold text-muted-foreground" role="columnheader">{label}</div>
        ))}
        {days.map((day) => {
          const inMonth = day.slice(0, 7) === first.slice(0, 7);
          const items = byDay.get(day) ?? [];
          return (
            <div key={day} role="gridcell" aria-label={day}
              className={`min-h-24 space-y-1 bg-card p-1.5 ${inMonth ? "" : "opacity-50"} ${day === today ? "ring-2 ring-inset ring-primary" : ""}`}>
              <div className={`text-right tabular-nums ${day === today ? "font-bold text-primary" : "text-muted-foreground"}`}>
                {Number.parseInt(day.slice(8), 10)}
              </div>
              {items.slice(0, 4).map((item) => (
                <Link key={`${item.obligation_id}-${item.occurrence}`} to={obligationPath(orgSlug, workspaceSlug, item.obligation_id)}
                  title={`${KIND_LABELS[item.kind]}: ${item.title}${item.projected ? " (a later occurrence)" : ""}`}
                  className={`block truncate rounded px-1 py-0.5 ${STATE_TONE[item.state]} ${item.projected ? "border border-dashed border-border bg-transparent" : ""}`}>
                  {item.title}
                </Link>
              ))}
              {items.length > 4 ? <div className="text-[10px] text-muted-foreground">+{items.length - 4} more</div> : null}
            </div>
          );
        })}
      </div>
    </section>
  );
};

export default ObligationCalendar;
