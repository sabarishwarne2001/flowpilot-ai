import React, { useCallback, useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Lock } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { helmPath, isHelmPath } from "@/routes/tenantPaths";
import { readLastPath, writeLastPath, writeMode, type HelmMode } from "@/components/helm/modeMemory";
import "@/styles/helm.css";

interface ModeSwitchProps {
  readonly mode: HelmMode;
  readonly organizationId: string;
  readonly orgSlug: string;
  /** Where Documents goes when no page is remembered (the workspace root). */
  readonly documentsPath: string;
}

const OPTIONS: readonly { mode: HelmMode; label: string }[] = [
  { mode: "documents", label: "Documents" },
  { mode: "helm", label: "Helm" },
];

/**
 * Documents | Helm (brief §10.1). A radio group to assistive technology: arrow keys move the choice,
 * one stop in the tab order. Shown only to owners, admins and the billing role (the caller decides).
 * Without `capability.helm` the Helm option carries a lock and opens Helm preview. Remembers the
 * last mode per browser and the last page in each mode.
 */
export const ModeSwitch: React.FC<ModeSwitchProps> = ({ mode, organizationId, orgSlug, documentsPath }) => {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const { granted, isLoading } = useCapabilityAccess(organizationId, CAPABILITY.helm);
  const refs = useRef<Record<HelmMode, HTMLButtonElement | null>>({ documents: null, helm: null });

  // Remember where this person is in the current mode.
  useEffect(() => {
    writeMode(mode);
    const here = `${pathname}${search}`;
    const ours = pathname === `/${orgSlug}` || pathname.startsWith(`/${orgSlug}/`) ||
      pathname.startsWith(`/${encodeURIComponent(orgSlug)}/`);
    if (ours && (mode === "helm") === isHelmPath(pathname)) {
      writeLastPath(organizationId, mode, here);
    }
  }, [mode, organizationId, orgSlug, pathname, search]);

  const go = useCallback(
    (next: HelmMode) => {
      if (next === mode) {
        return;
      }
      writeMode(next);
      const remembered = readLastPath(organizationId, next);
      const helmRoot = helmPath(orgSlug);
      const valid =
        remembered !== null &&
        (next === "helm" ? remembered.startsWith(helmRoot) : !isHelmPath(remembered.split("?")[0] ?? "") &&
          remembered.startsWith(`/${encodeURIComponent(orgSlug)}/`));
      navigate(valid && remembered ? remembered : next === "helm" ? helmRoot : documentsPath);
    },
    [documentsPath, mode, navigate, organizationId, orgSlug],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const next: HelmMode =
      event.key === "Home" ? "documents" : event.key === "End" ? "helm" : mode === "helm" ? "documents" : "helm";
    refs.current[next]?.focus();
    go(next);
  };

  const locked = !isLoading && !granted;

  return (
    <div role="radiogroup" aria-label="Mode" className="helm-mode-switch" onKeyDown={onKeyDown}>
      {OPTIONS.map((option) => {
        const checked = option.mode === mode;
        return (
          <button
            key={option.mode}
            ref={(node) => {
              refs.current[option.mode] = node;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            className="helm-mode-option"
            onClick={() => go(option.mode)}
            data-testid={`mode-${option.mode}`}
          >
            {option.label}
            {option.mode === "helm" && locked ? (
              <>
                <Lock className="h-3 w-3" aria-hidden="true" />
                <span className="sr-only"> (preview on your plan)</span>
              </>
            ) : null}
          </button>
        );
      })}
    </div>
  );
};

export default ModeSwitch;
