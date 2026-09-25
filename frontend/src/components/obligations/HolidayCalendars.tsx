/**
 * ARCH46-S2:holiday-calendars — the stored calendars business days are counted
 * on. A calendar is generated from a built-in template for the years asked
 * (US federal, England & Wales, India national), pasted as "YYYY-MM-DD Name"
 * lines, or imported from an .ics file; nothing is fetched from a calendar
 * service. Editing one reschedules every obligation that counts with it.
 * Administrators change them; everyone can read them.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Loader2, Star, Trash2 } from "lucide-react";

import { BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SELECT, SURFACE, TEXTAREA } from "@/components/ui/primitives";
import {
  createHolidayCalendar, deleteHolidayCalendar, listHolidayCalendars, obligationKeys, updateHolidayCalendar,
} from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";
import { WEEKDAY_LABELS, formatDay, type HolidayCalendarCreate, type HolidayRow } from "@/types/obligations";

type Source = "TEMPLATE" | "MANUAL" | "ICS";

/** "2026-01-26 Republic Day" per line (the name is optional). */
export const parseHolidayLines = (text: string): HolidayRow[] => {
  const out: HolidayRow[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const m = /^\s*(\d{4}-\d{2}-\d{2})\s*[,;:\t -]?\s*(.*)$/.exec(raw);
    if (m && m[1]) {
      out.push({ date: m[1], name: (m[2] ?? "").trim().slice(0, 120) || "Holiday" });
    }
  }
  return out;
};

interface Props {
  readonly workspaceId: string;
  readonly isAdmin: boolean;
}

