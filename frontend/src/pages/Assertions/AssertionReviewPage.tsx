import React from "react";
import { Loader2 } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { HINT, PAGE_TITLE } from "@/components/ui/primitives";
import AssertionReviewQueue from "./AssertionReviewQueue";

/**
 * ARCH36-S1:assertion-review-page — the route for ARCH-33's review queue.
 *
 * `AssertionReviewQueue` shipped in ARCH-33 as a prop-driven component and was
 * never routed or imported, so a document held for a reviewer's verdict could
 * not be resolved from the UI and its workflow stayed paused.
 *
 * This wrapper resolves the workspace and the capability from the same hooks
 * every other capability-gated page uses, so the gate cannot be forgotten at
 * the route. The queue component itself is unchanged; ARCH-40 folds it into
 * the unified review hub as a tab.
 */
const AssertionReviewPage: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const capability = useCapabilityAccess(organizationId, CAPABILITY.semanticAssertions);

  if (!workspaceId || capability.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading&hellip;
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-1">
        <h1 className={PAGE_TITLE}>Clause assertions</h1>
        <p className={HINT}>
          Requirement checks the engine was not confident enough to decide.
          Your verdict resumes the workflow and teaches the calibrator.
        </p>
      </header>
      <AssertionReviewQueue
        workspaceId={workspaceId}
        hasCapability={capability.granted}
        canChangePlan={workspace?.role === "ADMIN"}
      />
    </div>
  );
};

export default AssertionReviewPage;
