import React, { useId, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Sparkles } from "lucide-react";

import { MandateEditor, type EditorStart } from "@/components/helm/MandateEditor";
import { errorMessage } from "@/services/api/errors";
import { draftHelmMandate, previewHelmMandate, requestHelmBacktest } from "@/services/api/helm";
import { helmPath } from "@/routes/tenantPaths";
import type { DraftCard, HelmDraft, HelmMandate, LibraryTemplate } from "@/types/helm";

/**
 * "Say it in a sentence" (brief §5.3). The model only proposes which library mandate the sentence
 * means and the values it states; the server checks it with the same validator as the gallery and
 * reads it back. The person sees that readback, can change any value, pick a "did you mean"
 * instead, replay it in the Time machine first, or add it (in Propose mode). When the model is
 * missing or unsure, the gallery's closest mandates are offered instead, with nothing lost.
 */

const EXAMPLES = [
  "Never pay a supplier more than the agreed price",
  "Keep at least ₹5 lakh in the bank",
  "Remind customers who are late on day 3, 10 and 20",
];

const fromDraft = (result: HelmDraft): EditorStart | null => {
  const draft = result.draft;
  if (result.source !== "model" || !draft || !result.template || !draft.spec) {
    return null;
  }
  const unstated = draft.unstated ?? [];
  const blocking = result.unknown_counterparties.map(
    (name) =>
      `Helm has not seen a supplier or customer called "${name}" in this book. Upload one of their documents, or say it without them.`,
  );
  // The server put its own words for these first; the editor says them once, in its words.
  const own = draft.problems.slice(result.unknown_counterparties.length + unstated.length);
  return { template: result.template.template, title: result.template.title, preview: { ...draft, problems: own }, unstated, blocking };
};

