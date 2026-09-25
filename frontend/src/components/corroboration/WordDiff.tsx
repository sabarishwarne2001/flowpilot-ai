/**
 * ARCH45-S2:word-diff — what changed between two versions of a clause, word by
 * word (a longest-common-subsequence over case-folded words). Removed words
 * are struck through in red, added words underlined in green. Computed in the
 * browser so any document can be the baseline the reviewer compares against.
 */
import React, { useMemo } from "react";

type Op = "equal" | "delete" | "insert";

interface Piece {
  readonly op: Op;
  readonly text: string;
}

const MAX_WORDS = 600;

export const diffWords = (before: string, after: string): Piece[] => {
  const a = before.split(/\s+/).filter(Boolean).slice(0, MAX_WORDS);
  const b = after.split(/\s+/).filter(Boolean).slice(0, MAX_WORDS);
  const fold = (w: string): string => w.toLowerCase().replace(/[“”"‘’'.,;:()]/g, "");
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    const row = lcs[i]!;
    const below = lcs[i + 1]!;
    for (let j = m - 1; j >= 0; j -= 1) {
      row[j] = fold(a[i]!) === fold(b[j]!) ? below[j + 1]! + 1 : Math.max(below[j]!, row[j + 1]!);
    }
  }
  const out: Piece[] = [];
  const push = (op: Op, text: string): void => {
    const last = out[out.length - 1];
    if (last && last.op === op) {
      out[out.length - 1] = { op, text: `${last.text} ${text}` };
    } else {
      out.push({ op, text });
    }
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (fold(a[i]!) === fold(b[j]!)) {
      push("equal", b[j]!);
      i += 1;
      j += 1;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      push("delete", a[i]!);
      i += 1;
    } else {
      push("insert", b[j]!);
      j += 1;
    }
  }
  while (i < n) {
    push("delete", a[i]!);
    i += 1;
  }
  while (j < m) {
    push("insert", b[j]!);
    j += 1;
  }
  return out;
};

export const WordDiff: React.FC<{ readonly before: string; readonly after: string }> = ({ before, after }) => {
  const pieces = useMemo(() => diffWords(before, after), [before, after]);
  return (
    <p className="whitespace-pre-wrap break-words text-xs leading-6">
      {pieces.map((piece, index) => {
        if (piece.op === "equal") {
          return <span key={index}>{piece.text} </span>;
        }
        if (piece.op === "delete") {
          return (
            <del key={index} className="rounded bg-red-500/15 px-0.5 text-red-700 dark:text-red-300">
              {piece.text}{" "}
            </del>
          );
        }
        return (
          <ins key={index} className="rounded bg-green-500/15 px-0.5 text-green-700 no-underline dark:text-green-300">
            {piece.text}{" "}
          </ins>
        );
      })}
    </p>
  );
};

export default WordDiff;
