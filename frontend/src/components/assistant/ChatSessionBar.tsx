import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  BookText,
  Cpu,
  Download,
  FileText,
  Globe2,
  Layers,
  Loader2,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { assistantSessionsApi } from "@/services/api/assistantSessions";
import { ApiError } from "@/services/api/client";
import { assistantKeys, workItemKeys } from "@/services/api/queryKeys";
import { workItemApi } from "@/services/api/workItem";
import {
  formatModelPrice,
  type ConversationSession,
  type PromptTemplate,
} from "@/types/assistantSuite";

interface ChatSessionBarProps {
  readonly workspaceId: string;
  readonly session: ConversationSession;
  readonly canWriteTemplates: boolean;
  readonly onInsertTemplate: (body: string) => void;
}

type Panel = "scope" | "templates" | null;

const errorMessage = (error: unknown, fallback: string): string =>
  error instanceof ApiError ? error.message : fallback;

const PANEL_CLASS =
  "absolute left-0 right-0 top-full z-30 mt-1 rounded-xl border border-border bg-card p-3 shadow-lg";

/**
 * ARCH39-S1:chat-session-bar — what this conversation searches, which model
 * answers, and the organization's prompt templates.
 *
 * A document conversation's scope is shown and not offered: the database
 * ties DOCUMENT scope to the conversation's document, and the server refuses
 * to change it.
 */
