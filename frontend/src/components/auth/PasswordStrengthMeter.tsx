import React, { useEffect, useState } from "react";

import { MIN_PASSWORD_LENGTH } from "@/utils/validation";

/**
 * ASVS V2.1.8 — a strength meter while a password is being chosen.
 *
 * zxcvbn-ts, the same estimator family the server uses to refuse easy
 * passwords (app/core/password_policy.py, score below 3), loaded only on the
 * pages that choose a password (its word lists are ~1 MB). The server has the
 * final say; this is guidance, so a load failure simply shows nothing.
 */

type Estimator = (password: string, userInputs: string[]) => { score: number; warning: string };

let estimatorPromise: Promise<Estimator> | null = null;

function loadEstimator(): Promise<Estimator> {
  estimatorPromise ??= Promise.all([
    import("@zxcvbn-ts/core"),
    import("@zxcvbn-ts/language-common"),
    import("@zxcvbn-ts/language-en"),
  ]).then(([core, common, en]) => {
    const factory = new core.ZxcvbnFactory({
      translations: en.translations,
      graphs: common.adjacencyGraphs,
      dictionary: { ...common.dictionary, ...en.dictionary },
    });
    return (password: string, userInputs: string[]) => {
      const result = factory.check(password, ["flowpilot", ...userInputs]);
      return { score: result.score, warning: result.feedback.warning ?? "" };
    };
  });
  return estimatorPromise;
}

const LABELS = [
  "Too easy to guess",
  "Too easy to guess",
  "Fair: add another word",
  "Good",
  "Strong",
];
const COLOURS = [
  "bg-destructive",
  "bg-destructive",
  "bg-amber-500",
  "bg-emerald-500",
  "bg-emerald-600",
];

interface Props {
  readonly password: string;
  readonly userInputs?: readonly string[];
}

export const PasswordStrengthMeter: React.FC<Props> = ({ password, userInputs = [] }) => {
  const [result, setResult] = useState<{ score: number; warning: string } | null>(null);
  const inputsKey = userInputs.join("|");

  useEffect(() => {
    if (!password) {
      setResult(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      loadEstimator()
        .then((estimate) => {
          if (!cancelled) {
            setResult(estimate(password, inputsKey ? inputsKey.split("|") : []));
          }
        })
        .catch(() => {
          if (!cancelled) {
            setResult(null);
          }
        });
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [password, inputsKey]);

  if (!password) {
    return null;
  }

  const tooShort = password.length < MIN_PASSWORD_LENGTH;
  const score = tooShort ? Math.min(result?.score ?? 0, 1) : (result?.score ?? 0);
  const label = tooShort
    ? `Too short: ${MIN_PASSWORD_LENGTH} characters minimum`
    : result
      ? LABELS[score]
      : "Checking…";

  return (
    <div className="space-y-1.5 pt-1.5" data-testid="password-strength">
      <div className="grid grid-cols-4 gap-1.5" aria-hidden="true">
        {[1, 2, 3, 4].map((segment) => (
          <span
            key={segment}
            className={`h-1 rounded-full transition-colors duration-300 ${
              result && score >= segment ? COLOURS[score] : "bg-muted ring-1 ring-inset ring-border/60"
            }`}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        Password strength:{" "}
        <span className={`font-medium ${!result || tooShort ? "" : score <= 1 ? "text-destructive" : score === 2 ? "text-amber-600 dark:text-amber-400" : "text-emerald-600 dark:text-emerald-400"}`}>
          {label}
        </span>
        {!tooShort && result?.warning && score < 3 ? `. ${result.warning}` : ""}
      </p>
    </div>
  );
};

export default PasswordStrengthMeter;
