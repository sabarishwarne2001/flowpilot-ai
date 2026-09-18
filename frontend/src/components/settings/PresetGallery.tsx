import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Info, ShieldAlert } from "lucide-react";

import {
  applyPreset,
  listPresets,
  setPresetEnabled,
  type DocumentPreset,
} from "@/services/api/ingestion";
import { ingestionKeys } from "@/services/api/queryKeys";

/**
 * ARCH-38 — the preset gallery.
 *
 * Apply, then review, then enable. Two acts, because the reviewer has to see
 * the field list, the suggested assertions and the redaction profile before a
 * pack starts shaping extraction on real documents.
 *
 * The Safe Harbor notice is rendered verbatim from the server
 * (`safe_harbor_notice`). It says identifier removal and never "HIPAA
 * compliant": that claim also requires a business associate agreement and
 * administrative safeguards, which are not software.
 */

export interface PresetGalleryProps {
  readonly workspaceId: string;
  readonly canManage: boolean;
}

const INDUSTRY_LABELS: Record<string, string> = {
  HR: "HR",
  HEALTHCARE: "Healthcare",
  LEGAL: "Legal",
  LOGISTICS: "Logistics",
  KYC: "KYC",
};

const PresetCard: React.FC<{
  preset: DocumentPreset;
  workspaceId: string;
  canManage: boolean;
}> = ({ preset, workspaceId, canManage }) => {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);

  const invalidate = (): void => {
    void queryClient.invalidateQueries({
      queryKey: ingestionKeys.presets(workspaceId),
    });
  };

  const apply = useMutation({
    mutationFn: () => applyPreset(workspaceId, preset.id),
    onSuccess: () => {
      setExpanded(true);
      invalidate();
    },
  });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      setPresetEnabled(workspaceId, preset.id, enabled),
    onSuccess: invalidate,
  });

  const fields = useMemo(() => {
    const properties = preset.schema_fields["properties"];
    if (typeof properties !== "object" || properties === null) {
      return [] as { name: string; type: string }[];
    }
    return Object.entries(properties as Record<string, { type?: string }>).map(
      ([name, definition]) => ({
        name,
        type: definition?.type ?? "string",
      }),
    );
  }, [preset.schema_fields]);

  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-medium">{preset.label}</h4>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
              {INDUSTRY_LABELS[preset.industry] ?? preset.industry}
            </span>
            {preset.enabled && (
              <span className="inline-flex items-center gap-1 rounded bg-emerald-600/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                <Check className="h-3 w-3" aria-hidden="true" />
                Enabled
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {preset.description}
          </p>
        </div>

        {canManage && !preset.applied && (
          <button
            type="button"
            onClick={() => apply.mutate()}
            disabled={apply.isPending}
            className="shrink-0 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
          >
            Apply to workspace
          </button>
        )}
        {canManage && preset.applied && (
          <button
            type="button"
            onClick={() => toggle.mutate(!preset.enabled)}
            disabled={toggle.isPending}
            className="shrink-0 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
          >
            {preset.enabled ? "Disable" : "Enable"}
          </button>
        )}
      </div>

      {preset.applied && !preset.enabled && (
        <p className="mt-2 flex items-start gap-1.5 rounded-md bg-muted/50 px-2.5 py-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          Applied but not enabled. Review the fields, assertions and redaction
          profile below, then enable it.
        </p>
      )}

      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="mt-2 text-xs font-medium text-primary hover:underline"
      >
        {expanded ? "Hide details" : `Review ${fields.length} fields`}
      </button>

      {expanded && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <div>
            <h5 className="text-xs font-medium">Extracted fields</h5>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {fields.map((field) => (
                <li
                  key={field.name}
                  className="rounded bg-muted px-1.5 py-0.5 text-[11px]"
                >
                  {field.name}
                  <span className="ml-1 text-muted-foreground">
                    {field.type}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {preset.assertions.length > 0 && (
            <div>
              <h5 className="text-xs font-medium">Suggested checks</h5>
              <ul className="mt-1 space-y-1">
                {preset.assertions.map((assertion) => (
                  <li key={assertion.sentence} className="text-xs text-muted-foreground">
                    {assertion.sentence}
                    <span className="ml-1.5 text-[10px] uppercase">
                      {assertion.severity}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preset.redaction_profile && (
            <div>
              <h5 className="text-xs font-medium">Redaction profile</h5>
              <p className="mt-1 text-xs text-muted-foreground">
                {preset.redaction_profile}
              </p>
              {preset.safe_harbor_notice && (
                <p className="mt-1.5 flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-2 text-xs">
                  <ShieldAlert
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                    aria-hidden="true"
                  />
                  <span>{preset.safe_harbor_notice}</span>
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
};

export const PresetGallery: React.FC<PresetGalleryProps> = ({
  workspaceId,
  canManage,
}) => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ingestionKeys.presets(workspaceId),
    queryFn: () => listPresets(workspaceId),
    enabled: Boolean(workspaceId),
  });

  const grouped = useMemo(() => {
    const map = new Map<string, DocumentPreset[]>();
    (data ?? []).forEach((preset) => {
      const bucket = map.get(preset.industry) ?? [];
      bucket.push(preset);
      map.set(preset.industry, bucket);
    });
    return Array.from(map.entries());
  }, [data]);

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading preset packs…</p>;
  }
  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Preset packs could not be loaded.
      </p>
    );
  }

  return (
    <section className="space-y-6">
      <div>
        <h3 className="text-sm font-medium">Document packs</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          A pack gives a document type its field list, its suggested checks and
          a redaction profile. Apply one, review it, then enable it.
        </p>
      </div>

      {grouped.map(([industry, presets]) => (
        <div key={industry}>
          <h4 className="text-xs font-medium uppercase text-muted-foreground">
            {INDUSTRY_LABELS[industry] ?? industry}
          </h4>
          <ul className="mt-2 space-y-2">
            {presets.map((preset) => (
              <PresetCard
                key={preset.id}
                preset={preset}
                workspaceId={workspaceId}
                canManage={canManage}
              />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
};

export default PresetGallery;