const ChatSessionBar: React.FC<ChatSessionBarProps> = ({
  workspaceId,
  session,
  canWriteTemplates,
  onInsertTemplate,
}) => {
  const queryClient = useQueryClient();
  const [panel, setPanel] = useState<Panel>(null);
  const [picked, setPicked] = useState<readonly string[]>([]);
  const [docSearch, setDocSearch] = useState("");
  const [newName, setNewName] = useState("");
  const [newBody, setNewBody] = useState("");

  useEffect(() => {
    setPanel(null);
  }, [session.id]);

  const isDocument = session.kind === "document";

  const scope = useQuery({
    queryKey: assistantKeys.scope(workspaceId, session.id),
    queryFn: () => assistantSessionsApi.getScope(workspaceId, session.id),
    enabled: !isDocument,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (scope.data) {
      setPicked(scope.data.work_item_ids);
    }
  }, [scope.data]);

  const models = useQuery({
    queryKey: assistantKeys.models(workspaceId),
    queryFn: () => assistantSessionsApi.listModels(workspaceId),
    staleTime: 5 * 60_000,
  });

  const templates = useQuery({
    queryKey: assistantKeys.templates(workspaceId),
    queryFn: () => assistantSessionsApi.listPromptTemplates(workspaceId),
    enabled: panel === "templates",
    staleTime: 60_000,
  });

  const docFilters = useMemo(
    () => ({
      page: 1,
      pageSize: 50,
      status: "COMPLETED" as const,
      ...(docSearch.trim() ? { search: docSearch.trim() } : {}),
    }),
    [docSearch],
  );

  const documents = useQuery({
    queryKey: workItemKeys.list(workspaceId, docFilters),
    queryFn: () => workItemApi.getWorkItems(workspaceId, docFilters),
    enabled: panel === "scope",
    staleTime: 30_000,
  });

  const refreshSessions = () =>
    queryClient.invalidateQueries({ queryKey: assistantKeys.sessionsRoot(workspaceId) });

  const saveScope = useMutation({
    mutationFn: (input: { readonly mode: "WORKSPACE" | "SELECTED"; readonly ids: readonly string[] }) =>
      assistantSessionsApi.setScope(workspaceId, session.id, {
        mode: input.mode,
        work_item_ids: input.ids,
      }),
    onSuccess: async (data) => {
      queryClient.setQueryData(assistantKeys.scope(workspaceId, session.id), data);
      await refreshSessions();
      setPanel(null);
      toast.success(
        data.mode === "WORKSPACE"
          ? "Searching the whole workspace."
          : `Searching ${data.work_item_ids.length} selected document(s).`,
      );
    },
    onError: (error) => toast.error(errorMessage(error, "The scope could not be saved.")),
  });

  const chooseModel = useMutation({
    mutationFn: (model: string) =>
      assistantSessionsApi.updateSession(
        workspaceId,
        session.id,
        model ? { model_override: model } : { clear_model_override: true },
      ),
    onSuccess: refreshSessions,
    onError: (error) => toast.error(errorMessage(error, "The model could not be changed.")),
  });

  const createTemplate = useMutation({
    mutationFn: () =>
      assistantSessionsApi.createPromptTemplate(workspaceId, { name: newName, body: newBody }),
    onSuccess: async () => {
      setNewName("");
      setNewBody("");
      await queryClient.invalidateQueries({ queryKey: assistantKeys.templates(workspaceId) });
      toast.success("Template saved for your organization.");
    },
    onError: (error) => toast.error(errorMessage(error, "The template could not be saved.")),
  });

  const archiveTemplate = useMutation({
    mutationFn: (template: PromptTemplate) =>
      assistantSessionsApi.archivePromptTemplate(workspaceId, template.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: assistantKeys.templates(workspaceId) }),
    onError: (error) => toast.error(errorMessage(error, "The template could not be removed.")),
  });

  const exportMarkdown = async (): Promise<void> => {
    try {
      await assistantSessionsApi.downloadSessionExport(workspaceId, session.id, "markdown");
    } catch (error) {
      toast.error(errorMessage(error, "The export could not be downloaded."));
    }
  };

  const maxDocuments = scope.data?.max_documents ?? 20;
  const selectedModel = session.model_override ?? "";
  const defaultModel = models.data?.find((option) => option.is_workspace_default)?.model;
  const activeOption = models.data?.find(
    (option) => option.model === (session.model_override ?? defaultModel),
  );
  const activePrice = activeOption ? formatModelPrice(activeOption) : null;

  const toggle = (id: string): void => {
    setPicked((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : current.length >= maxDocuments
          ? current
          : [...current, id],
    );
  };

  const chip =
    "inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-xs font-semibold hover:bg-muted";

  return (
    <div className="relative mb-2 flex flex-wrap items-center gap-2">
      {isDocument ? (
        <span
          className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-lg bg-amber-500/10 px-2.5 text-xs font-semibold text-amber-700 dark:text-amber-400"
          title="Document conversations always search their own document"
        >
          <FileText className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          <span className="truncate">Only: {session.document_title ?? "this document"}</span>
        </span>
      ) : (
        <button
          type="button"
          className={chip}
          aria-expanded={panel === "scope"}
          onClick={() => setPanel(panel === "scope" ? null : "scope")}
        >
          {session.scope_mode === "SELECTED" ? (
            <Layers className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Globe2 className="h-3.5 w-3.5" aria-hidden />
          )}
          {session.scope_mode === "SELECTED"
            ? `${session.scope_document_count} selected document(s)`
            : "Whole workspace"}
        </button>
      )}

      <label className={`${chip} pr-1`}>
        <Cpu className="h-3.5 w-3.5" aria-hidden />
        <span className="sr-only">Model for this conversation</span>
        <select
          value={selectedModel}
          disabled={models.isLoading || chooseModel.isPending}
          onChange={(event) => chooseModel.mutate(event.target.value)}
          className="max-w-[14rem] bg-slate-900 text-slate-100 text-xs font-semibold outline-none cursor-pointer rounded px-1.5 py-0.5 border border-slate-700"
        >
          <option value="" className="bg-slate-900 text-slate-100 py-1">Default{defaultModel ? ` (${defaultModel})` : ""}</option>
          {(models.data ?? [])
            .filter((option) => !option.is_workspace_default)
            .map((option) => (
              <option key={option.model} value={option.model} className="bg-slate-900 text-slate-100 py-1">
                {option.model}
              </option>
            ))}
        </select>
      </label>
      {activePrice && (
        <span className="text-[10px] text-muted-foreground" title="Price book rate">
          {activePrice}
        </span>
      )}

      <button
        type="button"
        className={chip}
        aria-expanded={panel === "templates"}
        onClick={() => setPanel(panel === "templates" ? null : "templates")}
      >
        <BookText className="h-3.5 w-3.5" aria-hidden />
        Templates
      </button>

      <button type="button" className={`${chip} ml-auto`} onClick={() => void exportMarkdown()}>
        <Download className="h-3.5 w-3.5" aria-hidden />
        Export
      </button>

      {panel === "scope" && !isDocument && (
        <div className={PANEL_CLASS} role="dialog" aria-label="Choose documents to search">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-bold">Search which documents?</p>
            <button type="button" onClick={() => setPanel(null)} aria-label="Close" className="rounded p-1 hover:bg-muted">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <label className="relative mb-2 block">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              type="search"
              value={docSearch}
              onChange={(event) => setDocSearch(event.target.value)}
              placeholder="Filter processed documents"
              className="h-8 w-full rounded-lg border border-border bg-background pl-7 pr-2 text-xs"
            />
          </label>
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {documents.isLoading ? (
              <li className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading documents…
              </li>
            ) : (documents.data?.items ?? []).length === 0 ? (
              <li className="p-2 text-xs text-muted-foreground">No processed documents match.</li>
            ) : (
              (documents.data?.items ?? []).map((item) => (
                <li key={item.id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-muted">
                    <input
                      type="checkbox"
                      checked={picked.includes(item.id)}
                      onChange={() => toggle(item.id)}
                      disabled={!picked.includes(item.id) && picked.length >= maxDocuments}
                    />
                    <span className="truncate">{item.original_filename}</span>
                  </label>
                </li>
              ))
            )}
          </ul>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {picked.length} of at most {maxDocuments} selected. Comparing two to four documents
            works best.
          </p>
          <div className="mt-2 flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className={chip}
              disabled={saveScope.isPending}
              onClick={() => saveScope.mutate({ mode: "WORKSPACE", ids: [] })}
            >
              Whole workspace
            </button>
            <button
              type="button"
              disabled={saveScope.isPending || picked.length === 0}
              onClick={() => saveScope.mutate({ mode: "SELECTED", ids: picked })}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50"
            >
              {saveScope.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              Search selected ({picked.length})
            </button>
          </div>
        </div>
      )}

      {panel === "templates" && (
        <div className={PANEL_CLASS} role="dialog" aria-label="Prompt templates">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-bold">Prompt templates</p>
            <button type="button" onClick={() => setPanel(null)} aria-label="Close" className="rounded p-1 hover:bg-muted">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <ul className="max-h-48 space-y-1 overflow-y-auto">
            {templates.isLoading ? (
              <li className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading…
              </li>
            ) : (templates.data ?? []).length === 0 ? (
              <li className="p-2 text-xs text-muted-foreground">No templates yet.</li>
            ) : (
              (templates.data ?? []).map((template) => (
                <li key={template.id} className="flex items-start gap-2 rounded px-2 py-1.5 hover:bg-muted">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => {
                      onInsertTemplate(template.body);
                      setPanel(null);
                    }}
                  >
                    <span className="block truncate text-xs font-semibold">{template.name}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{template.body}</span>
                  </button>
                  {canWriteTemplates && (
                    <button
                      type="button"
                      onClick={() => archiveTemplate.mutate(template)}
                      className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      aria-label={`Remove template ${template.name}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              ))
            )}
          </ul>
          {canWriteTemplates && (
            <form
              className="mt-3 space-y-2 border-t border-border pt-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (newName.trim() && newBody.trim()) {
                  createTemplate.mutate();
                }
              }}
            >
              <input
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="Template name"
                maxLength={80}
                className="h-8 w-full rounded-lg border border-border bg-background px-2 text-xs"
              />
              <textarea
                value={newBody}
                onChange={(event) => setNewBody(event.target.value)}
                placeholder="e.g. List every payment term, due date and penalty clause with page references."
                maxLength={8000}
                rows={3}
                className="w-full rounded-lg border border-border bg-background px-2 py-1.5 text-xs"
              />
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={createTemplate.isPending || !newName.trim() || !newBody.trim()}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {createTemplate.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Plus className="h-3.5 w-3.5" aria-hidden />
                  )}
                  Save template
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </div>
  );
};

export default ChatSessionBar;
