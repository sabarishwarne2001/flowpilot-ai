import React, { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { EyeOff, Loader2, Lock } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { redactionPath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/client";
import { startRedaction } from "@/services/api/redaction";

/**
 * The redaction profiles the engine defines, with the words a reviewer reads.
 *
 * Keys mirror `PROFILES` in app/services/redaction/vocabulary.py.
 * verify_arch36.py fails when the two sets differ: an extra key here is a
 * button that always answers 422, and a missing one is a profile nobody can
 * start.
 */
export const REDACTION_PROFILE_LABELS: Readonly<Record<string, { label: string; hint: string }>> = {
  all_identifiers: {
    label: "All identifiers",
    hint: "Every detector the engine has",
  },
  financial: {
    label: "Financial",
    hint: "Card numbers, IBAN, SSN, PAN, GSTIN",
  },
  hipaa_safe_harbor: {
    label: "HIPAA Safe Harbor",
    hint: "Names, dates of birth, SSN, card numbers, contact details",
  },
  india_kyc: {
    label: "India KYC",
    hint: "Aadhaar, PAN, GSTIN, date of birth, contact details",
  },
};

interface StartRedactionButtonProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  /** `work_items.file_type` — the stored MIME type. */
  readonly mimeType: string;
  readonly status: string;
}

/**
 * ARCH36-S1:redaction-entry — the only way into the Redaction Studio.
 *
 * ARCH-32 shipped the studio at `redactions/:jobId` and `startRedaction` in
 * the API client, and nothing called `startRedaction`. With no job there is no
 * id, and with no id the studio cannot be opened, so the feature was
 * unreachable however the navigation was arranged.
 *
 * Shown for completed PDFs only, because the engine refuses anything else
 * (it rebuilds a PDF from rendered pages and needs extraction geometry).
 * Hidden below CONTRIBUTOR, which is what the endpoint requires. When the
 * tier lacks the capability the button renders locked rather than hidden;
 * the endpoint's capability gate is what actually refuses.
 */
const StartRedactionButton: React.FC<StartRedactionButtonProps> = ({
  workspaceId,
  workItemId,
  mimeType,
  status,
}) => {
  const navigate = useNavigate();
  const { organization, workspace, workspaceRole } = useResolvedTenant();
  const capability = useCapabilityAccess(organization.organization_id, CAPABILITY.redaction);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const start = useMutation({
    mutationFn: (profileKey: string) => startRedaction(workspaceId, workItemId, profileKey),
    onSuccess: (job) => {
      setOpen(false);
      navigate(redactionPath(organization.organization_slug, workspace.slug, job.id));
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError ? error.message : "Redaction could not be started.",
      );
    },
  });

  const isPdf = mimeType.toLowerCase() === "application/pdf";
  if (!isPdf || status !== "COMPLETED" || !isAtLeast(workspaceRole, "CONTRIBUTOR")) {
    return null;
  }

  const buttonClass =
    "inline-flex items-center rounded-lg border border-border bg-card px-3 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-50";

  if (!capability.isLoading && !capability.granted) {
    return (
      <button
        type="button"
        disabled
        title="Redaction is not included in your plan"
        className={buttonClass}
      >
        <Lock className="mr-2 h-4 w-4" aria-hidden />
        Redact
      </button>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={capability.isLoading || start.isPending}
        aria-haspopup="menu"
        aria-expanded={open}
        className={buttonClass}
      >
        {start.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <EyeOff className="mr-2 h-4 w-4" aria-hidden />
        )}
        Redact
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Redaction profile"
          className="absolute right-0 z-30 mt-2 w-72 rounded-xl border border-border bg-card p-1 shadow-lg"
        >
          <p className="px-3 py-2 text-xs text-muted-foreground">
            Choose what to detect. You review every region before anything is
            applied.
          </p>
          {Object.entries(REDACTION_PROFILE_LABELS).map(([key, profile]) => (
            <button
              key={key}
              type="button"
              role="menuitem"
              onClick={() => start.mutate(key)}
              className="flex w-full flex-col items-start rounded-lg px-3 py-2 text-left hover:bg-muted"
            >
              <span className="text-sm font-semibold">{profile.label}</span>
              <span className="text-xs text-muted-foreground">{profile.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default StartRedactionButton;
