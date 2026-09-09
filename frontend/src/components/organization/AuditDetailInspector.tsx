import React from "react";
import { Bot, Globe, Monitor, User, X } from "lucide-react";

import type { AuditLogRead } from "@/services/api/audit";

/**
 * ARCH-29 Slice 3 — the audit detail inspector.
 *
 * NO NETWORK CALL, ON PURPOSE
 * ===========================
 *
 * `getAuditLog` exists and was reported as an unwired endpoint, so the obvious
 * build is a modal that fetches a row by id. It would be a wasted round trip.
 * `AuditLogPage.items` is typed `list[AuditLogRead]` on the backend and the
 * list route builds it as `[AuditLogRead.model_validate(row) for row in rows]`
 * — the identical schema the detail route returns. `details`, `ip_address`
 * and `user_agent` are already in the payload that drew the table; they were
 * simply never rendered.
 *
 * So this takes the row it is given. The endpoint stays in the service layer
 * for deep-linking to a single entry, which is a real use it does not have
 * yet.
 *
 * WHAT AN ABSENT VALUE MEANS
 * ==========================
 *
 * `details`, `ip_address` and `user_agent` are all nullable, and null is
 * meaningful rather than empty. A system-initiated action genuinely has no IP
 * — a scheduler is not a browser — and rendering a blank cell invites the
 * reader to assume the record is incomplete. Each absence says which kind of
 * absence it is.
 */

const formatJson = (value: Record<string, unknown>): string =>
  JSON.stringify(value, null, 2);

const Row: React.FC<{
  readonly label: string;
  readonly children: React.ReactNode;
}> = ({ label, children }) => (
  <div className="grid grid-cols-[8rem_1fr] gap-3 border-t border-border py-2 text-xs">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="break-all">{children}</dd>
  </div>
);

const Absent: React.FC<{ readonly reason: string }> = ({ reason }) => (
  <span className="text-muted-foreground" title={reason}>
    not recorded
  </span>
);

export interface AuditDetailInspectorProps {
  readonly entry: AuditLogRead;
  readonly onClose: () => void;
}

export const AuditDetailInspector: React.FC<AuditDetailInspectorProps> = ({
  entry,
  onClose,
}) => {
  const succeeded = entry.outcome.toUpperCase() === "SUCCESS";
  const isSystem = !entry.actor_id && !entry.api_key_id;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="audit-detail-title"
    >
      <div className="my-8 w-full max-w-2xl rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3
              id="audit-detail-title"
              className="text-base font-semibold text-foreground"
            >
              {entry.action}
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {new Date(entry.created_at).toLocaleString()} ·{" "}
              <span
                className={succeeded ? "text-emerald-700" : "text-destructive"}
              >
                {entry.outcome}
              </span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md border border-border p-1 hover:bg-muted"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <dl className="mt-4">
          <Row label="Entry ID">
            <span className="font-mono">{entry.id}</span>
          </Row>

          <Row label="Actor">
            {entry.api_key_id ? (
              <span className="inline-flex items-center gap-1.5">
                <Bot className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                <span>
                  Directory sync ·{" "}
                  <span className="font-mono">{entry.api_key_id}</span>
                </span>
              </span>
            ) : entry.actor_id ? (
              <span className="inline-flex items-center gap-1.5">
                <User
                  className="h-3.5 w-3.5 text-muted-foreground"
                  aria-hidden
                />
                <span className="font-mono">{entry.actor_id}</span>
              </span>
            ) : (
              <span className="text-muted-foreground">
                System — no human or key initiated this
              </span>
            )}
          </Row>

          <Row label="Resource">
            {entry.resource_type}
            {entry.resource_id ? (
              <span className="ml-2 font-mono text-muted-foreground">
                {entry.resource_id}
              </span>
            ) : null}
          </Row>

          <Row label="Workspace">
            {entry.workspace_id ? (
              <span className="font-mono">{entry.workspace_id}</span>
            ) : (
              <span className="text-muted-foreground">
                Organization-level — not scoped to a workspace
              </span>
            )}
          </Row>

          <Row label="IP address">
            {entry.ip_address ? (
              <span className="inline-flex items-center gap-1.5">
                <Globe
                  className="h-3.5 w-3.5 text-muted-foreground"
                  aria-hidden
                />
                <span className="font-mono">{entry.ip_address}</span>
              </span>
            ) : (
              <Absent
                reason={
                  isSystem
                    ? "A system-initiated action has no client address."
                    : "No address was captured for this entry."
                }
              />
            )}
          </Row>

          <Row label="User agent">
            {entry.user_agent ? (
              <span className="inline-flex items-start gap-1.5">
                <Monitor
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
                  aria-hidden
                />
                <span>{entry.user_agent}</span>
              </span>
            ) : (
              <Absent
                reason={
                  isSystem
                    ? "A system-initiated action has no browser."
                    : "No user agent was captured for this entry."
                }
              />
            )}
          </Row>
        </dl>

        <div className="mt-4">
          <p className="text-xs font-medium text-foreground">Event metadata</p>
          {entry.details && Object.keys(entry.details).length > 0 ? (
            <pre className="mt-1.5 max-h-72 overflow-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
              {formatJson(entry.details)}
            </pre>
          ) : (
            <p className="mt-1.5 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
              {/*
                An empty object and a null are the same thing to a reader and
                are reported the same way. What must not happen is rendering
                `{}` or `null` as if it were content.
              */}
              This action recorded no additional metadata. The entry itself is
              complete — the audit log stores metadata only where the action
              produces some.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};

export default AuditDetailInspector;
