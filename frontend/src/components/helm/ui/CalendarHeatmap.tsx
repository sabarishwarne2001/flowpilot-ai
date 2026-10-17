import React, { useMemo, useRef, useState } from "react";

import { formatCalendarDate, formatCalendarMonthShort } from "@/utils/displayTime";

export interface HeatDay {
  readonly value: number;
  /** The day in words for its accessible name and tooltip ("₹4,20,000.00 in 3 promises"). */
  readonly words: string;
}

const DAY_MS = 86_400_000;
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const isoOf = (time: number): string => new Date(time).toISOString().slice(0, 10);
const timeOf = (iso: string): number => Date.parse(`${iso}T00:00:00Z`);

/** The Monday on or before a day (ISO weeks start on Monday). */
export const mondayOf = (iso: string): string => {
  const time = timeOf(iso);
  const weekday = (new Date(time).getUTCDay() + 6) % 7;
  return isoOf(time - weekday * DAY_MS);
};

/**
 * A calendar heatmap (docs/helm/VISUAL.md "CalendarHeatmap"): `weeks` columns of seven days from
 * the Monday of `today`'s week, each cell shaded by the amount on that day. A grid to assistive
 * technology: one tab stop, arrow keys move between days (left and right by a week), Home and End
 * jump to the first and last day, Enter opens the day. Each cell's name says its date and amount;
 * `summary` says the whole picture in words. Cells fade in along the diagonal on first view.
 */
export const CalendarHeatmap: React.FC<{
  readonly today: string;
  readonly weeks?: number;
  readonly days: ReadonlyMap<string, HeatDay>;
  readonly label: string;
  readonly summary: string;
  readonly direction?: "owe" | "owed";
  readonly onOpen?: (iso: string) => void;
  readonly testId?: string;
}> = ({ today, weeks = 12, days, label, summary, direction = "owe", onOpen, testId }) => {
  const start = mondayOf(today);
  const total = weeks * 7;
  const todayIndex = Math.round((timeOf(today) - timeOf(start)) / DAY_MS);
  const [active, setActive] = useState(todayIndex);
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const max = useMemo(() => Math.max(0, ...[...days.values()].map((d) => d.value)), [days]);

  const level = (value: number): number => (value <= 0 || max <= 0 ? 0 : Math.max(1, Math.ceil(Math.sqrt(value / max) * 4)));

  const months = useMemo(() => {
    const out: { week: number; name: string }[] = [];
    let last = "";
    for (let week = 0; week < weeks; week += 1) {
      const day = new Date(timeOf(start) + week * 7 * DAY_MS);
      const name = formatCalendarMonthShort(day.getUTCFullYear(), day.getUTCMonth() + 1);
      if (name !== last) {
        out.push({ week, name });
        last = name;
      }
    }
    return out;
  }, [start, weeks]);

  const move = (to: number): void => {
    const next = Math.max(todayIndex, Math.min(total - 1, to));
    setActive(next);
    refs.current[next]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>, index: number): void => {
    const steps: Record<string, number> = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 };
    if (event.key in steps) {
      event.preventDefault();
      move(index + (steps[event.key] as number));
    } else if (event.key === "Home") {
      event.preventDefault();
      move(todayIndex);
    } else if (event.key === "End") {
      event.preventDefault();
      move(total - 1);
    } else if ((event.key === "Enter" || event.key === " ") && onOpen) {
      event.preventDefault();
      onOpen(isoOf(timeOf(start) + index * DAY_MS));
    }
  };

  return (
    <div data-testid={testId} data-direction={direction}>
      <p className="sr-only" data-testid={testId ? `${testId}-summary` : undefined}>
        {summary}
      </p>
      <div className="helm-heat mb-1" style={{ ["--weeks" as string]: weeks }} aria-hidden="true">
        <span />
        {Array.from({ length: weeks }, (_, week) => (
          <span key={week} className="helm-faint truncate text-[10px] font-medium">
            {months.find((m) => m.week === week)?.name ?? ""}
          </span>
        ))}
      </div>
      <div
        role="grid"
        aria-label={label}
        aria-describedby={testId ? `${testId}-hint` : undefined}
        className="helm-heat"
        style={{ ["--weeks" as string]: weeks }}
      >
        {WEEKDAYS.map((weekday, row) => (
          <div role="row" key={weekday} className="contents">
            <span className="helm-faint flex items-center pr-1 text-[10px] font-medium" aria-hidden="true">
              {row % 2 === 0 ? weekday.slice(0, 1) : ""}
            </span>
            {Array.from({ length: weeks }, (_, week) => {
              const index = week * 7 + row;
              const iso = isoOf(timeOf(start) + index * DAY_MS);
              const day = days.get(iso);
              const past = index < todayIndex;
              const name = `${formatCalendarDate(iso)}${index === todayIndex ? " (today)" : ""}: ${past ? "past" : day ? day.words : "nothing due"}`;
              return (
                <div
                  key={iso}
                  ref={(node) => {
                    refs.current[index] = node;
                  }}
                  role="gridcell"
                  tabIndex={index === active ? 0 : -1}
                  aria-label={name}
                  aria-selected={index === active ? "true" : "false"}
                  aria-disabled={past ? "true" : undefined}
                  title={name}
                  className="helm-heat-cell helm-cell-in"
                  data-level={past ? 0 : level(day?.value ?? 0)}
                  data-today={index === todayIndex ? "true" : undefined}
                  data-past={past ? "true" : undefined}
                  data-date={iso}
                  style={{ ["--d" as string]: week + row }}
                  onKeyDown={(event) => onKeyDown(event, index)}
                  onFocus={() => setActive(index)}
                  onClick={() => {
                    if (past) {
                      return;
                    }
                    setActive(index);
                    onOpen?.(iso);
                  }}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="helm-faint mt-2 flex items-center justify-between gap-2 text-[10px]">
        <span id={testId ? `${testId}-hint` : undefined}>Arrow keys move, Enter opens the day</span>
        <span className="inline-flex items-center gap-1" aria-hidden="true">
          Less
          {[0, 1, 2, 3, 4].map((n) => (
            <span key={n} className="helm-heat-cell helm-heat-legend" data-level={n} style={{ cursor: "default" }} />
          ))}
          More
        </span>
      </div>
    </div>
  );
};

export default CalendarHeatmap;
