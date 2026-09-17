/**
 * ARCH-37 — the builder's right rail: the rule in one sentence, what still
 * blocks saving (each item jumps to its card), and the keyboard shortcuts.
 */

import React from "react";
import { AlertTriangle, CheckCircle2, Keyboard, Loader2, Save } from "lucide-react";

import type { CardIssue, CardKey } from "./flowModel";

const CARD_TITLES: Readonly<Record<CardKey, string>> = {
  general: "Details",
  trigger: "When",
  conditions: "Only if",
  actions: "Then",
  else: "Otherwise",
};

const CARD_ANCHORS: Readonly<Record<CardKey, string>> = {
  general: "flow-step-general",
  trigger: "flow-step-trigger",
  conditions: "flow-step-conditions",
  actions: "flow-step-actions",
  else: "flow-step-else",
};

interface SummaryRailProps {
  readonly sentence: string;
  readonly issues: readonly CardIssue[];
  readonly dirty: boolean;
  readonly saving: boolean;
  readonly isEdit: boolean;
  readonly onSave: () => void;
}

export const SummaryRail: React.FC<SummaryRailProps> = ({ sentence, issues, dirty, saving, isEdit, onSave }) => (
  <aside className="space-y-4 lg:sticky lg:top-0" aria-label="Rule summary">
    <div className="rounded-xl border border-border bg-card p-4">
      <h3 className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">This rule</h3>
      <p className="mt-2 text-sm leading-relaxed" aria-live="polite">{sentence}</p>
    </div>

    <div className="rounded-xl border border-border bg-card p-4">
      {issues.length === 0 ? (
        <p className="flex items-center gap-2 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> Ready to save
        </p>
      ) : (
        <>
          <p className="flex items-center gap-2 text-xs font-bold text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {issues.length} {issues.length === 1 ? "thing" : "things"} to fix
          </p>
          <ul className="mt-2 space-y-1">
            {issues.slice(0, 8).map((issue, index) => (
              <li key={index}>
                <a
                  href={`#${CARD_ANCHORS[issue.card]}`}
                  onClick={(event) => {
                    event.preventDefault();
                    document.getElementById(CARD_ANCHORS[issue.card])?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}
                  className="block rounded px-1.5 py-1 text-xs hover:bg-muted"
                >
                  <span className="font-bold">
                    {CARD_TITLES[issue.card]}
                    {issue.index !== null ? ` #${issue.index + 1}` : ""}:
                  </span>{" "}
                  {issue.message}
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Save className="h-4 w-4" aria-hidden="true" />}
        {isEdit ? "Save changes" : "Create rule"}
      </button>
      {dirty && !saving && <p className="mt-2 text-center text-[11px] text-muted-foreground">Unsaved changes</p>}
    </div>

    <div className="rounded-xl border border-border bg-card p-4 text-[11px] text-muted-foreground">
      <p className="mb-1 flex items-center gap-1.5 font-bold uppercase tracking-wider">
        <Keyboard className="h-3.5 w-3.5" aria-hidden="true" /> Shortcuts
      </p>
      <p><kbd className="rounded border border-border px-1">Ctrl/⌘ S</kbd> save</p>
      <p><kbd className="rounded border border-border px-1">Ctrl/⌘ Enter</kbd> save</p>
      <p><kbd className="rounded border border-border px-1">Esc</kbd> close (asks if unsaved)</p>
    </div>
  </aside>
);
