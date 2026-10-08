/**
 * Phase 2 — character-level difference between two short values (a field as one extractor read it
 * against the value proposed for it), for highlighting what differs in review.
 *
 * A longest-common-subsequence walk: the characters of `value` that are part of the longest run it
 * shares with `reference` are "same", the rest "changed". Values longer than MAX_DIFF characters are
 * compared whole, which keeps the table cheap for long free text.
 */
export interface DiffSegment {
  readonly text: string;
  readonly same: boolean;
}

const MAX_DIFF = 240;

export function diffSegments(value: string, reference: string): DiffSegment[] {
  if (value === reference) {
    return value ? [{ text: value, same: true }] : [];
  }
  if (!value) {
    return [];
  }
  if (!reference || value.length > MAX_DIFF || reference.length > MAX_DIFF) {
    return [{ text: value, same: false }];
  }
  const n = value.length;
  const m = reference.length;
  // lengths[i][j]: the longest common subsequence of value[i:] and reference[j:].
  const lengths: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lengths[i]![j] =
        value[i] === reference[j] ? lengths[i + 1]![j + 1]! + 1 : Math.max(lengths[i + 1]![j]!, lengths[i]![j + 1]!);
    }
  }
  const segments: DiffSegment[] = [];
  const push = (char: string, same: boolean) => {
    const last = segments[segments.length - 1];
    if (last && last.same === same) {
      segments[segments.length - 1] = { text: last.text + char, same };
    } else {
      segments.push({ text: char, same });
    }
  };
  let i = 0;
  let j = 0;
  while (i < n) {
    if (j < m && value[i] === reference[j]) {
      push(value[i]!, true);
      i += 1;
      j += 1;
    } else if (j < m && lengths[i]![j + 1]! >= lengths[i + 1]![j]!) {
      j += 1;
    } else {
      push(value[i]!, false);
      i += 1;
    }
  }
  return segments;
}

/** Share of `value`'s characters that match `reference` (1 when equal, 0 when nothing does). */
export function similarity(value: string, reference: string): number {
  if (value === reference) {
    return 1;
  }
  if (!value || !reference) {
    return 0;
  }
  const same = diffSegments(value, reference)
    .filter((segment) => segment.same)
    .reduce((total, segment) => total + segment.text.length, 0);
  return same / Math.max(value.length, reference.length);
}