export const HolidayCalendars: React.FC<Props> = ({ workspaceId, isAdmin }) => {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: obligationKeys.calendars(workspaceId), queryFn: () => listHolidayCalendars(workspaceId),
    enabled: Boolean(workspaceId) });
  const [name, setName] = useState("");
  const [source, setSource] = useState<Source>("TEMPLATE");
  const [template, setTemplate] = useState("IN-NATIONAL");
  const [years, setYears] = useState(`${new Date().getFullYear()}, ${new Date().getFullYear() + 1}`);
  const [lines, setLines] = useState("");
  const [ics, setIcs] = useState("");
  const [weekend, setWeekend] = useState<number[]>([6, 7]);
  const [makeDefault, setMakeDefault] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: obligationKeys.all(workspaceId) });
  };

  const create = useMutation({
    mutationFn: () => {
      const yearList = years.split(/[\s,]+/).map((y) => Number.parseInt(y, 10)).filter((y) => !Number.isNaN(y));
      const body: HolidayCalendarCreate = {
        name: name.trim(), weekend_days: weekend, is_default: makeDefault,
        ...(source === "TEMPLATE" ? { template, years: yearList } : {}),
        ...(source === "MANUAL" ? { holidays: parseHolidayLines(lines) } : {}),
        ...(source === "ICS" ? { ics, years: yearList } : {}),
      };
      return createHolidayCalendar(workspaceId, body);
    },
    onSuccess: (row) => { setName(""); setLines(""); setIcs(""); setMessage(`Saved ${row.name} with ${row.holidays.length} holiday(s).`); refresh(); },
  });
  const makeDefaultMutation = useMutation({
    mutationFn: (id: string) => updateHolidayCalendar(workspaceId, id, { is_default: true }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteHolidayCalendar(workspaceId, id),
    onSuccess: refresh,
  });

  const readFile = (file: File | undefined): void => {
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setIcs(String(reader.result ?? ""));
    reader.readAsText(file);
  };
  const toggleWeekend = (day: number): void => {
    setWeekend((current) => (current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort()));
  };

  return (
    <div className="space-y-4">
      <p className={HINT}>
        Business days skip a calendar&apos;s weekend days and its stored holidays. New obligations use the default calendar;
        an obligation keeps the calendar it was created with until you change it.
      </p>
      {query.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Calendars could not be loaded.")}</p> : null}
      <ul className="space-y-2">
        {(query.data?.items ?? []).map((cal) => (
          <li key={cal.id} className={`${SURFACE} space-y-2 p-3`}>
            <div className="flex flex-wrap items-center gap-2">
              <CalendarDays className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span className="font-semibold">{cal.name}</span>
              {cal.is_default ? <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">Default</span> : null}
              <span className={HINT}>
                {cal.holidays.length} holiday(s) · weekend {cal.weekend_days.map((d) => WEEKDAY_LABELS[d - 1]).join(", ") || "none"}
                · {cal.obligations} obligation(s) · {cal.source.toLowerCase()}{cal.template_code ? ` ${cal.template_code}` : ""}
              </span>
              <button type="button" className={`${BUTTON_GHOST} ml-auto`} onClick={() => setOpen(open === cal.id ? null : cal.id)}>
                {open === cal.id ? "Hide dates" : "Show dates"}
              </button>
              {isAdmin && !cal.is_default ? (
                <button type="button" className={BUTTON_GHOST} disabled={makeDefaultMutation.isPending}
                  onClick={() => makeDefaultMutation.mutate(cal.id)}>
                  <Star className="h-4 w-4" aria-hidden /> Make default
                </button>
              ) : null}
              {isAdmin ? (
                <button type="button" className={BUTTON_DESTRUCTIVE} disabled={remove.isPending}
                  onClick={() => { if (window.confirm(`Delete ${cal.name}? Its obligations fall back to weekends only.`)) { remove.mutate(cal.id); } }}>
                  <Trash2 className="h-4 w-4" aria-hidden /> Delete
                </button>
              ) : null}
            </div>
            {open === cal.id ? (
              <ul className="grid gap-x-4 text-xs sm:grid-cols-2 md:grid-cols-3">
                {cal.holidays.map((h) => (
                  <li key={h.date}><span className="tabular-nums">{formatDay(h.date)}</span> — {h.name}</li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
        {query.data && query.data.items.length === 0 ? <li className={HINT}>No holiday calendar yet: business days skip weekends only.</li> : null}
      </ul>
      {isAdmin ? (
        <form className={`${SURFACE} space-y-3 p-4`} aria-label="New holiday calendar"
          onSubmit={(event) => { event.preventDefault(); if (name.trim()) { create.mutate(); } }}>
          <h3 className="text-sm font-semibold">New holiday calendar</h3>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1">
              <span className={FIELD_LABEL}>Name</span>
              <input className={INPUT} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="India national holidays" />
            </label>
            <label className="space-y-1">
              <span className={FIELD_LABEL}>From</span>
              <select className={SELECT} value={source} onChange={(e) => setSource(e.target.value as Source)}>
                <option value="TEMPLATE">A built-in template</option>
                <option value="MANUAL">Dates I paste</option>
                <option value="ICS">An .ics file</option>
              </select>
            </label>
          </div>
          {source === "TEMPLATE" ? (
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1">
                <span className={FIELD_LABEL}>Template</span>
                <select className={SELECT} value={template} onChange={(e) => setTemplate(e.target.value)}>
                  {(query.data?.templates ?? []).map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
                </select>
                <span className={HINT}>{query.data?.templates.find((t) => t.code === template)?.description ?? ""}</span>
              </label>
              <label className="space-y-1">
                <span className={FIELD_LABEL}>Years</span>
                <input className={INPUT} value={years} onChange={(e) => setYears(e.target.value)} />
              </label>
            </div>
          ) : null}
          {source === "MANUAL" ? (
            <label className="block space-y-1">
              <span className={FIELD_LABEL}>Holidays, one per line</span>
              <textarea className={TEXTAREA} rows={6} value={lines} onChange={(e) => setLines(e.target.value)}
                placeholder={"2026-01-26 Republic Day\n2026-10-20 Diwali"} />
              <span className={HINT}>{parseHolidayLines(lines).length} date(s) read</span>
            </label>
          ) : null}
          {source === "ICS" ? (
            <div className="space-y-1">
              <input type="file" accept=".ics,text/calendar" onChange={(e) => readFile(e.target.files?.[0])} aria-label=".ics file" />
              <label className="block space-y-1">
                <span className={HINT}>Years to expand yearly repeats into</span>
                <input className={INPUT} value={years} onChange={(e) => setYears(e.target.value)} />
              </label>
              <span className={HINT}>{ics ? `${ics.length.toLocaleString()} characters loaded` : "No file chosen"}</span>
            </div>
          ) : null}
          <fieldset className="flex flex-wrap items-center gap-2 text-xs">
            <legend className={FIELD_LABEL}>Weekend days</legend>
            {WEEKDAY_LABELS.map((label, i) => (
              <label key={label} className="inline-flex items-center gap-1">
                <input type="checkbox" checked={weekend.includes(i + 1)} onChange={() => toggleWeekend(i + 1)} /> {label}
              </label>
            ))}
          </fieldset>
          <label className="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} /> Use for new obligations
          </label>
          {create.isError ? <p className="text-sm text-destructive">{errorMessage(create.error, "The calendar could not be saved.")}</p> : null}
          {message ? <p className="text-sm text-muted-foreground" aria-live="polite">{message}</p> : null}
          <button type="submit" className={BUTTON_PRIMARY} disabled={!name.trim() || create.isPending}>
            {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null} Save calendar
          </button>
        </form>
      ) : null}
    </div>
  );
};

export default HolidayCalendars;
