import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  Check,
  Download,
  Edit2,
  FileText,
  Loader2,
  MessageSquare,
  MoreVertical,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { PortalMenu } from "@/components/common/PortalMenu";
import { assistantApi } from "@/services/api/assistant";
import { assistantSessionsApi } from "@/services/api/assistantSessions";
import { ApiError } from "@/services/api/client";
import { assistantKeys } from "@/services/api/queryKeys";
import type {
  ConversationKindFilter,
  ConversationSession,
  ConversationSessionUpdate,
  ExportFormat,
} from "@/types/assistantSuite";
import { formatDateTime } from "@/utils/formatters";

interface ConversationSidebarProps {
  readonly workspaceId: string;
  readonly selectedId: string | null;
  readonly onSelect: (session: ConversationSession | null) => void;
}

const TABS: readonly { readonly key: ConversationKindFilter; readonly label: string }[] = [
  { key: "all", label: "All" },
  { key: "workspace", label: "Workspace" },
  { key: "document", label: "Documents" },
];

const errorMessage = (error: unknown, fallback: string): string =>
  error instanceof ApiError ? error.message : fallback;

/**
 * ARCH39-S1:conversation-sidebar — the Assistant's session list.
 *
 * Workspace and document conversations are told apart by a badge and a tab:
 * before ARCH-39 they appeared in one undifferentiated list, and a document
 * chat opened from the workspace page silently searched only that document.
 *
 * Search runs on the server (title, document name, message text), debounced,
 * so it finds a conversation the first page of results does not include.
 */
