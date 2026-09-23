/**
 * ARCH41-S3:provenance-chip — what extraction memory did to THIS document.
 *
 * Renders nothing without the capability, and nothing for a document memory
 * never saw: provenance that says "no provenance" on every document of a plan
 * that does not include the feature is noise, not transparency.
 */
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Brain } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { HINT, SURFACE_INSET } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { extractionMemoryKeys, getWorkItemMemory } from "@/services/api/extractionMemory";

export const ExtractionMemoryProvenance: React.FC<{ readonly workItemId: string }> = ({ workItemId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.extractionMemory);
  const [open, setOpen] = useState<string | null>(null);

  const query = useQuery({
    queryKey: extractionMemoryKeys.workItem(workspaceId, workItemId),
    queryFn: () => getWorkItemMemory(workspaceId, workItemId),
    enabled: Boolean(workspaceId && workItemId && capability.granted),
    staleTime: 60_000,
  });

  const memory = query.data;
  if (!capability.granted || !memory || !memory.applied) {
    return null;
  }

  return (
    <section className={`${SURFACE_INSET} space-y-2 p-3`} aria-label="Extraction memory">
      <p className="flex items-center gap-2 text-sm font-medium">
        <Brain className="h-4 w-4 text-primary" aria-hidden />
        {memory.headline}
      </p>
      {memory.fields.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {memory.fields.map((field) => (
            <li key={field.field_path}>
              <button
                type="button"
                className="rounded-full border border-border bg-background px-2.5 py-1 text-xs hover:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                aria-expanded={open === field.field_path}
                title={field.sentence}
                onClick={() => setOpen((current) => (current === field.field_path ? null : field.field_path))}
              >
                <span className="font-mono">{field.field_path}</span>
                <span className="text-muted-foreground"> · {field.sentence}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {open ? (
        <p className={HINT}>
          {(() => {
            const field = memory.fields.find((f) => f.field_path === open);
            if (!field) {return null;}
            const anchor = field.anchor_state
              ? ` A layout rule for this field is ${field.anchor_state.toLowerCase()}.`
              : "";
            return `${field.corrections} correction(s) and ${field.confirmations} confirmation(s) from reviewers on ${memory.layout_documents} document(s) of this ${memory.document_type ?? ""} layout.${anchor}`;
          })()}
        </p>
      ) : null}
    </section>
  );
};

export default ExtractionMemoryProvenance;
