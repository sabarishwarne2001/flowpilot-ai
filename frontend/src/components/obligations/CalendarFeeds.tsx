/**
 * ARCH46-S2:calendar-feeds — subscribe a calendar app (Google, Outlook, Apple)
 * to the workspace's obligations. The URL carries a signed token that is shown
 * ONCE, here, when it is issued; the server keeps only its SHA-256. Revoking a
 * feed takes effect on the calendar app's next poll (nothing caches a feed).
 * Each member manages their own feeds; an administrator can revoke anyone's.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Link2, Loader2, XCircle } from "lucide-react";

import { BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, FIELD_LABEL, HINT, INPUT, SELECT, SURFACE } from "@/components/ui/primitives";
import { feedUrls, issueFeed, listFeeds, obligationKeys, revokeFeed } from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";
import type { FeedIssued } from "@/types/obligations";
import { formatDateTime } from "@/utils/formatters";

interface Props {
  readonly workspaceId: string;
}

export const CalendarFeeds: React.FC<Props> = ({ workspaceId }) => {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: obligationKeys.feeds(workspaceId), queryFn: () => listFeeds(workspaceId),
    enabled: Boolean(workspaceId) });
  const [label, setLabel] = useState("My obligations");
  const [scope, setScope] = useState("MINE");
  const [includeClosed, setIncludeClosed] = useState(true);
  const [expires, setExpires] = useState("");
  const [issued, setIssued] = useState<FeedIssued | null>(null);
  const [copied, setCopied] = useState(false);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: obligationKeys.feeds(workspaceId) });
  };
  const issue = useMutation({
    mutationFn: () => {
      const days = expires ? Number.parseInt(expires, 10) : null;
      return issueFeed(workspaceId, {
        label: label.trim() || "Obligations", scope, include_closed: includeClosed,
        ...(days && !Number.isNaN(days) ? { expires_in_days: days } : {}),
      });
    },
    onSuccess: (result) => { setIssued(result); setCopied(false); refresh(); },
  });
  const revoke = useMutation({ mutationFn: (id: string) => revokeFeed(workspaceId, id), onSuccess: refresh });
  const urls = issued ? feedUrls(issued.token) : null;
  const copy = async (): Promise<void> => {
    if (urls) {
      await navigator.clipboard.writeText(urls.https);
      setCopied(true);
    }
  };

  return (
    <div className="space-y-4">
      <p className={HINT}>
        A feed is a private link: anyone holding it can read the obligations it covers, so treat it like a password and
        revoke it if it leaks. It shows each due date as an all-day event in your calendar, with a reminder at the
        obligation&apos;s lead time.
      </p>
      <form className={`${SURFACE} space-y-3 p-4`} aria-label="New calendar feed"
        onSubmit={(event) => { event.preventDefault(); issue.mutate(); }}>
        <div className="grid gap-3 md:grid-cols-4">
          <label className="space-y-1 md:col-span-2">
            <span className={FIELD_LABEL}>Label</span>
            <input className={INPUT} value={label} maxLength={100} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className={FIELD_LABEL}>Covers</span>
            <select className={SELECT} value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="MINE">Obligations I own</option>
              <option value="ALL">Every obligation in the workspace</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className={FIELD_LABEL}>Expires after (days)</span>
            <input type="number" min={1} max={3650} className={INPUT} value={expires} placeholder="Never"
              onChange={(e) => setExpires(e.target.value)} />
          </label>
        </div>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeClosed} onChange={(e) => setIncludeClosed(e.target.checked)} />
          Keep recently done and waived obligations on the calendar
        </label>
        {issue.isError ? <p className="text-sm text-destructive">{errorMessage(issue.error, "The feed could not be created.")}</p> : null}
        <button type="submit" className={BUTTON_PRIMARY} disabled={issue.isPending}>
          {issue.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Link2 className="h-4 w-4" aria-hidden />}
          Create feed link
        </button>
      </form>
      {issued && urls ? (
        <div className="space-y-2 rounded-lg border border-primary/40 bg-primary/5 p-3" role="status">
          <p className="text-sm font-semibold">Copy this link now: it is shown only once.</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="max-w-full flex-1 break-all rounded bg-background px-2 py-1 text-xs">{urls.https}</code>
            <button type="button" className={BUTTON_GHOST} onClick={() => void copy()}>
              {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />} {copied ? "Copied" : "Copy"}
            </button>
            <a className={BUTTON_GHOST} href={urls.webcal}>Open in my calendar app</a>
          </div>
          <p className={HINT}>In Google Calendar: Other calendars → From URL. In Outlook: Add calendar → Subscribe from web.</p>
        </div>
      ) : null}
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Feeds could not be loaded.")}</p> : null}
      <ul className="space-y-2">
        {(query.data?.items ?? []).map((feed) => (
          <li key={feed.id} className={`${SURFACE} flex flex-wrap items-center gap-2 p-3 text-sm ${feed.revoked_at ? "opacity-60" : ""}`}>
            <span className="font-semibold">{feed.label}</span>
            <span className={HINT}>
              {feed.scope === "MINE" ? "my obligations" : "whole workspace"} · created {formatDateTime(feed.created_at)}
              {feed.last_used_at ? ` · last read ${formatDateTime(feed.last_used_at)} (${feed.use_count}×)` : " · never read"}
              {feed.expires_at ? ` · expires ${formatDateTime(feed.expires_at)}` : ""}
              {feed.mine ? "" : " · another member's"}
            </span>
            {feed.revoked_at ? (
              <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground">
                <XCircle className="h-3.5 w-3.5" aria-hidden /> Revoked {formatDateTime(feed.revoked_at)}
              </span>
            ) : (
              <button type="button" className={`${BUTTON_DESTRUCTIVE} ml-auto`} disabled={revoke.isPending}
                onClick={() => { if (window.confirm(`Revoke "${feed.label}"? Calendars subscribed to it stop updating.`)) { revoke.mutate(feed.id); } }}>
                Revoke
              </button>
            )}
          </li>
        ))}
        {query.data && query.data.items.length === 0 ? <li className={HINT}>No feeds yet.</li> : null}
      </ul>
    </div>
  );
};

export default CalendarFeeds;
