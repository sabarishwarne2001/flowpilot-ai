import React, { useState, useCallback, useEffect } from "react";

import { Link, useParams } from "react-router-dom";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { toast } from "sonner";

import {
  ArrowLeft,
  Database,
  Eye,
  FileCheck,
  FileText,
  MessageSquare,
  RefreshCw,
} from "lucide-react";

import { workItemApi } from "@/services/api/workItem";
import { assistantApi } from "@/services/api/assistant";

import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { ErrorState } from "@/components/common/ErrorState";
import ChatPanel from "@/components/assistant/ChatPanel";

import { formatBytes, formatDateTime } from "@/utils/formatters";

import { ApiError } from "@/services/api/client";

import { ROUTES } from "@/constants/routes";

import type { WorkItemStatus } from "@/types/workItem";

/* ============================================================================
   Types
============================================================================ */

type DetailTab = "summary" | "entities" | "ocr" | "chat";

/* ============================================================================
   Query Key Factory
============================================================================ */

const WORK_ITEM_DETAILS_QUERY_KEY = (id: string | undefined) =>
  ["work-item-details", id] as const;

/* ============================================================================
   Status Badge Styling
============================================================================ */

const STATUS_BADGE_MAP: Record<WorkItemStatus, string> = {
  QUEUED: "bg-primary/10 text-primary border-primary/20",

  PROCESSING: "bg-amber-500/10 text-amber-500 border-amber-500/20",

  COMPLETED: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",

  FAILED: "bg-destructive/10 text-destructive border-destructive/20",
};

/* ============================================================================
   Detail Tabs
============================================================================ */

const DETAIL_TABS = [
  {
    value: "summary",
    label: "Summary",
    icon: FileCheck,
  },
  {
    value: "entities",
    label: "Entities",
    icon: Database,
  },
  {
    value: "ocr",
    label: "OCR",
    icon: Eye,
  },
  {
    value: "chat",
    label: "Chat",
    icon: MessageSquare,
  },
] as const;

/* ============================================================================
   Component
============================================================================ */

