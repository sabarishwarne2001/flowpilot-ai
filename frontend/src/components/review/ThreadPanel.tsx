/**
 * ARCH48-S2:thread-panel. Discussion on a review item (Enterprise).
 *
 * A thread is anchored to the whole item, to one extracted field, or to one
 * paragraph of the item's document (chosen from the paragraphs the pipeline
 * stored; the server re-finds a paragraph after reprocessing and marks it
 * MOVED or OUTDATED). Replies, @-mentions of the workspace's reviewers (each
 * gets an in-app notification naming the item, never the text), resolve and
 * reopen, edit and delete your own comment (an admin may delete anyone's).
 *
 * Every submission carries a client nonce, so a double click or a retried
 * request writes one comment. The panel refreshes itself when the live channel
 * says a thread on this item changed (the hub invalidates its query).
 */

import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AtSign, CheckCircle2, Loader2, MessageSquare, Pencil, RotateCcw, Trash2, X } from "lucide-react";

import { Avatar } from "@/components/review/LivePresence";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, SELECT, TEXTAREA } from "@/components/ui/primitives";
import {
  deleteComment,
  editComment,
  listParagraphs,
  listThreads,
  reopenThread,
  replyToThread,
  resolveThread,
  startThread,
} from "@/services/api/collab";
import { errorMessage } from "@/services/api/errors";
import { reviewKeys } from "@/services/api/queryKeys";
import {
  ANCHOR_STATE_LABELS,
  MAX_COMMENT_CHARS,
  MAX_MENTIONS,
  type AnchorIn,
  type AnchorKind,
  type ThreadOut,
} from "@/types/collab";
import type { ReviewAssignee, ReviewItem } from "@/types/review";
import { formatTimestamp } from "@/utils/displayTime";

interface Props {
  readonly workspaceId: string;
  readonly item: ReviewItem;
  readonly meId: string;
  readonly isAdmin: boolean;
  readonly people: readonly ReviewAssignee[];
  readonly onClose: () => void;
}

const nonce = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "00000000-0000-4000-8000-000000000000".replace(/0/g, () => Math.floor(Math.random() * 16).toString(16));

const MentionPicker: React.FC<{
  readonly people: readonly ReviewAssignee[];
  readonly chosen: readonly string[];
  readonly onChange: (ids: string[]) => void;
}> = ({ people, chosen, onChange }) => (
  <div className="flex flex-wrap items-center gap-1.5">
    <AtSign className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
    {chosen.map((id) => {
      const person = people.find((p) => p.user_id === id);
      return (
        <button
          key={id}
          type="button"
          onClick={() => onChange(chosen.filter((x) => x !== id))}
          className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary"
          aria-label={`Remove mention of ${person?.email ?? id}`}
        >
          {person?.email ?? id} <X className="h-3 w-3" aria-hidden="true" />
        </button>
      );
    })}
    {chosen.length < MAX_MENTIONS && (
      <select
        aria-label="Mention a reviewer"
        value=""
        onChange={(event) => {
          if (event.target.value) {
            onChange([...chosen, event.target.value]);
          }
        }}
        className={`${SELECT} h-7 w-auto py-0 text-xs`}
      >
        <option value="">Mention…</option>
        {people
          .filter((p) => !chosen.includes(p.user_id))
          .map((p) => (
            <option key={p.user_id} value={p.user_id}>
              {p.email}
            </option>
          ))}
      </select>
    )}
  </div>
);

const Composer: React.FC<{
  readonly people: readonly ReviewAssignee[];
  readonly pending: boolean;
  readonly placeholder: string;
  readonly submitLabel: string;
  readonly onSubmit: (body: string, mentions: string[]) => void;
}> = ({ people, pending, placeholder, submitLabel, onSubmit }) => {
  const [body, setBody] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  const trimmed = body.trim();
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (trimmed && !pending) {
          onSubmit(trimmed, mentions);
          setBody("");
          setMentions([]);
        }
      }}
    >
      <textarea
        aria-label={placeholder}
        value={body}
        maxLength={MAX_COMMENT_CHARS}
        onChange={(event) => setBody(event.target.value)}
        placeholder={placeholder}
        className={`${TEXTAREA} min-h-[4rem] text-sm`}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <MentionPicker people={people} chosen={mentions} onChange={setMentions} />
        <button type="submit" disabled={!trimmed || pending} className={`${BUTTON_PRIMARY} h-8 px-3 text-xs`}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null} {submitLabel}
        </button>
      </div>
    </form>
  );
};

