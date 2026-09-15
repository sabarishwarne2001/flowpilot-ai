import React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FlaskConical, Loader2 } from "lucide-react";

import {
  previewAssertion,
  saveAssertion,
  simulateAssertion,
} from "@/services/api/assertions";
import { ApiError } from "@/services/api/errors";
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  FIELD_LABEL,
  HINT,
  INPUT,
  SECTION_TITLE,
  SURFACE,
  SURFACE_INSET,
} from "@/components/ui/primitives";
import type {
  AssertionDefinition,
  AssertionPreview,
  AssertionSimulation,
} from "@/types/assertions";

/**
 * ARCH-33 §4.6 — the "Check a clause" block in the rule builder.
 *
 * WHY THE COMPILED FORM IS FETCHED AND NOT DERIVED
 * ================================================
 *
 * The obvious implementation parses the sentence in the browser and shows the
 * result instantly. It would be wrong within a week: the console's parser and
 * `app/services/assertions/compiler.py` would drift, and the line an
 * administrator read before saving would stop being the check that runs.
 *
 * §4.2's whole claim is that "the rule builder shows the compiled form before
 * the rule can be saved". That is only true if the form shown is the one the
 * engine compiled. So every keystroke debounces into `/assertions/preview`,
 * which runs the real compiler, and this component renders what comes back.
 *
 * WHY THE SLIDER IS INTEGER PERCENT
 * =================================
 *
 * The backend's threshold is `numeric(5,4)` and the wire type is a string.
 * Keeping a float in component state and formatting it on submit is how
 * 0.9499999999999999 gets posted. The slider holds 95; exactly one
 * conversion happens, in `services/api/assertions.ts`.
 *
 * WHAT THE SENTENCE UNDER THE SLIDER SAYS, AND WHO WROTE IT
 * =========================================================
 *
 * `preview.consequence` is assembled by the backend from this tenant's own
 * reviewed documents. It is rendered verbatim and never reworded here: a
 * console that paraphrased "3 in 100" into "very few" would be making a claim
 * the measurement does not support.
 */

const DEBOUNCE_MS = 400;

/**
 * §4.6's wording, verbatim and in one piece.
 *
 * Inlined in the JSX it would be wrapped across two lines by any formatter,
 * and the gate that asserts this console actually renders the notice would
 * then be asserting against a string the file no longer contains as one
 * substring. A constant is the only form where "the notice is rendered" is
 * checkable without checking the formatter.
 */
const LLM_NOTICE =
  "This will be checked by the AI model and billed as assistant usage.";

export interface ClauseAssertionBlockProps {
  readonly workspaceId: string;
  readonly ruleId: string;
  readonly nodeKey: string;
  readonly existing?: AssertionDefinition | null | undefined;
  /** Documents the author can try the rule against, before saving. */
  readonly testableWorkItems?: ReadonlyArray<{
    readonly id: string;
    readonly name: string;
  }>;
  readonly onSaved?: ((definition: AssertionDefinition) => void) | undefined;
  readonly onCancel?: (() => void) | undefined;
}

