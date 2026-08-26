import React, { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, Download, Filter, Loader2, User } from "lucide-react";

import {
  downloadBlob,
  exportAuditLogs,
  listAuditLogs,
} from "@/services/api/audit";
import type { AuditExportFormat, AuditLogQuery } from "@/services/api/audit";
import { auditKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";

const PAGE_SIZE = 50;

export const AuditExplorer: React.FC = () => {
  const { organizationId } = useResolvedOrganization();

  const [filters, setFilters] = useState<AuditLogQuery>({});
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [exporting, setExporting] = useState(false);

  const query = useQuery({
    queryKey: auditKeys.list(organizationId, {
      ...filters,
      cursor: cursor ?? "start",
    }),
    queryFn: () =>
      listAuditLogs(organizationId, {
        ...filters,
        limit: PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      }),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const applyFilter = useCallback((patch: AuditLogQuery) => {
    setFilters((current) => ({ ...current, ...patch }));
    setCursor(null);
    setHistory([]);
  }, []);

  const goNext = useCallback(() => {
    const next = query.data?.next_cursor;
    if (!next) {
      return;
    }
    setHistory((stack) => [...stack, cursor]);
    setCursor(next);
  }, [query.data, cursor]);

  const goBack = useCallback(() => {
    setHistory((stack) => {
      if (stack.length === 0) {
        return stack;
      }
      const copy = [...stack];
      const previous = copy.pop() ?? null;
      setCursor(previous);
      return copy;
    });
  }, []);

  const runExport = useCallback(
    async (format: AuditExportFormat) => {
      setExporting(true);
      try {
        const blob = await exportAuditLogs(organizationId, format, filters);
        const stamp = new Date().toISOString().slice(0, 10);
        downloadBlob(
          blob,
          `audit-${stamp}.${format === "CSV" ? "csv" : "ndjson"}`,
        );
      } finally {
        setExporting(false);
      }
    },
    [organizationId, filters],
  );

  const rows = query.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Audit log</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Every privileged action, append-only.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void runExport("CSV")}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
          >
            {exporting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            CSV
          </button>
          <button
            type="button"
            onClick={() => void runExport("NDJSON")}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" />
            NDJSON
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card p-2.5">
        <Filter className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />

        <input
          value={filters.resource_type ?? ""}
          onChange={(e) => applyFilter({ resource_type: e.target.value })}
          placeholder="resource type"
          aria-label="Resource type"
          className="w-36 rounded border border-border bg-background px-2 py-1 text-xs"
        />
        <input
          value={filters.action ?? ""}
          onChange={(e) => applyFilter({ action: e.target.value })}
          placeholder="action"
          aria-label="Action"
          className="w-32 rounded border border-border bg-background px-2 py-1 text-xs"
        />
        <input
          value={filters.actor_id ?? ""}
          onChange={(e) => applyFilter({ actor_id: e.target.value })}
          placeholder="actor id"
          aria-label="Actor"
          className="w-56 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
        />
        <input
          type="date"
          onChange={(e) => {
            if (e.target.value) {
              applyFilter({
                date_from: new Date(e.target.value).toISOString(),
              });
            } else {
              setFilters((current) => {
                const { date_from: _removed, ...rest } = current;
                return rest;
              });
              setCursor(null);
              setHistory([]);
            }
          }}
          aria-label="From date"
          className="rounded border border-border bg-background px-2 py-1 text-xs"
        />

        {Object.keys(filters).length > 0 && (
          <button
            type="button"
            onClick={() => {
              setFilters({});
              setCursor(null);
              setHistory([]);
            }}
            className="ml-auto rounded border border-border px-2 py-1 text-xs hover:bg-muted"
          >
            Clear
          </button>
        )}
      </div>

      {query.isLoading ? (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading entries…
        </div>
      ) : rows.length === 0 ? (
        <p className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          No entries match these filters.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40">
              <tr className="text-left text-xs text-muted-foreground">
                <th scope="col" className="px-3 py-2 font-medium">When</th>
                <th scope="col" className="px-3 py-2 font-medium">Actor</th>
                <th scope="col" className="px-3 py-2 font-medium">Action</th>
                <th scope="col" className="px-3 py-2 font-medium">Resource</th>
                <th scope="col" className="px-3 py-2 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-border align-top">
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                    {new Date(row.created_at).toLocaleString()}
                  </td>

                  <td className="px-3 py-2 text-xs">
                    {row.api_key_id ? (
                      <span className="inline-flex items-center gap-1.5">
                        <Bot className="h-3.5 w-3.5 text-muted-foreground" />
                        <span>
                          <span className="block">Directory sync</span>
                          <span className="block font-mono text-[11px] text-muted-foreground">
                            {row.api_key_id.slice(0, 8)}
                          </span>
                        </span>
                      </span>
                    ) : row.actor_id ? (
                      <span className="inline-flex items-center gap-1.5">
                        <User className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="font-mono text-[11px]">
                          {row.actor_id.slice(0, 8)}
                        </span>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">System</span>
                    )}
                  </td>

                  <td className="px-3 py-2 text-xs font-medium">{row.action}</td>

                  <td className="px-3 py-2 text-xs">
                    <span className="block">{row.resource_type}</span>
                    {row.resource_id && (
                      <span className="block font-mono text-[11px] text-muted-foreground">
                        {row.resource_id.slice(0, 8)}
                      </span>
                    )}
                  </td>

                  <td className="px-3 py-2 text-xs">
                    <span
                      className={
                        row.outcome.toUpperCase() === "SUCCESS"
                          ? "text-emerald-700"
                          : "text-destructive"
                      }
                    >
                      {row.outcome}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={goBack}
          disabled={history.length === 0}
          className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-40"
        >
          Back
        </button>

        <span className="text-xs text-muted-foreground">
          {rows.length} {rows.length === 1 ? "entry" : "entries"}
          {history.length > 0 && ` · page ${history.length + 1}`}
        </span>

        <button
          type="button"
          onClick={goNext}
          disabled={!query.data?.has_more}
          className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
};

export default AuditExplorer;
