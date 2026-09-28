/**
 * ARCH48-S2:live-presence. Avatars of the reviewers looking at an item, the
 * item's lock badge, and the hub's live status pill.
 *
 * A lock is SOFT: it tells everyone else that someone is deciding this item,
 * and the server refuses anyone else's resolution while it is live. It lapses
 * by itself when its holder's tab goes quiet; a workspace admin can break it.
 */

import React from "react";
import { Lock, Radio, WifiOff } from "lucide-react";

import type { LiveStatus } from "@/hooks/useLiveReview";
import { initials, personHue, type LiveLock, type Viewer } from "@/types/collab";
import { formatTimestampTime } from "@/utils/displayTime";

const MAX_AVATARS = 4;

export const Avatar: React.FC<{ readonly person: { user_id: string; name?: string; email?: string }; readonly size?: "sm" | "md" }> = ({
  person,
  size = "sm",
}) => {
  const hue = personHue(person.user_id);
  const dimension = size === "sm" ? "h-6 w-6 text-[10px]" : "h-8 w-8 text-xs";
  return (
    <span
      className={`inline-flex ${dimension} items-center justify-center rounded-full font-bold text-white ring-2 ring-card`}
      style={{ backgroundColor: `hsl(${hue} 55% 45%)` }}
      title={person.name || person.email || "Reviewer"}
      aria-hidden="true"
    >
      {initials(person)}
    </span>
  );
};

export const PresenceAvatars: React.FC<{ readonly viewers: readonly Viewer[]; readonly meId?: string | undefined }> = ({
  viewers,
  meId,
}) => {
  const others = viewers.filter((viewer) => viewer.user_id !== meId);
  if (others.length === 0) {
    return null;
  }
  const names = others.map((viewer) => viewer.name || viewer.email).join(", ");
  return (
    <span className="inline-flex items-center" aria-label={`Also looking: ${names}`} title={`Also looking: ${names}`}>
      <span className="flex -space-x-1.5">
        {others.slice(0, MAX_AVATARS).map((viewer) => (
          <Avatar key={viewer.user_id} person={viewer} />
        ))}
      </span>
      {others.length > MAX_AVATARS && (
        <span className="ml-1 text-[10px] font-semibold text-muted-foreground">+{others.length - MAX_AVATARS}</span>
      )}
    </span>
  );
};

export const LockBadge: React.FC<{ readonly lock: LiveLock | undefined; readonly meId?: string | undefined }> = ({ lock, meId }) => {
  if (!lock) {
    return null;
  }
  const mine = lock.holder_user_id === meId;
  const who = lock.holder?.name || lock.holder?.email || "Another reviewer";
  const until = formatTimestampTime(lock.expires_at);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-bold ${
        mine ? "bg-primary/15 text-primary" : "bg-amber-500/15 text-amber-800 dark:text-amber-300"
      }`}
      title={mine ? `You are deciding this item (your lock renews while this tab is open; now until ${until}).` : `${who} is deciding this item. Their lock lapses if they step away (now until ${until}).`}
      role="status"
    >
      <Lock className="h-3 w-3" aria-hidden="true" />
      {mine ? "You are deciding" : `${who} is deciding`}
    </span>
  );
};

const STATUS_TEXT: Readonly<Record<LiveStatus, string>> = {
  off: "Live off",
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  refused: "Live unavailable",
};

export const LiveStatusPill: React.FC<{ readonly status: LiveStatus; readonly degraded: boolean; readonly others: number }> = ({
  status,
  degraded,
  others,
}) => {
  const live = status === "live";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
        live ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400" : "border-border text-muted-foreground"
      }`}
      role="status"
      aria-live="polite"
      title={
        live
          ? degraded
            ? "Live, but the cross-server channel is recovering: some updates may arrive late."
            : "The queue updates as reviewers decide, and you can see who is looking at what."
          : "The queue refreshes when you act; live updates are not connected."
      }
    >
      {live ? <Radio className="h-3.5 w-3.5" aria-hidden="true" /> : <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />}
      {STATUS_TEXT[status]}
      {live && others > 0 ? ` · ${others} other${others === 1 ? "" : "s"} here` : ""}
    </span>
  );
};

export default PresenceAvatars;