export const WorkItemDetails: React.FC = () => {
  const { id } = useParams<{
    id: string;
  }>();

  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<DetailTab>("summary");

  const [conversationId, setConversationId] = useState<string>();

  const queryKey = WORK_ITEM_DETAILS_QUERY_KEY(id);

  const {
    data: workItem,
    isLoading,
    error,
  } = useQuery({
    queryKey,

    queryFn: () => {
      if (!id) {
        throw new Error("Missing work item id.");
      }

      return workItemApi.getWorkItemDetails(id);
    },

    enabled: !!id,

    staleTime: 30_000,

    refetchOnWindowFocus: true,

    placeholderData: (previousData) => previousData,

    refetchInterval: (query) => {
      const item = query.state.data;

      if (!item) {
        return false;
      }

      return item.status === "QUEUED" || item.status === "PROCESSING"
        ? 2_000
        : false;
    },
  });


  /* ==========================================================================
       Reprocess Mutation
    ========================================================================== */

  const { mutate: reprocessDocument, isPending: isReprocessing } = useMutation({
    mutationFn: workItemApi.reprocessWorkItem,

    onSuccess: async () => {
      toast.success("Document queued for reprocessing.");

      await queryClient.invalidateQueries({
        queryKey,
      });
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message ?? "Unable to reprocess document.");

        return;
      }

      toast.error("Unexpected server error.");
    },
  });

  /* ==========================================================================
       Callbacks
    ========================================================================== */

  const handleRetry = useCallback((): void => {
    if (!id) {
      return;
    }

    reprocessDocument(id);
  }, [id, reprocessDocument]);


  /* ==========================================================================
    Document Conversation
  ========================================================================== */

  useEffect(() => {
    if (!id) {
      return;
    }

    const createConversation = async () => {
      try {
        const conversation =
          await assistantApi.getDocumentConversation(id);

        setConversationId(conversation.id);
      } catch (error) {
        console.error(error);

        toast.error(
          "Unable to initialize document assistant."
        );
      }
    };

    createConversation();
  }, [id]);


  /* ==========================================================================
       Loading State
    ========================================================================== */

  if (isLoading) {
    return (
      <div className="space-y-6">
        <header className="flex items-center space-x-3">
          <div className="h-9 w-9 animate-pulse rounded-lg bg-muted/40" />
          <div className="h-5 w-48 animate-pulse rounded bg-muted/60" />
        </header>

        <section className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <SkeletonCard />
          </div>

          <div className="lg:col-span-8">
            <SkeletonCard />
          </div>
        </section>
      </div>
    );
  }

  /* ==========================================================================
       Error State
    ========================================================================== */

  if (error || !workItem) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center p-6">
        <ErrorState
          title="Failed to retrieve document"
          description="The requested document could not be loaded. It may have been removed or is temporarily unavailable."
          onRetry={async () => {
            await queryClient.invalidateQueries({
              queryKey,
            });
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ======================================================
            Back Navigation
        ====================================================== */}

      <header className="flex items-center justify-between">
        <Link
          to={ROUTES.WORK_ITEMS}
          className="inline-flex items-center rounded-lg border border-border bg-card px-3 py-2 text-sm font-semibold text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Documents
        </Link>

        {workItem.status === "FAILED" && (
          <button
            type="button"
            onClick={handleRetry}
            disabled={isReprocessing}
            className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-all hover:bg-primary/95 disabled:pointer-events-none disabled:opacity-50"
          >
            <RefreshCw
              className={`mr-2 h-4 w-4 ${isReprocessing ? "animate-spin" : ""}`}
            />
            Retry Processing
          </button>
        )}
      </header>

      {/* ======================================================
            Header Card
        ====================================================== */}

      <section className="rounded-xl border border-border/60 bg-card p-6 shadow-sm dark:border-border/40">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-primary/10 p-3 text-primary">
                <FileText className="h-6 w-6" />
              </div>

              <div className="min-w-0">
                <h1
                  className="truncate text-2xl font-extrabold tracking-tight"
                  title={workItem.original_filename}
                >
                  {workItem.original_filename}
                </h1>

                <p className="mt-1 text-sm font-medium text-muted-foreground">
                  AI extraction results and document metadata
                </p>
              </div>
            </div>
          </div>

          <span
            className={`
                inline-flex
                items-center
                self-start
                rounded-full
                border
                px-3
                py-1
                text-xs
                font-black
                uppercase
                tracking-wide
                ${STATUS_BADGE_MAP[workItem.status]}
              `}
          >
            {workItem.status}
          </span>
        </div>
      </section>

      {/* ======================================================
            Main Layout
        ====================================================== */}

      <div className="grid gap-6 xl:grid-cols-12">
        {/* ======================================================
              Left Metadata Panel
          ====================================================== */}

        <aside className="space-y-6 xl:col-span-4">
          <section className="rounded-xl border border-border/60 bg-card p-6 shadow-sm dark:border-border/40">
            <h2 className="mb-5 text-lg font-bold">Document Information</h2>

            <dl className="space-y-4">
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  File Type
                </dt>

                <dd className="mt-1 text-sm font-semibold">
                  {workItem.file_type}
                </dd>
              </div>

              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  File Size
                </dt>

                <dd className="mt-1 text-sm font-semibold">
                  {formatBytes(workItem.file_size)}
                </dd>
              </div>

              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  Created
                </dt>

                <dd className="mt-1 text-sm font-semibold">
                  {formatDateTime(workItem.created_at)}
                </dd>
              </div>

              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  Last Updated
                </dt>

                <dd className="mt-1 text-sm font-semibold">
                  {formatDateTime(workItem.updated_at)}
                </dd>
              </div>

              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  Stored Filename
                </dt>

                <dd
                  className="mt-1 break-all text-xs font-medium text-muted-foreground"
                  title={workItem.stored_filename}
                >
                  {workItem.stored_filename}
                </dd>
              </div>

              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  Work Item ID
                </dt>

                <dd
                  className="mt-1 break-all font-mono text-xs text-muted-foreground"
                  title={workItem.id}
                >
                  {workItem.id}
                </dd>
              </div>
            </dl>
          </section>
        </aside>

        {/* ======================================================
              Right Analysis Panel
          ====================================================== */}

        <section className="xl:col-span-8">
          <div className="overflow-hidden rounded-xl border border-border/60 bg-card shadow-sm dark:border-border/40">
            {/* ======================================================
                  Tabs
              ====================================================== */}

            <nav className="flex border-b border-border/40 bg-muted/10">
              {DETAIL_TABS.map((tab) => {
                const Icon = tab.icon;

                const isActive = activeTab === tab.value;

                return (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => setActiveTab(tab.value)}
                    className={`
                          flex items-center gap-2
                          border-b-2
                          px-5
                          py-3
                          text-sm
                          font-semibold
                          transition-all
                          ${
                            isActive
                              ? "border-primary text-primary"
                              : "border-transparent text-muted-foreground hover:text-foreground"
                          }
                        `}
                  >
                    <Icon className="h-4 w-4" />

                    {tab.label}
                  </button>
                );
              })}
            </nav>

            <div className="p-6">
              {/* ======================================================
                    Summary Tab
                ====================================================== */}

              {activeTab === "summary" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-bold">AI Generated Summary</h2>

                    <p className="mt-1 text-sm text-muted-foreground">
                      High-level overview extracted from the uploaded document.
                    </p>
                  </div>

                  <div className="rounded-lg border border-border/40 bg-muted/10 p-5">
                    {/* TODO:
                          Replace with ReactMarkdown
                          in a future module.
                      */}

                    {workItem.summary ? (
                      <p className="whitespace-pre-wrap text-sm leading-7">
                        {workItem.summary}
                      </p>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        No AI summary is available for this document.
                      </p>
                    )}
                  </div>
                </section>
              )}

              {/* ======================================================
                    Entities Tab
                ====================================================== */}

              {activeTab === "entities" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-bold">Extracted Entities</h2>

                    <p className="mt-1 text-sm text-muted-foreground">
                      Structured data extracted from the document.
                    </p>
                  </div>

                  <div className="overflow-auto rounded-lg border border-border/40 bg-muted/10 p-5">
                    {/* TODO:
                          Replace JSON.stringify()
                          with a reusable JSON
                          Tree Viewer.
                      */}

                    {workItem.extracted_entities ? (
                      <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs leading-6">
                        {JSON.stringify(workItem.extracted_entities, null, 2)}
                      </pre>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        No structured entities were extracted.
                      </p>
                    )}
                  </div>
                </section>
              )}
              {/* ======================================================
                    OCR Tab
                ====================================================== */}

              {activeTab === "ocr" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-bold">
                      OCR & Processing Information
                    </h2>

                    <p className="mt-1 text-sm text-muted-foreground">
                      Technical information captured during the document
                      ingestion pipeline.
                    </p>
                  </div>

                  <div className="rounded-lg border border-border/40 bg-muted/10 p-5">
                    <dl className="grid gap-5 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                          Processing Status
                        </dt>

                        <dd className="mt-2">
                          <span
                            className={`
                                inline-flex
                                items-center
                                rounded-full
                                border
                                px-3
                                py-1
                                text-xs
                                font-black
                                uppercase
                                tracking-wide
                                ${STATUS_BADGE_MAP[workItem.status]}
                              `}
                          >
                            {workItem.status}
                          </span>
                        </dd>
                      </div>

                      <div>
                        <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                          File Format
                        </dt>

                        <dd className="mt-2 text-sm font-semibold">
                          {workItem.file_type}
                        </dd>
                      </div>

                      <div>
                        <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                          Original Filename
                        </dt>

                        <dd
                          className="mt-2 break-all text-sm font-semibold"
                          title={workItem.original_filename}
                        >
                          {workItem.original_filename}
                        </dd>
                      </div>

                      <div>
                        <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                          Uploaded Size
                        </dt>

                        <dd className="mt-2 text-sm font-semibold">
                          {formatBytes(workItem.file_size)}
                        </dd>
                      </div>

                      <div>
                        <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                          Uploaded At
                        </dt>

                        <dd className="mt-2 text-sm font-semibold">
                          {formatDateTime(workItem.created_at)}
                        </dd>
                      </div>

                      <div>
                        <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                          Last Updated
                        </dt>

                        <dd className="mt-2 text-sm font-semibold">
                          {formatDateTime(workItem.updated_at)}
                        </dd>
                      </div>
                    </dl>
                  </div>
                </section>
              )}
              {/* ======================================================
                    AI Assistant Tab
                ====================================================== */}

              {activeTab === "chat" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-bold">AI Assistant</h2>

                    <p className="mt-1 text-sm text-muted-foreground">
                      Ask questions about this document using its processed
                      content and extracted knowledge.
                    </p>
                  </div>

                  {conversationId ? (
                    <ChatPanel
                      mode="document"
                      conversationId={conversationId}
                      workItemId={workItem.id}
                    />
                  ) : (
                    <div className="flex items-center justify-center rounded-lg border border-dashed border-border/60 p-10">
                      <p className="text-sm text-muted-foreground">
                        Initializing document assistant...
                      </p>
                    </div>
                  )}
                </section>
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};

WorkItemDetails.displayName = "WorkItemDetails";

export default WorkItemDetails;