const AnchorLine: React.FC<{ readonly thread: ThreadOut }> = ({ thread }) => {
  const anchor = thread.anchor;
  if (anchor.kind === "ITEM") {
    return <span className={HINT}>On the whole item</span>;
  }
  if (anchor.kind === "FIELD") {
    return (
      <span className={HINT}>
        On the field <code className="rounded bg-muted px-1 font-mono text-[11px]">{anchor.field}</code>
      </span>
    );
  }
  const where =
    anchor.state === "OUTDATED"
      ? `page ${anchor.page ?? "?"}`
      : `page ${anchor.current_page ?? anchor.page ?? "?"}, paragraph ${(anchor.current_paragraph ?? anchor.paragraph ?? 0) + 1}`;
  return (
    <span className="block space-y-1">
      <span className={HINT}>
        On {where}
        {anchor.state !== "CURRENT" ? ` — ${ANCHOR_STATE_LABELS[anchor.state]}` : ""}
      </span>
      {anchor.quote && (
        <blockquote
          className={`border-l-2 pl-2 text-xs italic ${anchor.state === "OUTDATED" ? "border-amber-500 text-muted-foreground line-through" : "border-primary/50 text-foreground/80"}`}
        >
          {anchor.quote}
        </blockquote>
      )}
    </span>
  );
};

