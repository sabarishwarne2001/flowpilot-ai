/**
 * ARCH49-S2:object-timeline — one object's events, oldest first, with the other
 * objects each event touched. Activities, sources, actors and ids only: the
 * event log never holds document or comment text.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";

import { activityLabel } from "@/components/process/common";
import { BUTTON_GHOST, HINT, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";
import { getObjectTimeline, processKeys } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import { OBJECT_TYPE_LABELS, type ObjectType } from "@/types/process";

const attr = (value: unknown): string => (typeof value === "object" ? JSON.stringify(value) : String(value));

export const ObjectTimeline: React.FC<{
  readonly workspaceId: string;
  readonly objectType: ObjectType;
  readonly objectId: string;
  readonly onClose: () => void;
  readonly onOpen: (objectType: ObjectType, objectId: string) => void;
}> = ({ workspaceId, objectType, objectId, onClose, onOpen }) => {
  const q = useQuery({
    queryKey: processKeys.timeline(workspaceId, objectType, objectId),
    queryFn: () => getObjectTimeline(workspaceId, objectType, objectId),
    enabled: Boolean(workspaceId && objectId),
  });
  return (
    <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="object-timeline">
      <div className="flex items-center justify-between gap-2">
        <h2 id="object-timeline" className={SECTION_TITLE}>
          {OBJECT_TYPE_LABELS[objectType]} · <span className="font-mono text-xs">{objectId}</span>
        </h2>
        <button type="button" className={BUTTON_GHOST} onClick={onClose} aria-label="Close the timeline"><X className="h-4 w-4" aria-hidden /></button>
      </div>
      {q.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      {q.isError ? <p className="text-sm text-destructive">{errorMessage(q.error, "Could not load the events.")}</p> : null}
      {q.data && q.data.events.length === 0 ? <p className={HINT}>No events for this object in the log.</p> : null}
      {q.data && q.data.events.length > 0 ? (
        <ol className="space-y-2 border-l border-border pl-4">
          {q.data.events.map((e) => (
            <li key={e.id} className="text-sm">
              <p className="flex flex-wrap items-baseline gap-2">
                <span className="font-semibold">{activityLabel(e.activity)}</span>
                <span className="text-xs text-muted-foreground">{formatTimestamp(e.occurred_at)}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold">{e.actor_kind.toLowerCase()}</span>
                <span className="text-[10px] text-muted-foreground">{e.source.toLowerCase().split("_").join(" ")}</span>
              </p>
              {Object.keys(e.attributes).length ? (
                <p className="font-mono text-[11px] text-muted-foreground">
                  {Object.entries(e.attributes).map(([k, v]) => `${k}=${attr(v)}`).join(" · ")}
                </p>
              ) : null}
              {e.objects.length ? (
                <p className="flex flex-wrap gap-1 text-[11px]">
                  {e.objects.map((o) => (
                    <button key={`${o.object_type}-${o.object_id}-${o.qualifier}`} type="button"
                      className="rounded border border-border px-1.5 py-0.5 hover:bg-muted"
                      onClick={() => onOpen(o.object_type as ObjectType, o.object_id)}>
                      {OBJECT_TYPE_LABELS[o.object_type as ObjectType] ?? o.object_type}{o.qualifier ? ` (${o.qualifier})` : ""} · {o.object_id.slice(0, 8)}
                    </button>
                  ))}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
};

export default ObjectTimeline;
