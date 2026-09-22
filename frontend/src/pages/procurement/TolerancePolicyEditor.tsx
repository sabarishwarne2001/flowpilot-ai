import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import CapabilityLockCard from "@/components/procurement/CapabilityLockCard";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { ApiError } from "@/services/api/errors";
import {
  listPolicies,
  previewPolicy,
  publishPolicy,
  type PolicyDraft,
} from "@/services/api/procurement";
import { procurementKeys } from "@/services/api/queryKeys";
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  FIELD_LABEL,
  HINT,
  INPUT,
  PAGE_TITLE,
  SECTION_TITLE,
  SURFACE,
} from "@/components/ui/primitives";
import { formatTimestamp } from "@/utils/displayTime";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";

const RECONCILIATION_CAPABILITY = "capability.reconciliation";

const EMPTY_DRAFT: PolicyDraft = {
  price_tolerance_micros: 0,
  price_tolerance_bps: 0,
  quantity_tolerance: "0",
  max_pair_cost: 600_000,
  candidate_window_days: 90,
};

/**
 * ARCH-31 Step 4 — the tolerance policy editor.
 *
 * PUBLISHING IS NOT SAVING, AND THE SCREEN SAYS SO
 * ================================================
 * A published policy is immutable at the database. There is no edit button
 * and no "save" — only "publish version N+1" — because the alternative is a
 * button that looks like every other save button in the product and fails
 * with a trigger exception. The impact preview exists so the reader can see
 * what the numbers do BEFORE committing to a version they cannot take back.
 */
export const TolerancePolicyEditor: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const queryClient = useQueryClient();

  const capability = useCapabilityAccess(organizationId, RECONCILIATION_CAPABILITY);
  const [draft, setDraft] = useState<PolicyDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);

  const policiesQuery = useQuery({
    queryKey: procurementKeys.policies(workspaceId),
    queryFn: () => listPolicies(workspaceId),
    enabled: Boolean(workspaceId) && capability.granted,
    staleTime: 30_000,
  });

  const current = useMemo(
    () => policiesQuery.data?.find((policy: any) => policy.status === "PUBLISHED") ?? null,
    [policiesQuery.data],
  );

  const describe = (err: unknown): string =>
    err instanceof ApiError ? err.message : "Something went wrong. Try again.";

  const preview = useMutation({
    mutationFn: () => previewPolicy(workspaceId, draft, 30),
    onError: (err) => setError(describe(err)),
  });

  const publish = useMutation({
    mutationFn: () => publishPolicy(workspaceId, draft),
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({
        queryKey: procurementKeys.all(workspaceId),
      });
    },
    onError: (err) => setError(describe(err)),
  });

  if (!capability.isLoading && !capability.granted) {
    return (
      <div className="p-6">
        <CapabilityLockCard canChangePlan={workspace?.role === "ADMIN"} />
      </div>
    );
  }

  const field = (
    label: string,
    key: keyof PolicyDraft,
    hint: string,
    numeric = true,
  ) => (
    <label className="block space-y-1">
      <span className={FIELD_LABEL}>{label}</span>
      <input
        className={INPUT}
        type={numeric ? "number" : "text"}
        value={String(draft[key])}
        onChange={(event) =>
          setDraft((value) => ({
            ...value,
            [key]: numeric ? Number(event.target.value) : event.target.value,
          }))
        }
      />
      <span className={HINT}>{hint}</span>
    </label>
  );

  // HARDENING-T1:D26. A failed request rendered as a blank or permanent spinner.
  if (policiesQuery.isError) {
    return (
      <ErrorState
        title="Tolerance policies could not be loaded"
        description={errorMessage(policiesQuery.error, "The server did not return tolerance policies. Check your connection and try again.")}
        onRetry={() => void policiesQuery.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4 p-6">
      <h1 className={PAGE_TITLE}>Matching tolerances</h1>

      {current ? (
        <p className={HINT}>
          Version {current.version} is in force, published{" "}
          {formatTimestamp(current.published_at)}. Published versions cannot be
          edited — publishing writes the next version and every case scored
          afterwards uses it.
        </p>
      ) : (
        <p className={HINT}>
          Nothing published yet, so every difference counts as a variance.
          Publish a policy to allow for rounding and agreed over-delivery.
        </p>
      )}

      <section className={`${SURFACE} grid gap-4 p-6 sm:grid-cols-2`}>
        <h2 className={`${SECTION_TITLE} sm:col-span-2`}>Next version</h2>
        {field(
          "Price slack (micros)",
          "price_tolerance_micros",
          "Absolute allowance per line. Applied together with the percentage below — a line passes if it satisfies either.",
        )}
        {field(
          "Price slack (basis points)",
          "price_tolerance_bps",
          "100 bps is 1%. Relative to the purchase order price.",
        )}
        {field(
          "Quantity slack",
          "quantity_tolerance",
          "In the line's own unit. Over-delivery allowances are written in units, not percentages.",
          false,
        )}
        {field(
          "Pairing ceiling",
          "max_pair_cost",
          "0–1,000,000. Lines costing more than this to pair are left unpaired and reported as not ordered or not invoiced rather than matched to something unrelated.",
        )}
        {field(
          "Candidate window (days)",
          "candidate_window_days",
          "How far either side of an invoice to look for its purchase order when no PO number is printed.",
        )}
      </section>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      <div className="flex gap-2">
        <button
          type="button"
          className={BUTTON_SECONDARY}
          disabled={preview.isPending}
          onClick={() => preview.mutate()}
        >
          Preview last 30 days
        </button>
        <button
          type="button"
          className={BUTTON_PRIMARY}
          disabled={publish.isPending}
          onClick={() => publish.mutate()}
        >
          Publish version {(current?.version ?? 0) + 1}
        </button>
      </div>

      {preview.data ? (
        <section className={`${SURFACE} space-y-2 p-6`}>
          <h2 className={SECTION_TITLE}>
            Across {preview.data.cases_considered} cases in the last{" "}
            {preview.data.window_days} days
          </h2>
          <p className="text-sm">
            {preview.data.exceptions_today} exceptions today →{" "}
            <strong>{preview.data.exceptions_under_draft}</strong> under this
            draft. {preview.data.lines_that_would_clear} lines would clear;{" "}
            {preview.data.lines_that_would_flag} would newly flag.
          </p>
          <p className={HINT}>{preview.data.caveat}</p>
        </section>
      ) : null}
    </div>
  );
};

export default TolerancePolicyEditor;
