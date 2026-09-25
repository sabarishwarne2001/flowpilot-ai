/**
 * ARCH46-S2:new-obligation — a person's own obligation: a stated date, a date
 * counted from another date ("renewal − 60 days", in days, business days,
 * months or years), or a repeating series (RFC 5545 RRULE). "Check the date"
 * asks the server for the exact arithmetic — month ends, leap years, business
 * days on the chosen holiday calendar — and shows every step before saving.
 */
import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Calculator, Loader2 } from "lucide-react";

import { BUTTON_GHOST, BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SELECT, SURFACE } from "@/components/ui/primitives";
import { calculateDate, createObligation, listHolidayCalendars, obligationKeys } from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";
import { listWorkspaceMembers } from "@/services/api/workspaces";
import {
  KINDS, KIND_LABELS, ROLLS, ROLL_LABELS, UNITS, UNIT_LABELS, formatDay,
  type DateCalculation, type ObligationDetail, type ObligationKind, type RuleSpec,
} from "@/types/obligations";

type Mode = "FIXED" | "OFFSET" | "SERIES";

const SERIES_PRESETS: readonly { readonly label: string; readonly rrule: string }[] = [
  { label: "Every month", rrule: "FREQ=MONTHLY" },
  { label: "Last day of every month", rrule: "FREQ=MONTHLY;BYMONTHDAY=-1" },
  { label: "Every quarter end", rrule: "FREQ=YEARLY;BYMONTH=3,6,9,12;BYMONTHDAY=-1" },
  { label: "Every year", rrule: "FREQ=YEARLY" },
  { label: "Every week", rrule: "FREQ=WEEKLY" },
];

interface Props {
  readonly workspaceId: string;
  readonly onCreated: (detail: ObligationDetail) => void;
  readonly onCancel: () => void;
}