export const ThreadPanel: React.FC<Props> = ({ workspaceId, item, meId, isAdmin, people, onClose }) => {
  const queryClient = useQueryClient();
  const [anchorKind, setAnchorKind] = useState<AnchorKind>("ITEM");
  const [field, setField] = useState("");
  const [paragraph, setParagraph] = useState<number | null>(null);
  const [showResolved, setShowResolved] = useState(false);
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);

  const threadsQuery = useQuery({
    queryKey: reviewKeys.threads(workspaceId, item.kind, item.item_id),
    queryFn: () => listThreads(workspaceId, item.kind, item.item_id),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
  });
  const paragraphsQuery = useQuery({
    queryKey: reviewKeys.paragraphs(workspaceId, item.kind, item.item_id),
    queryFn: () => listParagraphs(workspaceId, item.kind, item.item_id),
    enabled: Boolean(workspaceId) && anchorKind === "PARAGRAPH" && Boolean(item.work_item_id),
    staleTime: 60_000,
  });

  const refresh = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: reviewKeys.threads(workspaceId, item.kind, item.item_id) });
    await queryClient.invalidateQueries({ queryKey: reviewKeys.queue(workspaceId, undefined).slice(0, -1) });
  };
  const failed = (fallback: string) => (error: unknown) => toast.error(errorMessage(error, fallback));

  const anchor = useMemo<AnchorIn | null>(() => {
    if (anchorKind === "ITEM") {
      return { kind: "ITEM" };
    }
    if (anchorKind === "FIELD") {
      return field.trim() ? { kind: "FIELD", field: field.trim() } : null;
    }
    const chosen = paragraphsQuery.data?.paragraphs.find((p) => p.index === paragraph);
    return chosen ? { kind: "PARAGRAPH", page: chosen.page, paragraph: chosen.index, digest: chosen.digest } : null;
  }, [anchorKind, field, paragraph, paragraphsQuery.data]);

  const create = useMutation({
    mutationFn: (args: { body: string; mentions: string[] }) =>
      startThread(workspaceId, item.kind, item.item_id, {
        anchor: anchor ?? { kind: "ITEM" },
        body: args.body,
        mentions: args.mentions,
        client_nonce: nonce(),
      }),
    onSuccess: async () => {
      setParagraph(null);
      setField("");
      setAnchorKind("ITEM");
      await refresh();
    },
    onError: async (error: unknown) => {
      failed("The thread could not be started.")(error);
      await queryClient.invalidateQueries({ queryKey: reviewKeys.paragraphs(workspaceId, item.kind, item.item_id) });
    },
  });
  const reply = useMutation({
    mutationFn: (args: { threadId: string; body: string; mentions: string[] }) =>
      replyToThread(workspaceId, args.threadId, { body: args.body, mentions: args.mentions, client_nonce: nonce() }),
    onSuccess: refresh,
    onError: failed("The reply could not be posted."),
  });
  const setStatus = useMutation({
    mutationFn: (args: { threadId: string; resolved: boolean }) =>
      args.resolved ? resolveThread(workspaceId, args.threadId) : reopenThread(workspaceId, args.threadId),
    onSuccess: refresh,
    onError: failed("The thread could not be updated."),
  });
  const edit = useMutation({
    mutationFn: (args: { commentId: string; body: string }) => editComment(workspaceId, args.commentId, { body: args.body }),
    onSuccess: async () => {
      setEditing(null);
      await refresh();
    },
    onError: failed("The comment could not be edited."),
  });
  const remove = useMutation({
    mutationFn: (commentId: string) => deleteComment(workspaceId, commentId),
    onSuccess: refresh,
    onError: failed("The comment could not be deleted."),
  });

  const threads = (threadsQuery.data?.threads ?? []).filter((t) => showResolved || t.status === "OPEN");
  const resolvedCount = (threadsQuery.data?.threads ?? []).filter((t) => t.status === "RESOLVED").length;

  return (
    <section className="space-y-4 rounded-lg border border-border bg-card p-4" aria-label="Discussion">
      <header className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <MessageSquare className="h-4 w-4" aria-hidden="true" /> Discussion
          {threadsQuery.data ? (
            <span className="text-xs font-normal text-muted-foreground">{threadsQuery.data.open_threads} open</span>
          ) : null}
        </h3>
        <span className="flex items-center gap-2">
          {resolvedCount > 0 && (
            <button type="button" onClick={() => setShowResolved((x) => !x)} className="text-xs text-primary hover:underline">
              {showResolved ? "Hide resolved" : `Show ${resolvedCount} resolved`}
            </button>
          )}
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close the discussion">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </span>
      </header>

      {threadsQuery.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Loading the discussion…
        </p>
      ) : threadsQuery.isError ? (
        <p className="text-xs text-destructive">{errorMessage(threadsQuery.error, "The discussion could not be loaded.")}</p>
      ) : threads.length === 0 ? (
        <p className={HINT}>No open discussion on this item yet.</p>
      ) : (
        <ul className="space-y-3" aria-label="Threads">
          {threads.map((thread) => (
            <li key={thread.id} className={`space-y-2 rounded-lg border p-3 ${thread.status === "RESOLVED" ? "border-border/50 opacity-75" : "border-border"}`}>
              <div className="flex items-start justify-between gap-2">
                <AnchorLine thread={thread} />
                {thread.status === "OPEN" ? (
                  <button
                    type="button"
                    disabled={setStatus.isPending}
                    onClick={() => setStatus.mutate({ threadId: thread.id, resolved: true })}
                    className="inline-flex shrink-0 items-center gap-1 text-[11px] font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> Resolve
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={setStatus.isPending}
                    onClick={() => setStatus.mutate({ threadId: thread.id, resolved: false })}
                    className="inline-flex shrink-0 items-center gap-1 text-[11px] font-semibold text-muted-foreground hover:underline"
                  >
                    <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Reopen
                  </button>
                )}
              </div>
              <ol className="space-y-2">
                {thread.comments.map((comment) => (
                  <li key={comment.id} className="flex gap-2">
                    {comment.author ? <Avatar person={comment.author} /> : <span className="h-6 w-6" aria-hidden="true" />}
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-muted-foreground">
                        <span className="font-semibold text-foreground">{comment.author?.name || comment.author?.email || "Former member"}</span>{" "}
                        · {formatTimestamp(comment.created_at)}
                        {comment.edited_at ? " · edited" : ""}
                      </p>
                      {editing?.id === comment.id ? (
                        <form
                          className="mt-1 space-y-1"
                          onSubmit={(event) => {
                            event.preventDefault();
                            if (editing.body.trim()) {
                              edit.mutate({ commentId: comment.id, body: editing.body.trim() });
                            }
                          }}
                        >
                          <textarea
                            aria-label="Edit your comment"
                            value={editing.body}
                            maxLength={MAX_COMMENT_CHARS}
                            onChange={(event) => setEditing({ id: comment.id, body: event.target.value })}
                            className={`${TEXTAREA} min-h-[3rem] text-sm`}
                          />
                          <span className="flex gap-2">
                            <button type="submit" disabled={edit.isPending} className={`${BUTTON_PRIMARY} h-7 px-2 text-xs`}>Save</button>
                            <button type="button" onClick={() => setEditing(null)} className={`${BUTTON_SECONDARY} h-7 px-2 text-xs`}>Cancel</button>
                          </span>
                        </form>
                      ) : comment.body === null ? (
                        <p className="text-xs italic text-muted-foreground">{comment.erased ? "Removed at the author's request." : "Deleted."}</p>
                      ) : (
                        <p className="whitespace-pre-wrap break-words text-sm">{comment.body}</p>
                      )}
                      {comment.mentions.length > 0 && comment.body !== null && (
                        <p className="mt-0.5 text-[11px] text-primary">
                          {comment.mentions.map((m) => `@${m.name || m.email}`).join(" ")}
                        </p>
                      )}
                      {comment.body !== null && editing?.id !== comment.id && (
                        <span className="mt-1 flex gap-3">
                          {comment.author?.user_id === meId && (
                            <button
                              type="button"
                              onClick={() => setEditing({ id: comment.id, body: comment.body ?? "" })}
                              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                            >
                              <Pencil className="h-3 w-3" aria-hidden="true" /> Edit
                            </button>
                          )}
                          {(comment.author?.user_id === meId || isAdmin) && (
                            <button
                              type="button"
                              disabled={remove.isPending}
                              onClick={() => remove.mutate(comment.id)}
                              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="h-3 w-3" aria-hidden="true" /> Delete
                            </button>
                          )}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
              {thread.status === "OPEN" && (
                <Composer
                  people={people}
                  pending={reply.isPending}
                  placeholder="Reply…"
                  submitLabel="Reply"
                  onSubmit={(body, mentions) => reply.mutate({ threadId: thread.id, body, mentions })}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-2 border-t border-border/60 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs font-semibold" htmlFor={`anchor-${item.item_id}`}>
            New thread on
          </label>
          <select
            id={`anchor-${item.item_id}`}
            value={anchorKind}
            onChange={(event) => setAnchorKind(event.target.value as AnchorKind)}
            className={`${SELECT} h-8 w-auto py-0 text-xs`}
          >
            <option value="ITEM">the whole item</option>
            <option value="FIELD">a field</option>
            {item.work_item_id ? <option value="PARAGRAPH">a paragraph of the document</option> : null}
          </select>
          {anchorKind === "FIELD" && (
            <input
              aria-label="Field path"
              value={field}
              maxLength={200}
              onChange={(event) => setField(event.target.value)}
              placeholder="e.g. invoice.total_amount"
              className={`${SELECT} h-8 w-56 py-0 font-mono text-xs`}
            />
          )}
        </div>
        {anchorKind === "PARAGRAPH" && (
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-border/60 p-2" role="listbox" aria-label="Paragraphs">
            {paragraphsQuery.isLoading ? (
              <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Reading the document…
              </p>
            ) : (paragraphsQuery.data?.paragraphs ?? []).length === 0 ? (
              <p className={HINT}>The document has no stored text to anchor to.</p>
            ) : (
              (paragraphsQuery.data?.paragraphs ?? []).map((p) => (
                <button
                  key={p.index}
                  type="button"
                  role="option"
                  aria-selected={paragraph === p.index}
                  onClick={() => setParagraph(p.index)}
                  className={`block w-full rounded px-2 py-1 text-left text-xs ${paragraph === p.index ? "bg-primary/10 ring-1 ring-primary" : "hover:bg-muted"}`}
                >
                  <span className="mr-2 font-mono text-[10px] text-muted-foreground">p{p.page}·{p.index + 1}</span>
                  {p.text.length > 280 ? `${p.text.slice(0, 280)}…` : p.text}
                </button>
              ))
            )}
          </div>
        )}
        <Composer
          people={people}
          pending={create.isPending}
          placeholder={anchor ? "Start a discussion…" : "Choose what the thread is about first"}
          submitLabel="Start thread"
          onSubmit={(body, mentions) => {
            if (!anchor) {
              toast.error(anchorKind === "FIELD" ? "Name the field first." : "Choose a paragraph first.");
              return;
            }
            create.mutate({ body, mentions });
          }}
        />
      </div>
    </section>
  );
};

export default ThreadPanel;
