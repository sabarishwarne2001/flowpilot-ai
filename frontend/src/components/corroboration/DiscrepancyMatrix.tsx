/**
 * ARCH45-S2:discrepancy-matrix — every difference as a row, every document as
 * a column. A cell shows that document's value; cells that agree with the
 * most common value are plain, cells that differ are tinted, absent values
 * are marked. Rows sort by materiality (the server's order). Arrow keys move
 * the selection; the selected row drives the synchronized viewer.
 */
import React, { useMemo } from "react";

import {
  DECISION_LABELS, KIND_LABELS, LAYER_LABELS, SEVERITY_TONE,
  type DiscrepancyRow, type DocValue, type RunDocument,
} from "@/types/corroboration";

const clip = (text: string, max = 220): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

/** The value most documents hold, so the odd ones out can be marked. */
const majorityOf = (row: DiscrepancyRow, docs: readonly RunDocument[]): string | null => {
  const counts = new Map<string, number>();
  for (const doc of docs) {
    const value: DocValue | undefined = row.values[doc.work_item_id];
    if (value?.present && value.normalized) {
      counts.set(value.normalized, (counts.get(value.normalized) ?? 0) + 1);
    }
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [key, count] of counts) {
    if (count > bestCount) {
      best = key;
      bestCount = count;
    }
  }
  return bestCount > 1 ? best : null;
};

interface MatrixProps {
  readonly rows: readonly DiscrepancyRow[];
  readonly documents: readonly RunDocument[];
  readonly selectedId: string | null;
  readonly onSelect: (id: string) => void;
}

export const DiscrepancyMatrix: React.FC<MatrixProps> = ({ rows, documents, selectedId, onSelect }) => {
  const docs = useMemo(() => [...documents].sort((a, b) => a.position - b.position), [documents]);
  const move = (event: React.KeyboardEvent<HTMLTableSectionElement>): void => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
      return;
    }
    event.preventDefault();
    const index = rows.findIndex((r) => r.id === selectedId);
    const next = rows[Math.min(rows.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      onSelect(next.id);
    }
  };
  if (rows.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No differences in this view.</p>;
  }
  return (
    <div className="overflow-x-auto overscroll-x-contain">
      <table className="w-full min-w-[48rem] text-xs" aria-label="Discrepancy matrix">
        <thead>
          <tr className="border-b border-border/60 text-left text-[11px] font-medium text-muted-foreground">
            <th className="w-24 p-2">Materiality</th>
            <th className="w-64 p-2">Difference</th>
            {docs.map((doc) => (
              <th key={doc.work_item_id} className="p-2" title={doc.label}>
                <span className="mr-1 inline-flex h-4 w-4 items-center justify-center rounded-full bg-primary/10 text-[9px] font-bold text-primary">
                  {doc.position + 1}
                </span>
                {clip(doc.label, 28)}
              </th>
            ))}
            <th className="w-24 p-2">Decision</th>
          </tr>
        </thead>
        <tbody onKeyDown={move}>
          {rows.map((row) => {
            const majority = majorityOf(row, docs);
            const selected = row.id === selectedId;
            return (
              <tr
                key={row.id}
                tabIndex={0}
                aria-selected={selected}
                onClick={() => onSelect(row.id)}
                onKeyDown={(event) => { if (event.key === "Enter") { onSelect(row.id); } }}
                className={`cursor-pointer border-b border-border/40 align-top outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary ${selected ? "bg-primary/10" : "hover:bg-muted/30"} ${row.status !== "OPEN" ? "opacity-60" : ""}`}
              >
                <td className="p-2">
                  <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-bold ${SEVERITY_TONE[row.severity]}`}>
                    {row.severity}
                  </span>
                  <div className="mt-1 h-1.5 w-16 rounded bg-muted" aria-label={`materiality ${row.materiality.toFixed(2)}`}>
                    <div className="h-1.5 rounded bg-primary" style={{ width: `${Math.round(row.materiality * 100)}%` }} />
                  </div>
                  <span className="text-[10px] tabular-nums text-muted-foreground">
                    {row.materiality.toFixed(2)}{row.is_material ? " · material" : ""}
                  </span>
                </td>
                <td className="p-2">
                  <p className="font-semibold">{clip(row.label, 120)}</p>
                  <p className="text-[11px] text-muted-foreground">{LAYER_LABELS[row.layer]} · {KIND_LABELS[row.kind]}</p>
                  <p className="mt-0.5 text-[11px]">{clip(row.summary, 200)}</p>
                </td>
                {docs.map((doc) => {
                  const value = row.values[doc.work_item_id];
                  if (!value || value.participates === false) {
                    return <td key={doc.work_item_id} className="p-2 text-muted-foreground">—</td>;
                  }
                  if (!value.present) {
                    return (
                      <td key={doc.work_item_id} className="p-2">
                        <span className="rounded bg-red-500/10 px-1 text-[11px] font-semibold text-red-700 dark:text-red-300">absent</span>
                      </td>
                    );
                  }
                  const odd = majority !== null && value.normalized !== majority;
                  return (
                    <td key={doc.work_item_id} className={`p-2 ${odd || majority === null ? "bg-amber-500/10" : ""}`}>
                      <span className="break-words">{clip(value.display)}</span>
                      {value.page ? <span className="ml-1 text-[10px] text-muted-foreground">p.{value.page}</span> : null}
                    </td>
                  );
                })}
                <td className="p-2">
                  <span className="text-[11px]">{DECISION_LABELS[row.status]}</span>
                  {row.note ? <p className="text-[10px] text-muted-foreground" title={row.note}>{clip(row.note, 40)}</p> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

/** How far each pair of documents agrees (share of compared clauses, fields, lines and parties that match). */
export const AgreementGrid: React.FC<{
  readonly documents: readonly RunDocument[];
  readonly pairs: readonly { readonly left_work_item_id: string; readonly right_work_item_id: string; readonly agreement: number; readonly material_count: number }[];
}> = ({ documents, pairs }) => {
  const docs = [...documents].sort((a, b) => a.position - b.position);
  const cell = (a: string, b: string) =>
    pairs.find((p) => (p.left_work_item_id === a && p.right_work_item_id === b) || (p.left_work_item_id === b && p.right_work_item_id === a));
  const tone = (x: number): string =>
    x >= 0.9 ? "bg-green-500/25" : x >= 0.7 ? "bg-green-500/10" : x >= 0.5 ? "bg-amber-500/15" : "bg-red-500/15";
  return (
    <table className="text-[11px]" aria-label="Agreement between documents">
      <thead>
        <tr>
          <th />
          {docs.map((d) => <th key={d.work_item_id} className="px-2 font-semibold">{d.position + 1}</th>)}
        </tr>
      </thead>
      <tbody>
        {docs.map((row) => (
          <tr key={row.work_item_id}>
            <th className="pr-2 text-left font-normal" title={row.label}>{row.position + 1}. {clip(row.label, 22)}</th>
            {docs.map((col) => {
              if (col.work_item_id === row.work_item_id) {
                return <td key={col.work_item_id} className="px-2 text-center text-muted-foreground">—</td>;
              }
              const p = cell(row.work_item_id, col.work_item_id);
              return (
                <td key={col.work_item_id} className={`px-2 py-1 text-center tabular-nums ${p ? tone(p.agreement) : ""}`}
                  title={p ? `${p.material_count} material difference(s)` : ""}>
                  {p ? `${Math.round(p.agreement * 100)}%` : "–"}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
};

export default DiscrepancyMatrix;
