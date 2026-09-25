/**
 * ARCH44-S2:page-table-viewer — one extracted table.
 *
 * The header renders exactly as detected (group labels span their columns,
 * stacked labels span rows). Every body cell is shaded by its confidence
 * (the heat legend reads it out), and a cell that failed an arithmetic check
 * is outlined with the expected and actual figures on hover and in the
 * failures list; choosing a failure scrolls to its cell. Contributors correct a
 * cell by double-clicking it (or Enter), set a column's role (learned for the
 * layout after three unanimous confirmations), and accept or reject a flagged
 * table. CSV, XLSX and JSON downloads go through the authenticated client.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, CheckCircle2, Download, Loader2, RotateCw, Table2, XCircle } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, INPUT, PAGE_TITLE, SCROLL_X, SECTION_TITLE, SELECT, SURFACE,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { tablesPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { reviewKeys } from "@/services/api/queryKeys";
import { correctCells, downloadTable, getTable, reviewTable, setColumnRole, tableKeys } from "@/services/api/tables";
import {
  CHECK_LABELS, COLUMN_ROLES, ROLE_LABELS, STATUS_LABELS, STATUS_TONE, confidenceTone,
  type ColumnRole, type ExportFormat, type RowKind, type TableCell, type TableDetail, type TableValidationRow,
} from "@/types/tables";

const ROW_STYLE: Readonly<Record<RowKind, string>> = {
  HEADER: "",
  BODY: "",
  SECTION: "font-semibold bg-muted/30",
  SUBTOTAL: "font-semibold border-t border-border",
  TOTAL: "font-bold border-t-2 border-border",
  CARRY: "italic text-muted-foreground",
};

const cellKey = (row: number, col: number): string => `${row}:${col}`;

const HEAT_LEGEND: readonly { readonly label: string; readonly tone: string }[] = [
  { label: "95%+", tone: "border border-border" },
  { label: "85–95%", tone: confidenceTone(0.9) },
  { label: "60–85%", tone: confidenceTone(0.7) },
  { label: "below 60%", tone: confidenceTone(0.3) },
];

interface Editing {
  readonly row: number;
  readonly col: number;
  readonly value: string;
}

const describe = (cell: TableCell | undefined, failure: TableValidationRow | undefined): string => {
  if (!cell) {return "Empty";}
  const parts = [`Confidence ${Math.round(cell.confidence * 100)}%`];
  if (failure) {parts.push(failure.message);}
  if (cell.flags.includes("TYPE_MISMATCH")) {parts.push("Does not read as this column's type");}
  if (cell.flags.includes("LOW_OCR")) {parts.push("Low recognition confidence");}
  if (cell.flags.includes("SPILL")) {parts.push("Text runs into the next column");}
  if (cell.original_text !== null && cell.flags.includes("CORRECTED")) {parts.push(`Corrected (was "${cell.original_text}")`);}
  return parts.join(" · ");
};

const TableGrid: React.FC<{
  readonly detail: TableDetail;
  readonly canEdit: boolean;
  readonly focus: string | null;
  readonly saving: boolean;
  readonly onSave: (row: number, col: number, text: string) => void;
  readonly onRole: (col: number, role: ColumnRole) => void;
}> = ({ detail, canEdit, focus, saving, onSave, onRole }) => {
  const [editing, setEditing] = useState<Editing | null>(null);
  const { table, columns, rows, cells, validations } = detail;
  const index = useMemo(() => new Map(cells.map((c) => [cellKey(c.row, c.col), c])), [cells]);
  const failures = useMemo(
    () => new Map(validations.filter((v) => v.scope === "CELL" && v.row_index !== null && v.col_index !== null)
      .map((v) => [cellKey(v.row_index ?? 0, v.col_index ?? 0), v])),
    [validations],
  );
  const covered = useMemo(() => {
    const out = new Set<string>();
    for (const c of cells) {
      for (let r = c.row; r < c.row + c.row_span; r += 1) {
        for (let k = c.col; k < c.col + c.col_span; k += 1) {
          if (r !== c.row || k !== c.col) {out.add(cellKey(r, k));}
        }
      }
    }
    return out;
  }, [cells]);
  const labelCol = useMemo(() => columns.find((c) => c.value_type === "TEXT")?.index ?? 0, [columns]);
  const headerRows = rows.filter((r) => r.index < table.header_rows);
  const bodyRows = rows.filter((r) => r.index >= table.header_rows);
  const commit = (): void => {
    if (!editing) {return;}
    const current = index.get(cellKey(editing.row, editing.col))?.text ?? "";
    if (editing.value.trim() !== current) {onSave(editing.row, editing.col, editing.value.trim());}
    setEditing(null);
  };
  return (
    <table className="w-full border-collapse text-sm" aria-label={`Table ${table.ordinal + 1}`}>
      <thead>
        {canEdit ? (
          <tr className="bg-muted/20">
            <th className="w-10 p-1 text-xs text-muted-foreground" scope="col">Role</th>
            {columns.map((c) => (
              <th key={c.index} className="p-1" scope="col">
                <select
                  aria-label={`Role of ${c.path.join(" / ") || `column ${c.index + 1}`}`}
                  className={`${SELECT} h-7 w-full text-xs`}
                  value={c.role}
                  disabled={saving}
                  onChange={(e) => onRole(c.index, e.target.value as ColumnRole)}
                >
                  {COLUMN_ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
                </select>
                {c.role_source !== "INFERRED" ? (
                  <span className="block text-[10px] text-muted-foreground">{c.role_source === "LEARNED" ? "learned" : "set by reviewer"}</span>
                ) : null}
              </th>
            ))}
          </tr>
        ) : null}
        {headerRows.map((r) => (
          <tr key={r.index} className="bg-muted/40">
            {r.index === 0 ? <th className="w-10 p-1" rowSpan={table.header_rows} aria-hidden /> : null}
            {columns.map((c) => {
              const k = cellKey(r.index, c.index);
              if (covered.has(k)) {return null;}
              const cell = index.get(k);
              return (
                <th key={k} scope="col" rowSpan={cell?.row_span ?? 1} colSpan={cell?.col_span ?? 1}
                    className="border border-border/60 p-2 text-center font-semibold">
                  {cell?.text ?? ""}
                </th>
              );
            })}
          </tr>
        ))}
        {table.header_rows === 0 ? (
          <tr className="bg-muted/40">
            <th className="w-10 p-1" aria-hidden />
            {columns.map((c) => <th key={c.index} scope="col" className="border border-border/60 p-2">{c.key}</th>)}
          </tr>
        ) : null}
      </thead>
      <tbody>
        {bodyRows.map((r) => (
          <tr key={r.index} className={ROW_STYLE[r.kind]}>
            <td className="w-10 p-1 text-center text-[10px] text-muted-foreground" title={`Page ${r.page}`}>p{r.page}</td>
            {columns.map((c) => {
              const k = cellKey(r.index, c.index);
              if (covered.has(k)) {return null;}
              const cell = index.get(k);
              const failure = failures.get(k);
              const numeric = c.value_type === "MONEY" || c.value_type === "NUMBER" || c.value_type === "PERCENT";
              const isEditing = editing?.row === r.index && editing?.col === c.index;
              const start = (): void => {
                if (canEdit && !saving) {setEditing({ row: r.index, col: c.index, value: cell?.text ?? "" });}
              };
              return (
                <td
                  key={k}
                  id={`cell-${k}`}
                  colSpan={cell?.col_span ?? 1}
                  tabIndex={0}
                  title={describe(cell, failure)}
                  onDoubleClick={start}
                  onKeyDown={(e) => { if (e.key === "Enter" && !isEditing) {start();} }}
                  className={[
                    "border border-border/40 p-1.5 align-top outline-none focus:ring-2 focus:ring-primary/60",
                    numeric ? "text-right tabular-nums" : "text-left",
                    cell ? confidenceTone(cell.confidence) : "",
                    failure ? "ring-2 ring-inset ring-red-500" : "",
                    focus === k ? "outline outline-2 outline-offset-1 outline-primary" : "",
                  ].join(" ")}
                  style={c.index === labelCol && r.level > 0 ? { paddingLeft: `${0.4 + r.level * 1.1}rem` } : undefined}
                >
                  {isEditing ? (
                    <input
                      autoFocus
                      aria-label="Corrected value"
                      className={`${INPUT} h-7 w-full min-w-[6rem] text-sm`}
                      value={editing.value}
                      onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                      onBlur={commit}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {commit();}
                        if (e.key === "Escape") {setEditing(null);}
                      }}
                    />
                  ) : (
                    <>
                      {cell?.text ?? ""}
                      {cell?.flags.includes("CORRECTED") ? <span className="ml-1 text-[10px] text-primary" aria-label="corrected">●</span> : null}
                    </>
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
};

const TableViewer: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "", tableId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; tableId: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.tableIntelligence);
  const canEdit = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const client = useQueryClient();
  const key = tableKeys.detail(workspaceId, tableId);
  const query = useQuery({ queryKey: key, queryFn: () => getTable(workspaceId, tableId), enabled: Boolean(workspaceId && tableId && capability.granted) });
  const [focus, setFocus] = useState<string | null>(null);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const settle = (detail: TableDetail): void => {
    client.setQueryData(key, detail);
    void client.invalidateQueries({ queryKey: tableKeys.all(workspaceId) });
    void client.invalidateQueries({ queryKey: reviewKeys.all(workspaceId) });
  };
  const correct = useMutation({
    mutationFn: (edit: { row: number; col: number; text: string }) => correctCells(workspaceId, tableId, [edit]),
    onSuccess: settle,
  });
  const role = useMutation({ mutationFn: (v: { col: number; role: ColumnRole }) => setColumnRole(workspaceId, tableId, v.col, v.role), onSuccess: settle });
  const review = useMutation({ mutationFn: (verdict: "ACCEPT" | "REJECT") => reviewTable(workspaceId, tableId, verdict), onSuccess: settle });
  const download = async (format: ExportFormat): Promise<void> => {
    if (!query.data) {return;}
    setExporting(format);
    setExportError(null);
    try {
      await downloadTable(workspaceId, tableId, format, query.data.table.original_filename, query.data.table.ordinal);
    } catch (error) {
      setExportError(errorMessage(error, "The download failed."));
    } finally {
      setExporting(null);
    }
  };
  if (!capability.granted) {
    return <p className={`${SURFACE} p-6 text-sm`}>Table intelligence is included on the Business and Enterprise plans.</p>;
  }
  if (query.isLoading) {return <Loader2 className="m-6 h-5 w-5 animate-spin" aria-label="Loading" />;}
  if (query.isError || !query.data) {
    return <p className="p-6 text-sm text-destructive">{errorMessage(query.error, "This table could not be loaded.")}</p>;
  }
  const detail = query.data;
  const t = detail.table;
  const relations = detail.validations.filter((v) => v.scope === "RELATION");
  const failures = detail.validations.filter((v) => v.scope === "CELL");
  const columnName = (col: number | null): string =>
    col === null ? "" : detail.columns[col]?.path.join(" / ") || `column ${col + 1}`;
  const mutationError = correct.error ?? role.error ?? review.error;
  const busy = correct.isPending || role.isPending || review.isPending;
  return (
    <div className="space-y-4 p-4">
      <Link to={tablesPath(orgSlug, workspaceSlug)} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline">
        <ArrowLeft className="h-4 w-4" aria-hidden /> All tables
      </Link>
      <header className="flex flex-wrap items-start gap-3">
        <Table2 className="mt-1 h-5 w-5" aria-hidden />
        <div className="min-w-0 flex-1">
          <h1 className={PAGE_TITLE}>
            Table {t.ordinal + 1} — {t.original_filename}
          </h1>
          <p className={HINT}>
            {t.title ? `${t.title} · ` : ""}
            {t.page_start === t.page_end ? `Page ${t.page_start}` : `Pages ${t.page_start}–${t.page_end}`} ·{" "}
            {t.n_rows - t.header_rows} rows × {t.n_cols} columns · {t.method.toLowerCase()}
            {t.rotation ? ` · rotated ${t.rotation}°` : ""}
            {t.skew_degrees ? ` · deskewed ${t.skew_degrees}°` : ""} · confidence {Math.round(t.confidence * 100)}%
          </p>
        </div>
        <span className={`rounded px-2 py-1 text-xs font-semibold ${STATUS_TONE[t.status]}`}>{STATUS_LABELS[t.status]}</span>
      </header>
      <div className="flex flex-wrap items-center gap-2">
        {(["csv", "xlsx", "json"] as const).map((format) => (
          <button key={format} type="button" className={BUTTON_SECONDARY} disabled={exporting !== null}
                  onClick={() => void download(format)}>
            {exporting === format ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
            {format.toUpperCase()}
          </button>
        ))}
        {canEdit && t.status === "FLAGGED" ? (
          <>
            <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => review.mutate("ACCEPT")}>
              <CheckCircle2 className="h-4 w-4" aria-hidden /> Accept figures
            </button>
            <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => review.mutate("REJECT")}>
              <XCircle className="h-4 w-4" aria-hidden /> Reject table
            </button>
          </>
        ) : null}
        {busy ? <RotateCw className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Saving" /> : null}
      </div>
      {exportError ? <p className="text-sm text-destructive">{exportError}</p> : null}
      {mutationError ? <p className="text-sm text-destructive">{errorMessage(mutationError, "The change was not saved.")}</p> : null}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground" aria-label="Confidence legend">
        <span>Confidence:</span>
        {HEAT_LEGEND.map((l) => (
          <span key={l.label} className="inline-flex items-center gap-1">
            <span className={`inline-block h-3 w-5 rounded-sm ${l.tone}`} aria-hidden />{l.label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-5 rounded-sm ring-2 ring-inset ring-red-500" aria-hidden />fails a check</span>
        {canEdit ? <span>· double-click a cell (or press Enter) to correct it</span> : null}
      </div>
      <div className={`${SURFACE} ${SCROLL_X}`}>
        <TableGrid detail={detail} canEdit={canEdit} focus={focus} saving={busy}
                   onSave={(row, col, text) => correct.mutate({ row, col, text })}
                   onRole={(col, r) => role.mutate({ col, role: r })} />
      </div>
      <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="checks-title">
        <h2 id="checks-title" className={SECTION_TITLE}>Arithmetic checks</h2>
        {relations.length === 0 ? (
          <p className={HINT}>No running balance, row total or column total was found to check in this table.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {relations.map((r, i) => (
              <li key={i} className="flex items-start gap-2">
                {r.outcome === "PASS"
                  ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-green-600" aria-label="passes" />
                  : <AlertTriangle className="mt-0.5 h-4 w-4 text-red-600" aria-label="fails" />}
                <span><span className="font-semibold">{CHECK_LABELS[r.kind]}.</span> {r.message}</span>
              </li>
            ))}
          </ul>
        )}
        {failures.length > 0 ? (
          <div className="space-y-1">
            <h3 className="text-sm font-semibold">Figures that do not reconcile</h3>
            <ul className="space-y-1 text-sm">
              {failures.map((f, i) => (
                <li key={i}>
                  <button
                    type="button"
                    className="text-left hover:underline"
                    onClick={() => {
                      const k = cellKey(f.row_index ?? 0, f.col_index ?? 0);
                      setFocus(k);
                      document.getElementById(`cell-${k}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
                    }}
                  >
                    Row {(f.row_index ?? 0) - t.header_rows + 1}, {columnName(f.col_index)}: {f.message}
                    {f.expected !== null ? <span className="text-muted-foreground"> (expected {f.expected}, found {f.actual ?? "—"})</span> : null}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>
      {detail.learned_mappings.length > 0 ? (
        <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="learned-title">
          <h2 id="learned-title" className={SECTION_TITLE}>Column roles learned for this layout</h2>
          <ul className="space-y-1 text-sm">
            {detail.learned_mappings.map((m) => (
              <li key={`${m.header_key}:${m.role}`}>
                “{m.header_key}” → {ROLE_LABELS[m.role]} · {m.confirmations} confirmation{m.confirmations === 1 ? "" : "s"}
                {m.contradictions ? `, ${m.contradictions} disagreement${m.contradictions === 1 ? "" : "s"}` : ""}
                {m.applied ? " · applied to new tables" : " · not yet applied"}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
};

export default TableViewer;