export const NewObligation: React.FC<Props> = ({ workspaceId, onCreated, onCancel }) => {
  const [kind, setKind] = useState<ObligationKind>("OTHER");
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<Mode>("FIXED");
  const [date, setDate] = useState("");
  const [anchorDate, setAnchorDate] = useState("");
  const [sign, setSign] = useState(-1);
  const [n, setN] = useState(30);
  const [unit, setUnit] = useState("DAY");
  const [rrule, setRrule] = useState(SERIES_PRESETS[0]?.rrule ?? "FREQ=MONTHLY");
  const [start, setStart] = useState("");
  const [offsetDays, setOffsetDays] = useState(0);
  const [roll, setRoll] = useState("NONE");
  const [owner, setOwner] = useState("");
  const [calendarId, setCalendarId] = useState("");
  const [lead, setLead] = useState("");
  const [preview, setPreview] = useState<DateCalculation | null>(null);

  const members = useQuery({ queryKey: ["workspace-members", workspaceId], queryFn: () => listWorkspaceMembers(workspaceId),
    enabled: Boolean(workspaceId) });
  const calendars = useQuery({ queryKey: obligationKeys.calendars(workspaceId), queryFn: () => listHolidayCalendars(workspaceId),
    enabled: Boolean(workspaceId) });

  const rule = (): RuleSpec => {
    if (mode === "FIXED") {
      return { kind: "FIXED", date, roll };
    }
    if (mode === "OFFSET") {
      return { kind: "OFFSET", anchor_date: anchorDate, sign, period: { n, unit }, roll };
    }
    const base: RuleSpec = { kind: "SERIES", rrule, start, roll };
    return offsetDays ? { ...base, sign: 1, period: { n: offsetDays, unit: "DAY" } } : base;
  };

  const calc = useMutation({
    mutationFn: () => calculateDate(workspaceId, calendarId ? { rule: rule(), calendar_id: calendarId } : { rule: rule() }),
    onSuccess: (result) => setPreview(result),
  });
  const create = useMutation({
    mutationFn: () => {
      const leadDays = lead.trim() ? Number.parseInt(lead, 10) : null;
      return createObligation(workspaceId, {
        kind, title: title.trim(), rule: rule(),
        ...(owner ? { owner_user_id: owner } : {}),
        ...(calendarId ? { calendar_id: calendarId } : {}),
        ...(leadDays !== null && !Number.isNaN(leadDays) ? { lead_days: leadDays } : {}),
      });
    },
    onSuccess: onCreated,
  });
  const ready = title.trim().length > 0 && (
    (mode === "FIXED" && date) || (mode === "OFFSET" && anchorDate && n >= 0) || (mode === "SERIES" && start && rrule));
  const owners = (members.data?.items ?? []).filter((m) => m.id !== null && m.status === "ACTIVE");

  return (
    <form className={`${SURFACE} space-y-3 p-4`} aria-label="New obligation"
      onSubmit={(event) => { event.preventDefault(); if (ready) { create.mutate(); } }}>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="space-y-1 md:col-span-2">
          <span className={FIELD_LABEL}>What is due</span>
          <input className={INPUT} value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)}
            placeholder="File the annual compliance return" />
        </label>
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Kind</span>
          <select className={SELECT} value={kind} onChange={(e) => setKind(e.target.value as ObligationKind)}>
            {KINDS.map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="space-y-2">
        <legend className={FIELD_LABEL}>When</legend>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="How the date is set">
          {([["FIXED", "On a date"], ["OFFSET", "Counted from a date"], ["SERIES", "Repeats"]] as const).map(([value, label]) => (
            <button key={value} type="button" role="radio" aria-checked={mode === value} onClick={() => { setMode(value); setPreview(null); }}
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${mode === value ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground"}`}>
              {label}
            </button>
          ))}
        </div>
        {mode === "FIXED" ? (
          <input type="date" className={`${INPUT} max-w-xs`} value={date} onChange={(e) => setDate(e.target.value)} aria-label="Due date" />
        ) : null}
        {mode === "OFFSET" ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <input type="number" min={0} max={3650} className={`${INPUT} w-24`} value={n}
              onChange={(e) => setN(Number.parseInt(e.target.value || "0", 10))} aria-label="How many" />
            <select className={`${SELECT} w-40`} value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Unit">
              {UNITS.map((u) => <option key={u} value={u}>{UNIT_LABELS[u]}</option>)}
            </select>
            <select className={`${SELECT} w-28`} value={sign} onChange={(e) => setSign(Number.parseInt(e.target.value, 10))} aria-label="Before or after">
              <option value={-1}>before</option>
              <option value={1}>after</option>
            </select>
            <input type="date" className={`${INPUT} w-44`} value={anchorDate} onChange={(e) => setAnchorDate(e.target.value)} aria-label="Anchor date" />
          </div>
        ) : null}
        {mode === "SERIES" ? (
          <div className="grid gap-2 md:grid-cols-3">
            <label className="space-y-1">
              <span className={HINT}>Repeats</span>
              <select className={SELECT} value={rrule} onChange={(e) => setRrule(e.target.value)}>
                {SERIES_PRESETS.map((p) => <option key={p.rrule} value={p.rrule}>{p.label}</option>)}
              </select>
            </label>
            <label className="space-y-1">
              <span className={HINT}>RRULE (RFC 5545)</span>
              <input className={`${INPUT} font-mono text-xs`} value={rrule} onChange={(e) => setRrule(e.target.value)} />
            </label>
            <label className="space-y-1">
              <span className={HINT}>Starting</span>
              <input type="date" className={INPUT} value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="space-y-1">
              <span className={HINT}>Days after each date</span>
              <input type="number" min={0} max={365} className={INPUT} value={offsetDays}
                onChange={(e) => setOffsetDays(Number.parseInt(e.target.value || "0", 10))} />
            </label>
          </div>
        ) : null}
        <div className="grid gap-2 md:grid-cols-2">
          <label className="space-y-1">
            <span className={HINT}>On a weekend or holiday</span>
            <select className={SELECT} value={roll} onChange={(e) => setRoll(e.target.value)}>
              {ROLLS.map((r) => <option key={r} value={r}>{ROLL_LABELS[r]}</option>)}
            </select>
          </label>
          <label className="space-y-1">
            <span className={HINT}>Holiday calendar</span>
            <select className={SELECT} value={calendarId} onChange={(e) => setCalendarId(e.target.value)}>
              <option value="">Weekends only</option>
              {(calendars.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}{c.is_default ? " (default)" : ""}</option>)}
            </select>
          </label>
        </div>
        <button type="button" className={BUTTON_GHOST} disabled={!ready || calc.isPending} onClick={() => calc.mutate()}>
          {calc.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Calculator className="h-4 w-4" aria-hidden />}
          Check the date
        </button>
        {calc.isError ? <p className="text-sm text-destructive">{errorMessage(calc.error, "That rule could not be read.")}</p> : null}
        {preview ? (
          <div className="rounded-lg border border-border/60 bg-muted/30 p-2 text-xs" aria-live="polite">
            <p className="font-semibold">Due {formatDay(preview.due_date)}</p>
            <ol className="ml-4 list-decimal text-muted-foreground">
              {preview.steps.map((step) => <li key={step}>{step}</li>)}
            </ol>
            {preview.upcoming.length > 1 ? (
              <p className="mt-1 text-muted-foreground">Then {preview.upcoming.slice(1, 6).map((o) => formatDay(o.due_date)).join(", ")}</p>
            ) : null}
          </div>
        ) : null}
      </fieldset>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Owner</span>
          <select className={SELECT} value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">Unassigned</option>
            {owners.map((m) => <option key={m.user.id} value={m.user.id}>{m.user.email}</option>)}
          </select>
        </label>
        <label className="space-y-1">
          <span className={FIELD_LABEL}>Remind how many days before</span>
          <input type="number" min={0} max={365} className={INPUT} value={lead} placeholder="Default for the kind"
            onChange={(e) => setLead(e.target.value)} />
        </label>
      </div>
      {create.isError ? <p className="text-sm text-destructive">{errorMessage(create.error, "The obligation could not be saved.")}</p> : null}
      <div className="flex gap-2">
        <button type="submit" className={BUTTON_PRIMARY} disabled={!ready || create.isPending}>
          {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null} Save obligation
        </button>
        <button type="button" className={BUTTON_GHOST} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
};

export default NewObligation;