export const MandateComposer: React.FC<{
  readonly organizationId: string;
  readonly orgSlug: string;
  readonly bookId: string;
  readonly library: readonly LibraryTemplate[];
  /** The plan's backtest months (0: none, e.g. a read-only preview book). */
  readonly backtestMonths: number;
  readonly onSaved: (mandate: HelmMandate) => void;
}> = ({ organizationId, orgSlug, bookId, library, backtestMonths, onSaved }) => {
  const navigate = useNavigate();
  const fieldId = useId();
  const [sentence, setSentence] = useState("");
  const [result, setResult] = useState<HelmDraft | null>(null);
  const [editing, setEditing] = useState<EditorStart | null>(null);

  const draft = useMutation({
    mutationFn: (text: string) => draftHelmMandate(organizationId, bookId, text),
    onSuccess: (answer) => {
      setResult(answer);
      setEditing(fromDraft(answer));
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not read that sentence.")),
  });

  const pick = useMutation({
    mutationFn: async (card: DraftCard): Promise<EditorStart> => {
      const preview = await previewHelmMandate(organizationId, bookId, { template: card.template });
      const required = library.find((t) => t.key === card.template)?.required ?? [];
      return { template: card.template, title: card.title, preview, unstated: required };
    },
    onSuccess: (start) => setEditing(start),
    onError: (error) => toast.error(errorMessage(error, "Helm could not open that mandate.")),
  });

  const backtest = useMutation({
    mutationFn: async ({ template, months, params, scope }: { template: string; months: number; params: Record<string, unknown>; scope: Record<string, unknown> }) =>
      requestHelmBacktest(organizationId, { book_id: bookId, months, mandates: [{ template, params, scope }] }),
    onSuccess: (queued) =>
      navigate(`${helmPath(orgSlug, "time-machine")}?book=${encodeURIComponent(bookId)}&backtest=${encodeURIComponent(queued.id)}`),
    onError: (error) => toast.error(errorMessage(error, "Helm could not start the backtest.")),
  });

  const reset = (): void => {
    setResult(null);
    setEditing(null);
    setSentence("");
  };

  const alternatives = result?.alternatives ?? [];
  return (
    <section aria-labelledby={`${fieldId}-title`} className="space-y-3">
      <form
        className="helm-panel helm-panel-strong helm-command helm-enter p-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (sentence.trim()) {
            draft.mutate(sentence.trim());
          }
        }}
      >
        <h2 id={`${fieldId}-title`} className="helm-label">
          Say it in a sentence
        </h2>
        <label htmlFor={fieldId} className="mt-1 block text-sm">
          What should always be true in your business?
        </label>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            id={fieldId}
            className="helm-input helm-command-input min-w-0 flex-1"
            maxLength={500}
            value={sentence}
            placeholder={EXAMPLES[0]}
            onChange={(event) => setSentence(event.target.value)}
          />
          <button type="submit" className="helm-btn helm-btn-primary helm-btn-hero shrink-0" disabled={draft.isPending || !sentence.trim()}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {draft.isPending ? "Reading…" : "Draft it"}
          </button>
        </div>
        <p className="helm-muted mt-2 text-xs">
          For example: {EXAMPLES.map((example, i) => (
            <React.Fragment key={example}>
              {i > 0 ? " · " : null}
              <button type="button" className="helm-accent hover:underline" onClick={() => setSentence(example)}>
                {example}
              </button>
            </React.Fragment>
          ))}
          . AI reads the sentence; Helm checks it and shows it back before anything is saved.
        </p>
      </form>

      {result ? (
        <div className="space-y-3" data-testid="helm-draft">
          {result.source === "gallery" ? (
            <p role="status" className="helm-panel p-3 text-sm">
              {result.message}
            </p>
          ) : result.template ? (
            <p role="status" className="helm-muted text-sm">
              Helm read it as <span className="font-semibold text-inherit">{result.template.title}</span>.
            </p>
          ) : null}

          {!editing && result.source === "model" && result.template && result.draft && !result.draft.spec ? (
            // The model's draft did not pass the validator (a value or a fact Helm cannot use): say
            // why, and let the person build it in the editor instead of leaving them with nothing.
            <div className="helm-panel space-y-2 p-3" data-testid="helm-draft-problems">
              <ul role="alert" className="list-disc space-y-1 pl-5 text-sm">
                {(result.draft.problems.length > 0
                  ? result.draft.problems
                  : ["Helm could not turn that sentence into a mandate it can check."]
                ).map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
              <button
                type="button"
                className="helm-btn"
                disabled={pick.isPending}
                onClick={() =>
                  result.template &&
                  pick.mutate({ template: result.template.template, title: result.template.title, summary: "", group: "" })
                }
              >
                Build {result.template.title.toLowerCase()} in the editor
              </button>
            </div>
          ) : null}

          {editing ? (
            <MandateEditor
              key={`${editing.template}:${result.sentence}`}
              organizationId={organizationId}
              bookId={bookId}
              start={editing}
              onCancel={() => setEditing(null)}
              onDone={(saved) => {
                reset();
                onSaved(saved);
              }}
              backtest={
                backtestMonths
                  ? {
                      months: backtestMonths,
                      pending: backtest.isPending,
                      run: ({ params, scope }) =>
                        backtest.mutate({ template: editing.template, months: backtestMonths, params, scope }),
                    }
                  : undefined
              }
            />
          ) : null}

          {alternatives.length > 0 ? (
            <div>
              <p className="helm-label mb-1.5">{result.source === "gallery" ? "Closest in the gallery" : "Did you mean"}</p>
              <ul className="flex flex-wrap gap-2">
                {alternatives.map((card) => (
                  <li key={card.template}>
                    <button
                      type="button"
                      className="helm-btn"
                      disabled={pick.isPending}
                      title={card.summary}
                      onClick={() => pick.mutate(card)}
                    >
                      {card.title}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : result.source === "gallery" ? (
            <p className="helm-muted text-sm">Pick one from the library below.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};

export default MandateComposer;
