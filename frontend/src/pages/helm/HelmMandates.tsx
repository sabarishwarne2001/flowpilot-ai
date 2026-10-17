import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, Lock, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";

import { EnterpriseNote } from "@/components/helm/EnterpriseNote";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { MandateComposer } from "@/components/helm/MandateComposer";
import { MandateEditor, type EditorStart } from "@/components/helm/MandateEditor";
import { ReadbackSentence } from "@/components/helm/ReadbackSentence";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { LoadFailed } from "@/components/common/LoadFailed";
import { errorMessage } from "@/services/api/errors";
import {
  createHelmMandate,
  getHelmLibrary,
  helmKeys,
  listHelmMandates,
  previewHelmMandate,
  setHelmMandateStatus,
} from "@/services/api/helm";
import type { HelmMandate, LibraryTemplate } from "@/types/helm";

const STATUS_WORDS: Record<HelmMandate["status"], string> = {
  DRAFT: "Draft",
  ACTIVE: "Active",
  PAUSED: "Paused",
  ARCHIVED: "Archived",
};

const MODE_WORDS: Record<string, string> = { watch: "Watch", propose: "Propose", act: "Act" };

const modeOf = (mandate: HelmMandate): string =>
  MODE_WORDS[String((mandate.spec?.autonomy as { mode?: string } | undefined)?.mode ?? "propose")] ?? "Propose";

const LawLinks: React.FC<{ readonly links: { title: string; url: string }[]; readonly disclaimer: string | null }> = ({
  links,
  disclaimer,
}) =>
  links.length > 0 || disclaimer ? (
    <div className="helm-muted mt-2 space-y-1 text-xs">
      {links.map((link) => (
        <a key={link.url} href={link.url} target="_blank" rel="noreferrer noopener" className="helm-accent mr-3 inline-flex items-center gap-1 hover:underline">
          {link.title}
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      ))}
      {disclaimer ? <p>{disclaimer}</p> : null}
    </div>
  ) : null;

