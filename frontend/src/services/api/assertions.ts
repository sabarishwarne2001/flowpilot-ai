/** ARCH-33 — assertions API client. */

import { apiClient } from "@/services/api/client";
import type {
  AssertionDefinition,
  AssertionFamily,
  AssertionPreview,
  AssertionReviewItem,
  AssertionSimulation,
  AssertionVerdict,
  RetrievalPhrase,
} from "@/types/assertions";

const base = (workspaceId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/assertions`;

/**
 * Compile a sentence without saving it.
 *
 * Called on every debounced keystroke in the rule builder, which is why it is
 * a POST that writes nothing rather than a GET with the sentence in a query
 * string: a clause sentence routinely contains `&`, `%` and `#`, and half of
 * them would arrive mangled.
 */
export const previewAssertion = async (
  workspaceId: string,
  sentence: string,
  thresholdPercent?: number,
): Promise<AssertionPreview> => {
  const { data } = await apiClient.post<AssertionPreview>(
    `${base(workspaceId)}/preview`,
    {
      sentence,
      threshold:
        thresholdPercent === undefined
          ? undefined
          : (thresholdPercent / 100).toFixed(4),
    },
  );
  return data;
};

export const listRuleAssertions = async (
  workspaceId: string,
  ruleId: string,
): Promise<AssertionDefinition[]> => {
  const { data } = await apiClient.get<AssertionDefinition[]>(
    `${base(workspaceId)}/rules/${encodeURIComponent(ruleId)}`,
  );
  return data;
};

export const saveAssertion = async (
  workspaceId: string,
  ruleId: string,
  nodeKey: string,
  payload: {
    readonly sentence: string;
    readonly thresholdPercent: number;
    readonly acknowledgeLlm: boolean;
  },
): Promise<AssertionDefinition> => {
  const { data } = await apiClient.put<AssertionDefinition>(
    `${base(workspaceId)}/rules/${encodeURIComponent(ruleId)}/nodes/${encodeURIComponent(nodeKey)}`,
    {
      node_key: nodeKey,
      sentence: payload.sentence,
      // Converted to a Decimal string exactly once, here. See types/assertions.ts.
      threshold: (payload.thresholdPercent / 100).toFixed(4),
      acknowledge_llm: payload.acknowledgeLlm,
    },
  );
  return data;
};

/** §4.6's "Test on a document". Starts no execution and writes nothing. */
export const simulateAssertion = async (
  workspaceId: string,
  payload: {
    readonly workItemId: string;
    // `| undefined` is not noise: tsconfig sets exactOptionalPropertyTypes,
    // so a caller passing an explicitly-undefined field is a type error
    // unless the property admits undefined.
    readonly definitionId?: string | undefined;
    readonly sentence?: string | undefined;
    readonly thresholdPercent?: number | undefined;
  },
): Promise<AssertionSimulation> => {
  const { data } = await apiClient.post<AssertionSimulation>(
    `${base(workspaceId)}/simulate`,
    {
      work_item_id: payload.workItemId,
      definition_id: payload.definitionId ?? null,
      sentence: payload.sentence ?? null,
      threshold:
        payload.thresholdPercent === undefined
          ? undefined
          : (payload.thresholdPercent / 100).toFixed(4),
    },
  );
  return data;
};

export const listAssertionReviews = async (
  workspaceId: string,
  options: {
    readonly workItemId?: string | undefined;
    readonly limit?: number | undefined;
  } = {},
): Promise<AssertionReviewItem[]> => {
  const params = new URLSearchParams();
  if (options.workItemId) {params.set("work_item_id", options.workItemId);}
  params.set("limit", String(options.limit ?? 50));

  const { data } = await apiClient.get<AssertionReviewItem[]>(
    `${base(workspaceId)}/reviews?${params.toString()}`,
  );
  return data;
};

/**
 * "It passes", "It fails", or "Wrong paragraph".
 *
 * The third is not a third verdict: the reviewer still says whether the
 * document passes, and additionally hands back the paragraph that should have
 * been read. `correctedQuote` is what teaches retrieval.
 */
export const resolveAssertionReview = async (
  workspaceId: string,
  evaluationId: string,
  payload: {
    readonly reviewerVerdict: AssertionVerdict;
    readonly correctedQuote?: string | undefined;
  },
): Promise<AssertionReviewItem> => {
  const { data } = await apiClient.post<AssertionReviewItem>(
    `${base(workspaceId)}/reviews/${encodeURIComponent(evaluationId)}/resolve`,
    {
      reviewer_verdict: payload.reviewerVerdict,
      corrected_quote: payload.correctedQuote ?? null,
      corrected_value: null,
    },
  );
  return data;
};

export const listLearnedPhrases = async (
  workspaceId: string,
  family?: AssertionFamily,
): Promise<RetrievalPhrase[]> => {
  const suffix = family ? `?family=${encodeURIComponent(family)}` : "";
  const { data } = await apiClient.get<RetrievalPhrase[]>(
    `${base(workspaceId)}/phrases${suffix}`,
  );
  return data;
};
