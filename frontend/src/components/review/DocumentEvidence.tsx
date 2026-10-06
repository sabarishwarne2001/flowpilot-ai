/**
 * PHASE 4 — the page beside the decision.
 *
 * For one field under review, shows the document page with a coloured box
 * where each agent's reading is printed (agent 1, agent 2, …), and says so
 * when a reading is not printed on the page at all - often the quickest way
 * to see which agent is right. Boxes come from the line geometry the
 * extraction stored; nothing is re-read.
 */

import React, { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileSearch, Loader2 } from "lucide-react";

import {
  getDocumentEvidence,
  getDocumentPageImage,
  type EvidenceLocation,
} from "@/services/api/documentEvidence";
import { formatFieldValue, type VerificationFieldResponse } from "@/types/verification";

/** One colour per agent, in order; enough for the 2–5 agents a verification can use. */
const AGENT_COLOURS = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed"] as const;
const agentColour = (index: number): string =>
  AGENT_COLOURS[index % AGENT_COLOURS.length] ?? "#2563eb";

interface DocumentEvidenceProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  readonly fields: readonly VerificationFieldResponse[];
  readonly fieldPath: string | null;
  readonly onSelectField: (fieldPath: string) => void;
}

export const DocumentEvidence: React.FC<DocumentEvidenceProps> = ({
  workspaceId,
  workItemId,
  fields,
  fieldPath,
  onSelectField,
}) => {
  const candidates = useMemo(
    () =>
      fields.flatMap((field) =>
        field.agent_values
          .map((value) => formatFieldValue(value))
          .filter((value) => value && value !== "—" && value !== "(empty)")
          .map((value) => `${field.field_path}:${value}`),
      ),
    [fields],
  );

  const evidence = useQuery({
    queryKey: ["document-evidence", workspaceId, workItemId, candidates],
    queryFn: () => getDocumentEvidence(workspaceId, workItemId, candidates),
    enabled: Boolean(workspaceId && workItemId),
    staleTime: 60_000,
  });

  // Until the reviewer picks one, show the first field the agents actually disagree on: on a
  // calibration hold every field is listed, and the first one is rarely the one in dispute.
  const field = fields.find((f) => f.field_path === fieldPath) ?? fields.find((f) => !f.agreed) ?? fields[0];
  const readings = useMemo(
    () => (field ? field.agent_values.map((value) => formatFieldValue(value)) : []),
    [field],
  );

  const boxes = useMemo(() => {
    const out: { location: EvidenceLocation; agents: number[] }[] = [];
    for (const location of evidence.data?.locations ?? []) {
      if (!field || location.field !== field.field_path) {
        continue;
      }
      const agents = readings
        .map((reading, index) => (reading === location.value ? index : -1))
        .filter((index) => index >= 0);
      if (agents.length > 0) {
        out.push({ location, agents });
      }
    }
    return out;
  }, [evidence.data, field, readings]);

  const page = boxes[0]?.location.page ?? 1;
  const image = useQuery({
    queryKey: ["document-page", workspaceId, workItemId, page],
    queryFn: () => getDocumentPageImage(workspaceId, workItemId, page),
    enabled: Boolean(evidence.data?.renderable),
    staleTime: 300_000,
  });
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!image.data) {
      setImageUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(image.data);
    setImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image.data]);

  if (!field) {
    return null;
  }
  if (evidence.isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Finding the values on the page…
      </p>
    );
  }
  if (evidence.isError || !evidence.data?.renderable) {
    return null;
  }

  const located = new Set(boxes.flatMap((box) => box.agents));

  return (
    <section aria-label="Where the values are printed" className="rounded-md border border-border bg-card p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <FileSearch className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-xs font-medium">On the page</span>
        <select
          value={field.field_path}
          onChange={(event) => onSelectField(event.target.value)}
          aria-label="Field to show on the page"
          className="ml-auto rounded border border-border bg-background px-2 py-1 font-mono text-xs"
        >
          {fields.map((f) => (
            <option key={f.field_path} value={f.field_path}>
              {f.field_path}
            </option>
          ))}
        </select>
      </div>

      <ul className="mb-2 flex flex-wrap gap-2 text-[11px]">
        {readings.map((reading, index) => (
          <li key={index} className="flex items-center gap-1.5 rounded border border-border px-1.5 py-0.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: agentColour(index) }} aria-hidden />
            <span>Agent {index + 1}:</span>
            <span className="font-mono">{reading}</span>
            {!located.has(index) && <span className="text-muted-foreground">(not printed on the page)</span>}
          </li>
        ))}
      </ul>

      <div className="relative w-full overflow-hidden rounded border border-border bg-white">
        {imageUrl ? (
          <img src={imageUrl} alt={`Page ${page} of the document`} className="block w-full" />
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-muted-foreground">
            {image.isError ? "The page image could not be loaded." : <Loader2 className="h-4 w-4 animate-spin" />}
          </div>
        )}
        {imageUrl &&
          boxes
            .filter((box) => box.location.page === page)
            .map((box, index) => (
              <span
                key={index}
                className="pointer-events-none absolute rounded-sm border-2"
                title={`Agent ${box.agents.map((a) => a + 1).join(", ")}: ${box.location.value}`}
                style={{
                  left: `${box.location.x0 * 100}%`,
                  top: `${box.location.y0 * 100}%`,
                  width: `${(box.location.x1 - box.location.x0) * 100}%`,
                  height: `${(box.location.y1 - box.location.y0) * 100}%`,
                  borderColor: agentColour(box.agents[0] ?? 0),
                  backgroundColor: `${agentColour(box.agents[0] ?? 0)}26`,
                }}
              />
            ))}
      </div>
    </section>
  );
};

export default DocumentEvidence;