export const ClauseAssertionBlock: React.FC<ClauseAssertionBlockProps> = ({
  workspaceId,
  ruleId,
  nodeKey,
  existing = null,
  testableWorkItems = [],
  onSaved,
  onCancel,
}) => {
  const [sentence, setSentence] = React.useState(existing?.sentence ?? "");
  const [debounced, setDebounced] = React.useState(sentence);
  const [percent, setPercent] = React.useState(() =>
    existing ? Math.round(Number(existing.threshold) * 100) : 95,
  );
  const [acknowledged, setAcknowledged] = React.useState(
    Boolean(existing?.llm_acknowledged_by),
  );
  const [testWorkItemId, setTestWorkItemId] = React.useState(
    testableWorkItems[0]?.id ?? "",
  );
  const [simulation, setSimulation] = React.useState<AssertionSimulation | null>(
    null,
  );
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(sentence), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [sentence]);

  const preview = useQuery<AssertionPreview>({
    queryKey: ["assertion-preview", workspaceId, debounced, percent],
    queryFn: () => previewAssertion(workspaceId, debounced, percent),
    enabled: debounced.trim().length > 0,
    retry: false,
  });

  // A compile failure is a 400 with a message written for a human; the
  // sentence is too long, or empty. Rendering it next to the field is the
  // whole point of compiling at save time.
  const previewError =
    preview.error instanceof ApiError ? preview.error.message : null;

  const needsTick = preview.data?.requires_acknowledgement ?? false;
  const blocked = needsTick && !acknowledged;

  const save = useMutation({
    mutationFn: () =>
      saveAssertion(workspaceId, ruleId, nodeKey, {
        sentence,
        thresholdPercent: percent,
        acknowledgeLlm: acknowledged,
      }),
    onSuccess: (definition) => {
      setError(null);
      onSaved?.(definition);
    },
    onError: (exc: unknown) => {
      setError(
        exc instanceof ApiError ? exc.message : "The step could not be saved.",
      );
    },
  });

  const test = useMutation({
    mutationFn: () =>
      simulateAssertion(workspaceId, {
        workItemId: testWorkItemId,
        sentence,
        thresholdPercent: percent,
      }),
    onSuccess: (result) => {
      setError(null);
      setSimulation(result);
    },
    onError: (exc: unknown) => {
      setError(
        exc instanceof ApiError
          ? exc.message
          : "The document could not be tested.",
      );
    },
  });

  return (
    <section
      className={`${SURFACE} space-y-4 p-5`}
      aria-labelledby="clause-assertion-heading"
    >
      <h3 id="clause-assertion-heading" className={SECTION_TITLE}>
        Check a clause
      </h3>

      <div className="space-y-1.5">
        <label className={FIELD_LABEL} htmlFor="assertion-sentence">
          Assert that
        </label>
        <input
          id="assertion-sentence"
          className={INPUT}
          value={sentence}
          maxLength={400}
          placeholder="payment terms do not exceed Net 30"
          onChange={(event) => {
            setSentence(event.target.value);
            setSimulation(null);
          }}
        />
        {previewError ? (
          <p className="text-xs text-destructive">{previewError}</p>
        ) : null}
      </div>

      {/* "Understood as:" — the compiled plan, in plain words, from the
          server's own compiler. */}
      <div className={`${SURFACE_INSET} flex flex-wrap items-center gap-2 p-3`}>
        <span className={HINT}>Understood as:</span>
        <span className="font-mono text-sm text-foreground">
          {preview.isFetching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            preview.data?.understood_as ?? "—"
          )}
        </span>
        {preview.data ? (
          <span className={`${HINT} ml-auto`}>
            {preview.data.evaluation_mode === "LLM"
              ? "Checked by the AI model"
              : "Checked by rules, no AI"}
          </span>
        ) : null}
      </div>

      {preview.data?.notes?.length ? (
        <ul className={`${HINT} list-disc space-y-0.5 pl-5`}>
          {preview.data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {/* The LLM notice and its required tick. §4.2: an untypeable sentence is
          saved only as an explicitly AI-evaluated assertion, labelled as such.
          `ck_ad_llm_acknowledged` refuses the row without it, so this is not
          the only guard — it is the one that explains itself. */}
      {needsTick ? (
        <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
              aria-hidden
            />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                {LLM_NOTICE}
              </p>
              {preview.data?.reason ? (
                <p className={HINT}>{preview.data.reason}</p>
              ) : null}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            I understand this step uses the AI model.
          </label>
        </div>
      ) : null}

      {/* The confidence slider. Never a bare percentage: the line under it is
          what turns a number into a decision, and it is measured on this
          tenant's own reviewed documents. */}
      <div className="space-y-1.5">
        <label className={FIELD_LABEL} htmlFor="assertion-threshold">
          Continue automatically when confidence is at least {percent}%
        </label>
        <input
          id="assertion-threshold"
          type="range"
          className="w-full accent-primary"
          min={51}
          max={99}
          step={1}
          value={percent}
          onChange={(event) => setPercent(Number(event.target.value))}
        />
        <p className={HINT}>{preview.data?.consequence ?? "\u00a0"}</p>
      </div>

      {/* "Test on a document" — runs the real retrieval and the real parser,
          starts no execution and writes nothing. */}
      {testableWorkItems.length > 0 ? (
        <div className={`${SURFACE_INSET} space-y-3 p-3`}>
          <div className="flex flex-wrap items-center gap-2">
            <FlaskConical className="h-4 w-4 text-muted-foreground" aria-hidden />
            <select
              className={INPUT}
              style={{ maxWidth: "20rem" }}
              value={testWorkItemId}
              onChange={(event) => setTestWorkItemId(event.target.value)}
            >
              {testableWorkItems.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={!testWorkItemId || !sentence.trim() || test.isPending}
              onClick={() => test.mutate()}
            >
              {test.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              Test on a document
            </button>
          </div>

          {simulation ? (
            <div className="space-y-2 text-sm">
              <p className="text-foreground">
                <span className="font-semibold">{simulation.verdict}</span>
                {simulation.extracted_value?.value ? (
                  <>
                    {" — read "}
                    <span className="font-mono">
                      {simulation.extracted_value.value}
                      {simulation.extracted_value.unit
                        ? ` ${simulation.extracted_value.unit}`
                        : ""}
                    </span>
                  </>
                ) : null}
                {simulation.calibrated_probability ? (
                  <>
                    {" at "}
                    {Math.round(
                      Number(simulation.calibrated_probability) * 100,
                    )}
                    % confidence
                  </>
                ) : null}
              </p>
              <p className={HINT}>{simulation.reason}</p>
              {simulation.evidence.slice(0, 1).map((item) => (
                <blockquote
                  key={item.quote}
                  className="border-l-2 border-primary/60 pl-3 text-sm italic text-muted-foreground"
                >
                  {item.quote}
                  {item.page_number ? (
                    <span className={`${HINT} not-italic`}>
                      {" "}
                      (page {item.page_number})
                    </span>
                  ) : null}
                </blockquote>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className={BUTTON_PRIMARY}
          disabled={
            blocked || !preview.data || save.isPending || !sentence.trim()
          }
          onClick={() => save.mutate()}
        >
          {save.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
          )}
          Save step
        </button>
        {onCancel ? (
          <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>
            Cancel
          </button>
        ) : null}
        {blocked ? (
          <span className={HINT}>
            Confirm the AI notice above before saving.
          </span>
        ) : null}
      </div>
    </section>
  );
};

export default ClauseAssertionBlock;
