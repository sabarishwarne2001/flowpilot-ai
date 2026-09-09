import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, RefreshCw } from "lucide-react";

import { getDestination, updateDestination } from "@/services/api/analytics";
import { analyticsKeys } from "@/services/api/queryKeys";
import {
  KIND_LABELS,
  type DestinationStatus,
  type WarehouseDestination,
  type WarehouseDestinationUpdate,
} from "@/types/analytics";
import {
  buildCredential,
  credentialIsComplete,
  CredentialFieldset,
} from "@/components/organization/warehouseCredential";

/**
 * ARCH-29 Slice 2 — edit an existing warehouse destination.
 *
 * WHAT THIS MODAL DOES NOT DO, AND WHY
 * ====================================
 *
 * The brief asked for an editor that updates "sync cadences and credentials".
 * Cadence is not a property of a destination. `WarehouseDestinationUpdate`
 * carries exactly three optional fields — `label`, `status`, `credential` —
 * and the backend rejects a body with anything else (`extra="forbid"`).
 * Cadence lives on schedules, a separate resource with its own editor that is
 * already wired. Putting a cadence control here would either do nothing or
 * write to the wrong object.
 *
 * `kind` is likewise absent and immutable. An S3 destination cannot become a
 * Snowflake one: the credential shape differs, so the change is a new
 * destination, not an edit.
 *
 * CREDENTIALS ROTATE WHOLE OR NOT AT ALL
 * ======================================
 *
 * The backend takes the entire credential or none of it. A partial update —
 * "change the password, keep the account" — requires reading the stored
 * secret, merging, and re-encrypting, which puts the plaintext in a request
 * handler for a reason other than storing it. So rotation here is opt-in and,
 * once opted into, every field must be filled: a half-filled form that
 * submitted would write a credential that only fails at the next scheduled
 * run.
 *
 * Existing secrets are never pre-filled, because the console has never
 * received them. It has a fingerprint, which is shown so the operator can tell
 * whether the credential actually changed.
 *
 * `getDestination` is called here rather than reading the row out of the list
 * the page already holds. It is one request against a form the operator is
 * about to submit, and it means a destination another admin disabled or
 * renamed thirty seconds ago is not silently overwritten with a stale label.
 */

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail[0]?.msg) {
    return String(detail[0].msg);
  }
  return fallback;
}

export interface WarehouseDestinationEditorProps {
  readonly organizationId: string;
  /** The row from the list; used for immediate render while the fetch lands. */
  readonly destination: WarehouseDestination;
  readonly onClose: () => void;
}

export const WarehouseDestinationEditor: React.FC<
  WarehouseDestinationEditorProps
> = ({ organizationId, destination, onClose }) => {
  const queryClient = useQueryClient();

  const fresh = useQuery({
    queryKey: analyticsKeys.destination(organizationId, destination.id),
    queryFn: () => getDestination(organizationId, destination.id),
    initialData: destination,
    staleTime: 0,
  });

  const current = fresh.data ?? destination;

  const [label, setLabel] = useState(destination.label);
  const [status, setStatus] = useState<DestinationStatus>(destination.status);
  const [rotating, setRotating] = useState(false);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const value = (key: string): string => fields[key] ?? "";
  const onChange =
    (key: string) =>
    (
      event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ): void =>
      setFields((prev) => ({ ...prev, [key]: event.target.value }));

  const labelChanged = label.trim() !== current.label;
  const statusChanged = status !== current.status;
  const credentialReady =
    rotating && credentialIsComplete(current.kind, value);

  const save = useMutation({
    mutationFn: () => {
      // Only changed fields are sent. The backend refuses an empty body, and
      // sending an unchanged label as if it were an edit would put a
      // meaningless write in the audit trail.
      const payload: WarehouseDestinationUpdate = {};
      if (labelChanged) {
        payload.label = label.trim();
      }
      if (statusChanged) {
        payload.status = status;
      }
      if (credentialReady) {
        payload.credential = buildCredential(current.kind, value);
      }
      return updateDestination(organizationId, current.id, payload);
    },
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({
        queryKey: analyticsKeys.destinations(organizationId),
      });
      onClose();
    },
    onError: (err) =>
      setError(detailOf(err, "The destination could not be updated.")),
  });

  const dirty = labelChanged || statusChanged || credentialReady;
  const rotationIncomplete = rotating && !credentialReady;

  const LABEL = "text-sm font-medium text-foreground";
  const HINT = "mt-1 text-xs text-muted-foreground";
  const INPUT =
    "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="destination-editor-title"
    >
      <div className="my-8 w-full max-w-2xl rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="flex items-start gap-3">
          <Pencil
            className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h3
              id="destination-editor-title"
              className="text-base font-semibold text-foreground"
            >
              Edit {current.label}
            </h3>
            <p className={HINT}>
              {KIND_LABELS[current.kind]} · credential{" "}
              <span className="font-mono">{current.credential_fingerprint}</span>
            </p>
          </div>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="destination-edit-label">
              Label
            </label>
            <input
              id="destination-edit-label"
              className={INPUT}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
            <p className={HINT}>Shown in the run history. Must be unique.</p>
          </div>

          <div>
            <label className={LABEL} htmlFor="destination-edit-status">
              Status
            </label>
            <select
              id="destination-edit-status"
              className={INPUT}
              value={status}
              onChange={(event) =>
                setStatus(event.target.value as DestinationStatus)
              }
            >
              <option value="ACTIVE">Active</option>
              <option value="DISABLED">Disabled</option>
            </select>
            <p className={HINT}>
              Disabling stops scheduled exports without deleting the
              destination or its run history.
            </p>
          </div>
        </div>

        <div className="mt-5 rounded-md border border-border p-3">
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={rotating}
              onChange={(event) => {
                setRotating(event.target.checked);
                if (!event.target.checked) {
                  setFields({});
                }
              }}
            />
            <span>
              <span className="flex items-center gap-1.5 font-medium text-foreground">
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                Replace the credential
              </span>
              <span className={HINT}>
                All fields at once — there is no partial credential edit. Leave
                this off to change only the label or status.
              </span>
            </span>
          </label>

          {rotating ? (
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <CredentialFieldset
                kind={current.kind}
                value={value}
                onChange={onChange}
                idPrefix="destination-edit"
              />
            </div>
          ) : null}

          {rotationIncomplete ? (
            <p className="mt-3 text-xs text-muted-foreground">
              Fill every field to rotate. A partly-filled credential would be
              accepted here and fail at the next scheduled run.
            </p>
          ) : null}
        </div>

        {error ? (
          <p className="mt-3 text-xs text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
            onClick={onClose}
            disabled={save.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : null}
            Save changes
          </button>
        </div>
      </div>
    </div>
  );
};

export default WarehouseDestinationEditor;