export const HelmMandates: React.FC = () => {
  const { organizationId, orgSlug, overview, canDecide } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const library = useQuery({ queryKey: helmKeys.library(organizationId), queryFn: () => getHelmLibrary(organizationId) });
  const mandates = useQuery({
    queryKey: helmKeys.mandates(organizationId, book.id),
    queryFn: () => listHelmMandates(organizationId, book.id),
  });
  const writable = canDecide && !book.read_only;
  const [editingId, setEditingId] = useState<string | null>(null);
  const [fromGallery, setFromGallery] = useState<EditorStart | null>(null);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
    void queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
  };

  // A template whose value has no default ("Keep cash above a floor") opens the editor: the gallery's
  // value is an example, and Helm saves the person's own.
  const open = useMutation({
    mutationFn: async (template: LibraryTemplate): Promise<EditorStart> => ({
      template: template.key,
      title: template.title,
      preview: await previewHelmMandate(organizationId, book.id, { template: template.key }),
      unstated: template.required,
    }),
    onSuccess: (start) => setFromGallery(start),
    onError: (error) => toast.error(errorMessage(error, "Helm could not open this mandate.")),
  });

  const add = useMutation({
    mutationFn: (template: LibraryTemplate) =>
      createHelmMandate(organizationId, book.id, { template: template.key, activate: true }),
    onSuccess: (mandate) => {
      toast.success(`${mandate.title} is active, in Propose mode: Helm suggests, a person decides.`);
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not add this mandate.")),
  });
  const status = useMutation({
    mutationFn: ({ mandate, verb }: { mandate: HelmMandate; verb: "activate" | "pause" | "archive" }) =>
      setHelmMandateStatus(organizationId, book.id, mandate.id, verb),
    onSuccess: (mandate) => {
      toast.success(`${mandate.title}: ${STATUS_WORDS[mandate.status].toLowerCase()}.`);
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not change this mandate.")),
  });

  const inUse = useMemo(() => new Set((mandates.data ?? []).map((m) => m.template)), [mandates.data]);
  // A mandate whose template this plan no longer carries (a custom condition after a move from
  // Enterprise) keeps running until paused or archived, but cannot be edited or switched back on.
  const locked = useMemo(
    () => new Set((library.data?.templates ?? []).filter((t) => !t.available).map((t) => t.key)),
    [library.data],
  );
  const groups = library.data?.groups ?? [];

  if (library.isError || mandates.isError) {
    return <LoadFailed what="the mandates" onRetry={() => void Promise.all([library.refetch(), mandates.refetch()])} />;
  }

  const limit = overview.limits.mandates;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <HelmPageHeader
        eyebrow={book.name}
        title="Mandates"
        description="A mandate is something that must always be true. Helm watches each one, proposes what keeps it true and checks every action against all of them. New mandates start in Propose mode."
      >
        {limit !== null ? (
          <div className="mt-3 flex max-w-sm items-center gap-3">
            <div className="helm-meter-track h-1.5 flex-1 overflow-hidden rounded-full" aria-hidden="true">
              <div
                className="helm-meter-fill h-full rounded-full"
                style={{ width: `${Math.min(100, Math.round((overview.limits.active_mandates / Math.max(1, limit)) * 100))}%` }}
              />
            </div>
            <p className="helm-muted shrink-0 text-xs">
              <span className="helm-figure">{overview.limits.active_mandates}</span> of{" "}
              <span className="helm-figure">{limit}</span> active mandates on your plan.
            </p>
          </div>
        ) : null}
      </HelmPageHeader>

      {writable ? (
        <MandateComposer
          organizationId={organizationId}
          orgSlug={orgSlug}
          bookId={book.id}
          library={library.data?.templates ?? []}
          backtestMonths={overview.limits.backtest_months}
          onSaved={refresh}
        />
      ) : null}

      <section aria-labelledby="helm-mandates-yours">
        <h2 id="helm-mandates-yours" className="helm-label mb-2">
          Yours
        </h2>
        {mandates.isLoading ? (
          <p className="helm-muted text-sm" role="status">
            Loading…
          </p>
        ) : (mandates.data ?? []).length === 0 ? (
          <p className="helm-panel p-4 text-sm">None yet. Pick from the library below.</p>
        ) : (
          <ul className="space-y-2">
            {(mandates.data ?? []).map((mandate) =>
              editingId === mandate.id ? (
                <li key={mandate.id}>
                  <MandateEditor
                    organizationId={organizationId}
                    bookId={book.id}
                    mandate={mandate}
                    start={{
                      template: mandate.template,
                      title: mandate.title,
                      preview: { valid: true, problems: [], spec: mandate.spec, readback: mandate.readback },
                    }}
                    onCancel={() => setEditingId(null)}
                    onDone={() => {
                      setEditingId(null);
                      refresh();
                    }}
                  />
                </li>
              ) : (
              <li key={mandate.id} className="helm-panel p-4" data-testid="helm-mandate" data-status={mandate.status}>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                      {mandate.title}
                      <span className="helm-chip">{STATUS_WORDS[mandate.status]}</span>
                      <span className="helm-chip">{modeOf(mandate)}</span>
                      <span className="helm-muted text-xs font-normal">version {mandate.version}</span>
                    </p>
                    <ReadbackSentence readback={mandate.readback} className="mt-1.5 text-sm" />
                    <LawLinks links={mandate.law_links} disclaimer={mandate.disclaimer} />
                    {locked.has(mandate.template) ? (
                      <EnterpriseNote
                        what="Changing or switching on this mandate is"
                        className="helm-muted mt-2 text-xs"
                      />
                    ) : null}
                  </div>
                  {writable ? (
                    <div className="flex shrink-0 flex-wrap gap-2">
                      {mandate.status !== "ARCHIVED" && !locked.has(mandate.template) ? (
                        <button
                          type="button"
                          className="helm-btn"
                          onClick={() => setEditingId(mandate.id)}
                          aria-label={`Edit ${mandate.title}`}
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                          Edit
                        </button>
                      ) : null}
                      {mandate.status === "ACTIVE" ? (
                        <button
                          type="button"
                          className="helm-btn"
                          disabled={status.isPending}
                          onClick={() => status.mutate({ mandate, verb: "pause" })}
                        >
                          <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                          Pause
                        </button>
                      ) : locked.has(mandate.template) ? null : (
                        <button
                          type="button"
                          className="helm-btn"
                          disabled={status.isPending}
                          onClick={() => status.mutate({ mandate, verb: "activate" })}
                        >
                          <Play className="h-3.5 w-3.5" aria-hidden="true" />
                          Activate
                        </button>
                      )}
                      <button
                        type="button"
                        className="helm-btn"
                        disabled={status.isPending}
                        onClick={() => status.mutate({ mandate, verb: "archive" })}
                        aria-label={`Archive ${mandate.title}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        Archive
                      </button>
                    </div>
                  ) : null}
                </div>
              </li>
              ),
            )}
          </ul>
        )}
      </section>

      <section aria-labelledby="helm-library">
        <h2 id="helm-library" className="helm-label mb-2">
          Library
        </h2>
        {fromGallery ? (
          <div className="mb-4">
            <MandateEditor
              key={fromGallery.template}
              organizationId={organizationId}
              bookId={book.id}
              start={fromGallery}
              onCancel={() => setFromGallery(null)}
              onDone={() => {
                setFromGallery(null);
                refresh();
              }}
            />
          </div>
        ) : null}
        {library.isLoading ? (
          <p className="helm-muted text-sm" role="status">
            Loading…
          </p>
        ) : (
          <div className="space-y-5">
            {groups.map((group) => {
              const templates = (library.data?.templates ?? [])
                .filter((t) => t.group === group)
                .sort((a, b) => a.order - b.order);
              if (templates.length === 0) {
                return null;
              }
              return (
                <div key={group}>
                  <h3 className="mb-2 text-sm font-semibold">{group}</h3>
                  <ul className="grid gap-3 md:grid-cols-2">
                    {templates.map((template) => {
                      // A repeatable template (the custom condition) takes one mandate per rule.
                      const another = template.repeatable && inUse.has(template.key);
                      const used = inUse.has(template.key) && !template.repeatable;
                      return (
                        <li key={template.key} className="helm-panel helm-panel-lift flex flex-col p-4" data-testid="helm-template">
                          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                            {template.title}
                            {template.plan === "enterprise" ? (
                              <span className="helm-chip">
                                {!template.available ? <Lock className="h-3 w-3" aria-hidden="true" /> : null}
                                Enterprise
                              </span>
                            ) : null}
                          </p>
                          <ReadbackSentence readback={template.readback} className="mt-1.5 text-sm" />
                          <p className="helm-muted mt-1 text-xs">{template.summary}</p>
                          <LawLinks links={template.law_links} disclaimer={template.disclaimer} />
                          {!template.available ? <EnterpriseNote what="It is" className="helm-muted mt-2 text-xs" /> : null}
                          {writable ? (
                            <div className="mt-auto pt-3">
                              <button
                                type="button"
                                className="helm-btn"
                                disabled={used || !template.available || add.isPending || open.isPending}
                                onClick={() => (template.required.length > 0 ? open.mutate(template) : add.mutate(template))}
                                aria-label={
                                  used
                                    ? `${template.title}: already yours`
                                    : another
                                      ? `Add another ${template.title.toLowerCase()}`
                                      : `Add ${template.title}`
                                }
                              >
                                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                                {used
                                  ? "Already yours"
                                  : !template.available
                                    ? "On Enterprise"
                                    : another
                                      ? "Add another"
                                      : template.required.length > 0
                                        ? "Set it up"
                                        : "Add in Propose mode"}
                              </button>
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
};

export default HelmMandates;
