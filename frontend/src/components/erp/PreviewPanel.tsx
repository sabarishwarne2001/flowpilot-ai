/**
 * ARCH47-S2:preview-panel — what would be sent: the canonical object, the
 * mapped record and the rendered file (validated against its schema before it
 * is shown). Nothing is posted by previewing.
 */
import React, { useState } from "react";

import { JsonBlock } from "@/components/erp/common";
import { HINT } from "@/components/ui/primitives";
import type { PreviewResponse } from "@/types/erp";

type View = "rendered" | "mapped" | "canonical";

export const PreviewPanel: React.FC<{ readonly preview: PreviewResponse }> = ({ preview }) => {
  const [view, setView] = useState<View>(preview.rendered_preview ? "rendered" : "canonical");
  return (
    <div className="space-y-2 rounded-lg border border-border/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${preview.ok
          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
          : "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300"}`}>
          {preview.ok ? "Valid — ready to post" : "Cannot be posted yet"}
        </span>
        {preview.filename ? <span className="font-mono text-xs">{preview.filename}</span> : null}
        {preview.rendered_media_type ? <span className={HINT}>{preview.rendered_media_type}</span> : null}
        <div className="ml-auto flex gap-1" role="tablist" aria-label="Preview views">
          {(["rendered", "mapped", "canonical"] as const).map((id) => (
            <button key={id} type="button" role="tab" aria-selected={view === id} onClick={() => setView(id)}
              className={`rounded-md px-2 py-0.5 text-xs font-semibold ${view === id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"}`}>
              {id === "rendered" ? "File" : id === "mapped" ? "Mapped" : "Canonical"}
            </button>
          ))}
        </div>
      </div>
      {preview.problems.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-destructive">
          {preview.problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      ) : null}
      {preview.notes.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
          {preview.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      ) : null}
      {view === "rendered" ? (
        preview.rendered_preview ? (
          <pre aria-label="Rendered file" className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/50 p-3 font-mono text-[11px]">
            {preview.rendered_preview}
          </pre>
        ) : <p className={HINT}>No file (it could not be rendered).</p>
      ) : null}
      {view === "mapped" ? (preview.mapped ? <JsonBlock label="Mapped record" value={preview.mapped} /> : <p className={HINT}>Not mapped.</p>) : null}
      {view === "canonical" ? (preview.canonical ? <JsonBlock label="Canonical object" value={preview.canonical} /> : <p className={HINT}>Not built.</p>) : null}
    </div>
  );
};

export default PreviewPanel;
