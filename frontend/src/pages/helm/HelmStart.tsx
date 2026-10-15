import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Compass, Lock } from "lucide-react";

import { useHelm } from "@/components/helm/HelmContext";
import { helmPath, organizationBillingPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { createHelmBook, helmKeys } from "@/services/api/helm";

const FIVE_PARTS: { name: string; text: string }[] = [
  { name: "Promise ledger", text: "Every promise your documents make: what you owe, what is owed to you, agreed prices, deadlines." },
  { name: "Mandates", text: "Rules that must always be true, such as never paying above the agreed price, in plain words." },
  { name: "Planner", text: "A payment plan that keeps every mandate, the law on MSME payments and your cash floor." },
  { name: "Guard", text: "Checks every action twice, when planned and just before it runs, and certifies it." },
  { name: "Time machine", text: "Replays your own history to show what Helm would have held, before you trust it." },
];

/** Where Helm begins: start it on a workspace (Business and up), or read what preview offers. */
export const HelmStart: React.FC = () => {
  const { organizationId, orgSlug, overview, access, canDecide, role, books } = useHelm();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: (workspaceId: string) => createHelmBook(organizationId, { workspace_id: workspaceId }),
    onSuccess: async (book) => {
      await queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
      toast.success(`Helm is reading ${book.name}. It starts in Propose mode: it suggests, a person decides.`);
      navigate(`${helmPath(orgSlug, "settings")}?book=${encodeURIComponent(book.id)}`);
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not start on this workspace.")),
  });
  const candidates = overview.workspaces.filter((w) => !w.is_sample && !w.has_book);

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="helm-panel p-6">
        <Compass className="helm-accent h-7 w-7" aria-hidden="true" />
        <h1 className="mt-3 text-xl font-semibold tracking-tight">Helm keeps your business promises</h1>
        <p className="helm-muted mt-2 text-sm leading-relaxed">
          Helm reads the documents you already have and keeps what they promise. It decides with rules you can read; AI
          only helps you write a rule. It never moves money: payments go through your own bank.
        </p>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {FIVE_PARTS.map((part) => (
            <li key={part.name} className="helm-panel-raised p-3">
              <p className="text-sm font-semibold">{part.name}</p>
              <p className="helm-muted mt-0.5 text-xs">{part.text}</p>
            </li>
          ))}
        </ul>
      </div>

      {access.preview ? (
        <div className="helm-panel p-5">
          <p className="flex items-center gap-2 text-sm font-semibold">
            <Lock className="h-4 w-4" aria-hidden="true" />
            Helm preview
          </p>
          <p className="helm-muted mt-1 text-sm">
            Helm is included on the Business and Enterprise plans. On your plan you can explore Helm with a sample company
            and run one backtest a week, of up to three months, on your own documents.
          </p>
          {role === "OWNER" || role === "BILLING" ? (
            <Link to={organizationBillingPath(orgSlug)} className="helm-btn helm-btn-primary mt-3">
              See plans
            </Link>
          ) : null}
        </div>
      ) : canDecide ? (
        <section aria-labelledby="helm-start-workspaces" className="helm-panel p-5">
          <h2 id="helm-start-workspaces" className="text-sm font-semibold">
            Start Helm on a workspace
          </h2>
          <p className="helm-muted mt-1 text-xs">
            Helm keeps one book per workspace. You confirm who you are and your cash next; then choose mandates.
          </p>
          {candidates.length === 0 ? (
            <p className="mt-3 text-sm">Every workspace you can read already has Helm.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {candidates.map((workspace) => (
                <li key={workspace.id} className="helm-hairline flex flex-wrap items-center justify-between gap-2 border-t pt-2 first:border-t-0 first:pt-0">
                  <span className="text-sm font-medium">{workspace.name}</span>
                  <button
                    type="button"
                    className="helm-btn helm-btn-primary"
                    disabled={start.isPending}
                    onClick={() => start.mutate(workspace.id)}
                  >
                    Start Helm on {workspace.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : books.length === 0 ? (
        <p className="helm-panel p-5 text-sm">An organization owner or admin starts Helm. You will see its plan here once they do.</p>
      ) : null}
    </div>
  );
};

export default HelmStart;