const ConversationSidebar: React.FC<ConversationSidebarProps> = ({
  workspaceId,
  selectedId,
  onSelect,
}) => {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ConversationKindFilter>("all");
  const [archived, setArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [toDelete, setToDelete] = useState<ConversationSession | null>(null);
  const anchors = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(search), 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  const filters = { kind, archived, q: debounced };

  const sessions = useQuery({
    queryKey: assistantKeys.sessions(workspaceId, filters),
    queryFn: () => assistantSessionsApi.listSessions(workspaceId, filters),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
    placeholderData: (previous) => previous,
  });

  const rows = sessions.data ?? [];

  const refresh = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: assistantKeys.sessionsRoot(workspaceId) });
    await queryClient.invalidateQueries({ queryKey: assistantKeys.conversations(workspaceId) });
  };

  const create = useMutation({
    mutationFn: () => assistantApi.createConversation(workspaceId),
    onSuccess: async (created) => {
      await refresh();
      setKind("all");
      setArchived(false);
      onSelect({
        id: created.id,
        title: created.title,
        kind: "workspace",
        scope_mode: "WORKSPACE",
        work_item_id: null,
        document_title: null,
        scope_document_count: 0,
        model_override: null,
        pinned: false,
        archived: false,
        message_count: 0,
        created_at: created.created_at,
        last_message_at: null,
      });
    },
    onError: (error) => toast.error(errorMessage(error, "The conversation could not be created.")),
  });

  const update = useMutation({
    mutationFn: (input: { readonly id: string; readonly payload: ConversationSessionUpdate }) =>
      assistantSessionsApi.updateSession(workspaceId, input.id, input.payload),
    onSuccess: async (session, input) => {
      await refresh();
      if (input.payload.archived !== undefined && session.id === selectedId) {
        onSelect(null);
      }
    },
    onError: (error) => toast.error(errorMessage(error, "That change could not be saved.")),
  });

  const remove = useMutation({
    mutationFn: (id: string) => assistantApi.deleteConversation(workspaceId, id),
    onSuccess: async (_, id) => {
      if (id === selectedId) {
        onSelect(null);
      }
      await refresh();
      toast.success("Conversation deleted.");
    },
    onError: (error) => toast.error(errorMessage(error, "The conversation could not be deleted.")),
  });

  const exportSession = async (id: string, format: ExportFormat): Promise<void> => {
    try {
      await assistantSessionsApi.downloadSessionExport(workspaceId, id, format);
    } catch (error) {
      toast.error(errorMessage(error, "The export could not be downloaded."));
    }
  };

  // Keep the selection valid when the list changes underneath it.
  useEffect(() => {
    if (sessions.isFetching || !sessions.data) {
      return;
    }
    const current = sessions.data.find((row) => row.id === selectedId);
    if (current) {
      onSelect(current);
    } else if (selectedId === null && sessions.data.length > 0 && !archived) {
      onSelect(sessions.data[0] ?? null);
    }
    // onSelect is deliberately not a dependency: the page passes a new
    // closure each render, and the selection is what this effect follows.
  }, [sessions.data, sessions.isFetching, selectedId, archived]);

  const commitRename = (id: string): void => {
    const title = draftTitle.trim();
    setEditing(null);
    if (title) {
      update.mutate({ id, payload: { title } });
    }
  };

  const pinned = rows.filter((row) => row.pinned);
  const others = rows.filter((row) => !row.pinned);

  const renderRow = (row: ConversationSession) => {
    const isSelected = row.id === selectedId;
    const isEditing = editing === row.id;
    const when = row.last_message_at ?? row.created_at;

    return (
      <li key={row.id}>
        <div
          className={`group flex items-start gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
            isSelected
              ? "border-primary/40 bg-primary/5"
              : "border-transparent hover:border-border hover:bg-muted/40"
          }`}
        >
          <button
            type="button"
            className="min-w-0 flex-1 text-left"
            onClick={() => onSelect(row)}
            aria-current={isSelected ? "true" : undefined}
            disabled={isEditing}
          >
            {isEditing ? (
              <input
                autoFocus
                value={draftTitle}
                maxLength={150}
                onChange={(event) => setDraftTitle(event.target.value)}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    commitRename(row.id);
                  }
                  if (event.key === "Escape") {
                    setEditing(null);
                  }
                }}
                aria-label="Conversation title"
                className="w-full rounded border border-border bg-background px-2 py-1 text-xs"
              />
            ) : (
              <span className="flex items-center gap-1.5">
                {row.pinned && <Pin className="h-3 w-3 flex-shrink-0 text-primary" aria-label="Pinned" />}
                <span className="truncate text-xs font-semibold text-foreground">{row.title}</span>
              </span>
            )}
            <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
              {row.kind === "document" ? (
                <span
                  className="inline-flex max-w-[11rem] items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-semibold text-amber-700 dark:text-amber-400"
                  title={row.document_title ?? "Document conversation"}
                >
                  <FileText className="h-2.5 w-2.5 flex-shrink-0" aria-hidden />
                  <span className="truncate">{row.document_title ?? "Document"}</span>
                </span>
              ) : (
                <span className="rounded-full bg-primary/10 px-1.5 py-0.5 font-semibold text-primary">
                  {row.scope_mode === "SELECTED"
                    ? `${row.scope_document_count} selected`
                    : "Workspace"}
                </span>
              )}
              <span>{row.message_count} msg</span>
              <span aria-hidden>·</span>
              <span>{formatDateTime(when)}</span>
            </span>
          </button>

          {isEditing ? (
            <span className="flex flex-shrink-0 gap-1">
              <button
                type="button"
                onClick={() => commitRename(row.id)}
                className="rounded p-1 hover:bg-muted"
                aria-label="Save title"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setEditing(null)}
                className="rounded p-1 hover:bg-muted"
                aria-label="Cancel rename"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ) : (
            <button
              type="button"
              ref={(node) => {
                anchors.current[row.id] = node;
              }}
              onClick={() => setMenuFor(menuFor === row.id ? null : row.id)}
              className="flex-shrink-0 rounded p-1 text-muted-foreground opacity-70 hover:bg-muted hover:text-foreground group-hover:opacity-100"
              aria-label={`Actions for ${row.title}`}
              aria-haspopup="menu"
              aria-expanded={menuFor === row.id}
            >
              <MoreVertical className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {menuFor === row.id && (
          <PortalMenu
            anchorRef={{ current: anchors.current[row.id] ?? null }}
            open
            onClose={() => setMenuFor(null)}
            width={200}
          >
            <div role="menu" aria-label="Conversation actions" className="p-1">
              {[
                {
                  key: "rename",
                  label: "Rename",
                  icon: Edit2,
                  run: () => {
                    setDraftTitle(row.title);
                    setEditing(row.id);
                  },
                },
                {
                  key: "pin",
                  label: row.pinned ? "Unpin" : "Pin to top",
                  icon: row.pinned ? PinOff : Pin,
                  run: () => update.mutate({ id: row.id, payload: { pinned: !row.pinned } }),
                },
                {
                  key: "archive",
                  label: row.archived ? "Restore" : "Archive",
                  icon: row.archived ? ArchiveRestore : Archive,
                  run: () => update.mutate({ id: row.id, payload: { archived: !row.archived } }),
                },
                {
                  key: "md",
                  label: "Export Markdown",
                  icon: Download,
                  run: () => void exportSession(row.id, "markdown"),
                },
                {
                  key: "json",
                  label: "Export JSON",
                  icon: Download,
                  run: () => void exportSession(row.id, "json"),
                },
              ].map((action) => (
                <button
                  key={action.key}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuFor(null);
                    action.run();
                  }}
                  className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-xs hover:bg-muted"
                >
                  <action.icon className="h-3.5 w-3.5" aria-hidden />
                  {action.label}
                </button>
              ))}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuFor(null);
                  setToDelete(row);
                }}
                className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-xs text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                Delete
              </button>
            </div>
          </PortalMenu>
        )}
      </li>
    );
  };

  return (
    <aside
      className="flex h-full min-h-0 flex-col rounded-xl border border-border/40 bg-card p-3"
      aria-label="Conversations"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
          Conversations
        </p>
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending}
          className="inline-flex h-8 items-center gap-1 rounded-lg border border-border bg-background px-2 text-xs font-semibold hover:bg-muted disabled:opacity-50"
        >
          {create.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Plus className="h-3.5 w-3.5" aria-hidden />
          )}
          New
        </button>
      </div>

      <label className="relative mb-2 block">
        <span className="sr-only">Search conversations</span>
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search titles, documents, messages"
          maxLength={100}
          className="h-8 w-full rounded-lg border border-border bg-background pl-7 pr-2 text-xs"
        />
      </label>

      <div className="mb-2 flex items-center gap-1" role="tablist" aria-label="Conversation type">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={kind === tab.key}
            onClick={() => setKind(tab.key)}
            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${
              kind === tab.key
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {tab.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setArchived((value) => !value)}
          aria-pressed={archived}
          className={`ml-auto inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold ${
            archived ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted"
          }`}
        >
          <Archive className="h-3 w-3" aria-hidden />
          Archived
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {sessions.isLoading ? (
          <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading…
          </div>
        ) : sessions.isError ? (
          <p role="alert" className="p-3 text-xs text-destructive">
            {errorMessage(sessions.error, "Conversations could not be loaded.")}
          </p>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center text-muted-foreground">
            <MessageSquare className="mb-1.5 h-6 w-6 opacity-40" aria-hidden />
            <p className="text-xs font-medium">
              {debounced
                ? "No conversation matches that search."
                : archived
                  ? "Nothing archived."
                  : "No conversations yet."}
            </p>
          </div>
        ) : (
          <>
            {pinned.length > 0 && (
              <>
                <p className="px-1 pb-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                  Pinned
                </p>
                <ul className="mb-2 space-y-1">{pinned.map(renderRow)}</ul>
              </>
            )}
            {others.length > 0 && (
              <>
                {pinned.length > 0 && (
                  <p className="px-1 pb-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                    Recent
                  </p>
                )}
                <ul className="space-y-1">{others.map(renderRow)}</ul>
              </>
            )}
          </>
        )}
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete conversation"
        message={toDelete ? `Delete "${toDelete.title}"? This cannot be undone.` : ""}
        confirmText="Delete"
        cancelText="Cancel"
        loading={remove.isPending}
        onCancel={() => setToDelete(null)}
        onConfirm={() => {
          if (toDelete) {
            remove.mutate(toDelete.id);
          }
          setToDelete(null);
        }}
      />
    </aside>
  );
};

export default ConversationSidebar;
