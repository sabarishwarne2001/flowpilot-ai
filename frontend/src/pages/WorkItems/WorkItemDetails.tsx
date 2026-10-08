import React, { useState, useCallback, useEffect } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  CalendarClock,
  BookUp,
  Database,
  Eye,
  FileCheck,
  FileSearch,
  FileText,
  MessageSquare,
  RefreshCw,
} from "lucide-react";

import { workItemApi } from "@/services/api/workItem";
import { assistantApi } from "@/services/api/assistant";
import { useActiveWorkspace, useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { useTenant } from "@/hooks/useTenant"; // Imported to resolve slug links
import { workItemKeys, keepPreviousWithinWorkspace } from "@/services/api/queryKeys";

import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { ErrorState } from "@/components/common/ErrorState";
import ChatPanel from "@/components/assistant/ChatPanel";
import StartRedactionButton from "@/components/redaction/StartRedactionButton";
// ARCH41-S3:provenance-import
import ExtractionMemoryProvenance from "@/components/extractionMemory/ExtractionMemoryProvenance";
// ARCH42-S2:entity-chips
import EntityChips from "@/components/entities/EntityChips";
// ARCH44-S2:document-tables
import DocumentTables from "@/components/tables/DocumentTables";
// ARCH45-S2:document-comparisons
import DocumentComparisons from "@/components/corroboration/DocumentComparisons";
// ARCH46-S2:document-obligations
import DocumentObligations from "@/components/obligations/DocumentObligations";
// ARCH47-S2:document-postings
import DocumentPostings from "@/components/erp/DocumentPostings";
import { formatBytes, formatDateTime } from "@/utils/formatters";
// N-020 items 6/7: the page beside the extracted fields, correctable in place.
import DocumentPageViewer, { useDocumentEvidence } from "@/components/workItems/DocumentPageViewer";
import ExtractedFieldsPanel from "@/components/workItems/ExtractedFieldsPanel";
import { getDocumentConfidence, getDocumentText, getFieldEditability } from "@/services/api/documentEvidence";
import { verificationPath } from "@/routes/tenantPaths";
import { canEditOwnContent } from "@/permissions/workspacePermissions";
import type { WorkspaceRole } from "@/types/tenancy";
import { ApiError } from "@/services/api/client";
import type { WorkItemStatus } from "@/types/workItem";

type DetailTab = "document" | "summary" | "entities" | "obligations" | "postings" | "ocr" | "chat";

const STATUS_BADGE_MAP: Record<WorkItemStatus, string> = {
  QUEUED: "bg-primary/10 text-primary border-primary/20",
  PROCESSING: "bg-amber-500/10 text-amber-500 border-amber-500/20",
  COMPLETED: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
  FAILED: "bg-destructive/10 text-destructive border-destructive/20",
};

const DETAIL_TABS = [
  { value: "document", label: "Document", icon: FileSearch },
  { value: "summary", label: "Summary", icon: FileCheck },
  { value: "entities", label: "Entities", icon: Database },
  // ARCH46-S2:obligations-tab
  { value: "obligations", label: "Obligations", icon: CalendarClock },
  // ARCH47-S2:postings-tab
  { value: "postings", label: "ERP postings", icon: BookUp },
  { value: "ocr", label: "OCR", icon: Eye },
  { value: "chat", label: "Chat", icon: MessageSquare },
] as const;

export const WorkItemDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const workspaceId = useActiveWorkspaceId();
  const { state: tenantState } = useTenant(); // Retrieve active tenant state for slugs

  const activeWorkspace = useActiveWorkspace();
  const [activeTab, setActiveTab] = useState<DetailTab>("document");
  const [conversationId, setConversationId] = useState<string>();
  const [viewerPage, setViewerPage] = useState(1);
  const [activeField, setActiveField] = useState<string | null>(null);

  useEffect(() => {
    setConversationId(undefined);
    setActiveTab("document");
    setViewerPage(1);
    setActiveField(null);
  }, [workspaceId, id]);

  const queryKey = workItemKeys.detail(workspaceId!, id!);

  const {
    data: workItem,
    isLoading,
    error,
  } = useQuery({
    queryKey,
    queryFn: () => {
      if (!id || !workspaceId) {
        throw new Error("Missing workspaceId or workItemId.");
      }
      return workItemApi.getWorkItemDetails(workspaceId, id);
    },
    enabled: Boolean(workspaceId && id),
    staleTime: 30_000,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousWithinWorkspace(workspaceId!),
    retry: (failureCount, err) => {
      if (err instanceof ApiError && err.status === 404) {
        toast.error("Document not found in this workspace.");
        if (tenantState.status === "ready") {
          navigate(`/${tenantState.organization.organization_slug}/${tenantState.workspace.slug}/work-items`);
        }
        return false;
      }
      return failureCount < 3;
    },
    refetchInterval: (query) => {
      const item = query.state.data;
      if (!item) {return false;}
      return item.status === "QUEUED" || item.status === "PROCESSING" ? 2000 : false;
    },
  });

  const finished = workItem?.status === "COMPLETED";
  const evidence = useDocumentEvidence(workspaceId ?? "", id ?? "", finished);
  const extractedLocations = (evidence.data?.locations ?? []).filter((location) => location.source === "extracted");
  const documentText = useQuery({
    queryKey: ["document-text", workspaceId, id],
    queryFn: () => getDocumentText(workspaceId!, id!),
    enabled: Boolean(workspaceId && id) && activeTab === "ocr" && finished,
    staleTime: 300_000,
  });
  // Phase 1 workbench: verification's confidence for the document and each field.
  const confidence = useQuery({
    queryKey: ["document-confidence", workspaceId, id],
    queryFn: () => getDocumentConfidence(workspaceId!, id!),
    enabled: Boolean(workspaceId && id) && finished,
    staleTime: 30_000,
  });
  const role = (activeWorkspace?.role ?? "VIEWER") as WorkspaceRole;
  // The server decides (review queue, legal hold, role); the viewer only asks before offering Edit.
  const editability = useQuery({
    queryKey: ["work-item-field-editability", workspaceId, id],
    queryFn: () => getFieldEditability(workspaceId!, id!),
    enabled: Boolean(workspaceId && id) && finished && canEditOwnContent(role),
    staleTime: 15_000,
  });
  const canCorrect = finished && canEditOwnContent(role) && editability.data?.editable === true;
  const readOnlyReason = !canEditOwnContent(role)
    ? "You have view-only access to this workspace. Ask a workspace administrator to correct a field."
    : !finished
      ? "Fields can be corrected once processing has finished."
      : editability.data && !editability.data.editable
        ? editability.data.message
        : null;
  const reviewQueueLink =
    editability.data?.code === "REVIEW_PENDING" && tenantState.status === "ready"
      ? verificationPath(tenantState.organization.organization_slug, tenantState.workspace.slug)
      : null;

  const selectField = (field: string, page: number | null) => {
    setActiveField(field);
    if (page !== null) {
      setViewerPage(page);
    }
  };

  const { mutate: reprocessDocument, isPending: isReprocessing } = useMutation({
    mutationFn: (workItemId: string) => workItemApi.reprocessWorkItem(workspaceId!, workItemId),
    onSuccess: async () => {
      toast.success("Document queued for reprocessing.");
      await queryClient.invalidateQueries({ queryKey });
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message ?? "Unable to reprocess document.");
        return;
      }
      toast.error("Unexpected server error.");
    },
  });

  const handleRetry = useCallback((): void => {
    if (!id) {return;}
    reprocessDocument(id);
  }, [id, reprocessDocument]);

  useEffect(() => {
    if (!id || !workspaceId) {return;}

    const createConversation = async () => {
      try {
        const conversation = await assistantApi.getDocumentConversation(workspaceId, id);
        setConversationId(conversation.id);
      } catch (err) {
        console.error(err);
        toast.error("Unable to initialize document assistant.");
      }
    };

    createConversation();
  }, [id, workspaceId]);

  const classification = (workItem?.extracted_entities as Record<string, unknown> | null | undefined)?.classification_details as
    | { document_classification?: string }
    | undefined;
  const documentType = classification?.document_classification ?? null;
  const pageCount = evidence.data?.pages.length ?? null;

  // Construct back navigation path dynamically
  const getBackPath = () => {
    if (tenantState.status !== "ready") {return "#";}
    return `/${tenantState.organization.organization_slug}/${tenantState.workspace.slug}/work-items`;
  };

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

  if (error || !workItem) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center p-6">
        <ErrorState
          title="Failed to retrieve document"
          description="The requested document could not be loaded. It may have been removed or is temporarily unavailable."
          onRetry={async () => {
            await queryClient.invalidateQueries({
              queryKey: queryKey,
            });
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between gap-3">
        <Link
          to={getBackPath()}
          className="fp-btn fp-btn-ghost -ml-2.5"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Documents
        </Link>

        {/* ARCH36-S1:redaction-entry-mount */}
        <div className="flex items-center gap-2">
        {workspaceId && (
          <StartRedactionButton
            workspaceId={workspaceId}
            workItemId={workItem.id}
            mimeType={workItem.file_type}
            status={workItem.status}
          />
        )}
        {workItem.status === "FAILED" && (
          <button
            type="button"
            onClick={handleRetry}
            disabled={isReprocessing}
            className="fp-btn fp-btn-primary"
          >
            <RefreshCw className={`h-4 w-4 ${isReprocessing ? "animate-spin" : ""}`} />
            Retry Processing
          </button>
        )}
        </div>
      </header>

      <section className="fp-card p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-gradient-to-b from-muted to-muted/40 text-primary shadow-inner-highlight">
              <FileText className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h1 className="break-all text-lg font-semibold leading-snug tracking-tight sm:truncate sm:text-xl" title={workItem.original_filename}>
                {workItem.original_filename}
              </h1>
              <h2 className="sr-only">Document Information</h2>
              <dl className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px]">
                {documentType ? (
                  <div className="flex items-center gap-1.5">
                    <dt className="sr-only">Document type</dt>
                    <dd className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-[11.5px] font-medium text-foreground">{documentType}</dd>
                  </div>
                ) : null}
                <div className="flex items-center gap-1.5">
                  <dt className="text-muted-foreground">File Type</dt>
                  <dd className="font-mono text-[12px] text-foreground">{workItem.file_type}</dd>
                </div>
                <div className="flex items-center gap-1.5">
                  <dt className="text-muted-foreground">File Size</dt>
                  <dd className="fp-num font-medium text-foreground">{formatBytes(workItem.file_size)}</dd>
                </div>
                {pageCount ? (
                  <div className="flex items-center gap-1.5">
                    <dt className="text-muted-foreground">Pages</dt>
                    <dd className="fp-num font-medium text-foreground">{pageCount}</dd>
                  </div>
                ) : null}
                <div className="flex items-center gap-1.5">
                  <dt className="text-muted-foreground">Created</dt>
                  <dd className="fp-num font-medium text-foreground">{formatDateTime(workItem.created_at)}</dd>
                </div>
                <div className="flex items-center gap-1.5">
                  <dt className="text-muted-foreground">Last Updated</dt>
                  <dd className="fp-num font-medium text-foreground">{formatDateTime(workItem.updated_at)}</dd>
                </div>
              </dl>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-1.5 sm:justify-end">
            {confidence.data && confidence.data.confidence !== null ? (
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                  confidence.data.confidence >= 0.9
                    ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : confidence.data.confidence >= 0.6
                      ? "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300"
                      : "border-destructive/30 bg-destructive/10 text-destructive"
                }`}
                title={confidence.data.reason ?? "Verified by independent agents"}
              >
                Verified {Math.round(confidence.data.confidence * 100)}%
              </span>
            ) : null}
            {confidence.data?.in_review ? (
              <span className="inline-flex items-center rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] font-semibold text-amber-800 dark:text-amber-300" title={confidence.data.reason ?? undefined}>
                In review
              </span>
            ) : null}
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${STATUS_BADGE_MAP[workItem.status]}`}>
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
              {workItem.status}
            </span>
          </div>
        </div>
        {workItem.duplicate_of && (
          <p
            role="note"
            className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-3 py-2 text-[13px] leading-relaxed text-amber-800 dark:text-amber-200"
          >
            <span className="font-semibold">Duplicate upload.</span> This file is identical to{" "}
            <Link
              to={`${getBackPath()}/${workItem.duplicate_of.id}`}
              className="font-medium underline underline-offset-2 hover:no-underline"
            >
              {workItem.duplicate_of.original_filename}
            </Link>
            , already in this workspace. Delete this copy if it was uploaded by mistake.
          </p>
        )}
        {/* HARDENING-T1:D4. Identifiers belong in a support drawer, not the
            primary card. The storage key is no longer sent at all. */}
            <details className="group mt-4 rounded-lg border border-border/70 bg-muted/30 px-3 py-2 text-xs">
              <summary className="cursor-pointer select-none font-semibold text-muted-foreground hover:text-foreground">
                Technical details
              </summary>
              <dl className="mt-2 space-y-2">
                <div>
                  <dt className="font-semibold uppercase tracking-wide text-muted-foreground">Reference ID</dt>
                  <dd className="mt-1 flex items-center gap-2">
                    <code className="break-all font-mono text-muted-foreground">{workItem.id}</code>
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard
                          ?.writeText(workItem.id)
                          .then(() => toast.success("Reference ID copied."))
                          .catch(() => toast.error("Copy failed; select the ID instead."));
                      }}
                      className="shrink-0 rounded-md border border-border bg-card px-1.5 py-0.5 font-medium hover:bg-accent"
                    >
                      Copy
                    </button>
                  </dd>
                </div>
                <p className="text-muted-foreground">Quote this ID when contacting support.</p>
              </dl>
            </details>
      </section>

      <div>
        <section>
          <div className="overflow-hidden fp-card">
            <nav className="flex overflow-x-auto border-b border-border bg-muted/20 px-2">
              {DETAIL_TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => setActiveTab(tab.value)}
                    className={`
                      relative flex shrink-0 items-center gap-2 whitespace-nowrap px-3.5 py-3 text-[13px] font-medium
                      after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full after:transition-colors
                      ${isActive ? "text-foreground after:bg-primary" : "text-muted-foreground after:bg-transparent hover:text-foreground"}
                    `}
                  >
                    <Icon className={`h-4 w-4 ${isActive ? "text-primary" : ""}`} />
                    {tab.label}
                  </button>
                );
              })}
            </nav>

            <div className="p-4 sm:p-5">
              {activeTab === "document" && (
                <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]" aria-label="Document and its data">
                  <div className="min-w-0 xl:sticky xl:top-2 xl:self-start">
                    {finished && workspaceId ? (
                      <DocumentPageViewer
                        workspaceId={workspaceId}
                        workItemId={workItem.id}
                        page={viewerPage}
                        onPageChange={setViewerPage}
                        locations={extractedLocations}
                        activeField={activeField}
                        onSelectField={(field) => setActiveField(field)}
                        canvasClassName="max-h-[75vh] xl:h-[calc(100vh-15rem)] xl:max-h-none"
                      />
                    ) : (
                      <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                        {workItem.status === "FAILED"
                          ? "Processing failed, so there is nothing to show yet. Retry processing above."
                          : "The document is being processed. Its pages and fields appear here when it finishes."}
                      </div>
                    )}
                  </div>
                  <div className="min-w-0">
                    {workspaceId && (
                      <ExtractedFieldsPanel
                        workspaceId={workspaceId}
                        workItemId={workItem.id}
                        entities={workItem.extracted_entities as Record<string, unknown> | null | undefined}
                        locations={extractedLocations}
                        canEdit={canCorrect}
                        readOnlyReason={readOnlyReason}
                        readOnlyLink={reviewQueueLink ? { to: reviewQueueLink, label: "Open the review queue" } : null}
                        activeField={activeField}
                        onSelectField={selectField}
                        confidence={confidence.data}
                      />
                    )}
                  </div>
                </section>
              )}

              {activeTab === "summary" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-semibold">AI Generated Summary</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                      High-level overview extracted from the uploaded document.
                    </p>
                  </div>
                  <div className="rounded-lg border border-border/40 bg-muted/10 p-5">
                    {workItem.summary ? (
                      <p className="whitespace-pre-wrap text-sm leading-7">{workItem.summary}</p>
                    ) : (
                      <p className="text-sm text-muted-foreground">No AI summary is available for this document.</p>
                    )}
                  </div>
                </section>
              )}

              {activeTab === "entities" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-semibold">Extracted Entities</h2>
                    <p className="mt-1 text-sm text-muted-foreground">Structured data extracted from the document.</p>
                  </div>
                  <ExtractionMemoryProvenance workItemId={workItem.id} />
                  <EntityChips workItemId={workItem.id} />
                  <DocumentTables workItemId={workItem.id} />
                  <DocumentComparisons workItemId={workItem.id} />
                  <div className="overflow-auto rounded-lg border border-border/40 bg-muted/10 p-5">
                    {workItem.extracted_entities ? (
                      <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs leading-6">
                        {JSON.stringify(workItem.extracted_entities, null, 2)}
                      </pre>
                    ) : (
                      <p className="text-sm text-muted-foreground">No structured entities were extracted.</p>
                    )}
                  </div>
                </section>
              )}

              {activeTab === "obligations" && <DocumentObligations workItemId={workItem.id} />}
              {activeTab === "postings" && <DocumentPostings workItemId={workItem.id} />}

              {activeTab === "ocr" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-semibold">OCR & Processing Information</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                      The page beside the text read from it, and what the ingestion pipeline recorded.
                    </p>
                  </div>
                  {finished && workspaceId && (
                    <div className="grid gap-4 lg:grid-cols-2">
                      <DocumentPageViewer
                        workspaceId={workspaceId}
                        workItemId={workItem.id}
                        page={viewerPage}
                        onPageChange={setViewerPage}
                        locations={extractedLocations}
                        activeField={activeField}
                        onSelectField={(field) => setActiveField(field)}
                      />
                      <section aria-label="Extracted text" className="rounded-lg border border-border bg-card">
                        <header className="border-b border-border px-3 py-2 text-xs font-semibold text-muted-foreground">
                          Extracted text
                        </header>
                        <div className="max-h-[75vh] overflow-auto p-3">
                          {documentText.isLoading ? (
                            <p className="text-xs text-muted-foreground">Loading the text…</p>
                          ) : documentText.data?.text ? (
                            <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5">
                              {documentText.data.text}
                            </pre>
                          ) : (
                            <p className="text-sm text-muted-foreground">No text was read from this document.</p>
                          )}
                          {documentText.data?.truncated && (
                            <p className="mt-2 text-xs text-muted-foreground">
                              Showing the first {documentText.data.text.length.toLocaleString()} of{" "}
                              {documentText.data.characters.toLocaleString()} characters.
                            </p>
                          )}
                        </div>
                      </section>
                    </div>
                  )}
                  <div className="rounded-lg border border-border/40 bg-muted/10 p-5">
                    <dl className="grid gap-5 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Processing Status</dt>
                        <dd className="mt-2">
                          <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase leading-none tracking-wide ${STATUS_BADGE_MAP[workItem.status]}`}>
                            {workItem.status}
                          </span>
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">File Format</dt>
                        <dd className="mt-2 text-sm font-semibold">{workItem.file_type}</dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Original Filename</dt>
                        <dd className="mt-2 break-all text-sm font-semibold" title={workItem.original_filename}>
                          {workItem.original_filename}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Uploaded Size</dt>
                        <dd className="mt-2 text-sm font-semibold">{formatBytes(workItem.file_size)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Uploaded At</dt>
                        <dd className="mt-2 text-sm font-semibold">{formatDateTime(workItem.created_at)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Last Updated</dt>
                        <dd className="mt-2 text-sm font-semibold">{formatDateTime(workItem.updated_at)}</dd>
                      </div>
                    </dl>
                  </div>
                </section>
              )}

              {activeTab === "chat" && (
                <section className="space-y-4">
                  <div>
                    <h2 className="text-lg font-semibold">AI Assistant</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Ask questions about this document using its processed content and extracted knowledge.
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
                      <p className="text-sm text-muted-foreground">Initializing document assistant...</p>
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
