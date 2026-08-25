import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PanelRightClose, PanelRightOpen, Send, Square } from "lucide-react";

import ContextPressureBar from "@/components/assistant/ContextPressureBar";
import MessageStream from "@/components/chat/MessageStream";
import PdfViewer from "@/components/pdf/PdfViewer";
import ProvenanceDrawer from "@/components/provenance/ProvenanceDrawer";
import UploadDropzone from "@/components/upload/UploadDropzone";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { getDocumentSettings } from "@/services/api/document-settings";
import { settingsKeys } from "@/services/api/queryKeys";
import { streamAssistantMessage } from "@/services/streaming/resumableStream";
import type {
  CitationEnvelope,
  CitationSource,
  ConnectionState,
  DoneFrame,
  ErrorFrame,
  StartFrame,
} from "@/services/streaming/resumableStream";

interface TurnState {
  readonly content: string;
  readonly start: StartFrame | null;
  readonly citations: CitationEnvelope | null;
  readonly done: DoneFrame | null;
  readonly error: ErrorFrame | null;
  readonly connection: ConnectionState;
}

const EMPTY_TURN: TurnState = {
  content: "",
  start: null,
  citations: null,
  done: null,
  error: null,
  connection: "closed",
};

export interface AssistantCanvasProps {
  readonly conversationId: string;
  readonly workItemId?: string | null;
}

export const AssistantCanvas: React.FC<AssistantCanvasProps> = ({
  conversationId,
  workItemId = null,
}) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";

  const [turn, setTurn] = useState<TurnState>(EMPTY_TURN);
  const [prompt, setPrompt] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeClaimId, setActiveClaimId] = useState<string | null>(null);
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);
  const [viewerOpen, setViewerOpen] = useState(true);
  const [viewerDocId, setViewerDocId] = useState<string | null>(workItemId);

  const contentRef = useRef("");
  const frameRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const { data: documentSettings } = useQuery({
    queryKey: settingsKeys.document(workspaceId),
    queryFn: () => getDocumentSettings(workspaceId),
    enabled: Boolean(workspaceId),
    staleTime: 5 * 60 * 1000,
  });

  const scheduleFlush = useCallback(() => {
    if (frameRef.current !== null) {
      return;
    }
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      setTurn((previous) => ({ ...previous, content: contentRef.current }));
    });
  }, []);

  useEffect(
    () => () => {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
      }
      abortRef.current?.abort();
    },
    [],
  );

  const streaming =
    turn.connection === "streaming" ||
    turn.connection === "connecting" ||
    turn.connection === "reconnecting";

  const send = useCallback(async () => {
    const content = prompt.trim();
    if (!content || streaming || !workspaceId) {
      return;
    }

    setPrompt("");
    contentRef.current = "";
    setTurn({ ...EMPTY_TURN, connection: "connecting" });
    setActiveClaimId(null);
    setActiveChunkId(null);

    const controller = new AbortController();
    abortRef.current = controller;

    await streamAssistantMessage({
      workspaceId,
      conversationId,
      content,
      signal: controller.signal,
      handlers: {
        onStart: (start) => setTurn((p) => ({ ...p, start })),

        onToken: (text) => {
          contentRef.current += text;
          scheduleFlush();
        },

        onCitations: (citations) =>
          setTurn((p) => ({ ...p, citations, content: contentRef.current })),

        onDone: (done) =>
          setTurn((p) => ({ ...p, done, content: contentRef.current })),

        onError: (error) =>
          setTurn((p) => ({ ...p, error, content: contentRef.current })),

        onConnectionChange: (connection) =>
          setTurn((p) => ({ ...p, connection })),
      },
    });
  }, [conversationId, prompt, scheduleFlush, streaming, workspaceId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const sourcesForViewer = useMemo<CitationSource[]>(() => {
    if (!turn.citations || !viewerDocId) {
      return [];
    }
    return turn.citations.claims
      .flatMap((claim) => claim.sources)
      .filter((source) => source.work_item_id === viewerDocId);
  }, [turn.citations, viewerDocId]);

  const handleCitationClick = useCallback(
    (claimId: string) => {
      setActiveClaimId(claimId);
      setDrawerOpen(true);

      const claim = turn.citations?.claims.find((c) => c.claim_id === claimId);
      const best = claim?.sources
        .slice()
        .sort((a, b) => a.rank - b.rank)
        .find((source) => source.bbox !== null && source.page_number !== null);

      if (best) {
        setViewerDocId(best.work_item_id);
        setActiveChunkId(best.chunk_id);
        setViewerOpen(true);
      }
    },
    [turn.citations],
  );

  const handleOpenSource = useCallback((source: CitationSource) => {
    setViewerDocId(source.work_item_id);
    setActiveChunkId(source.chunk_id);
    setViewerOpen(true);
    setDrawerOpen(false);
  }, []);

  if (!workspace) {
    return null;
  }

  const allowedTypes = documentSettings?.allowed_file_types
    ? documentSettings.allowed_file_types
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean)
    : undefined;

  return (
    <div className="flex h-full min-h-0">
      <section
        className="flex min-w-0 flex-1 flex-col"
        aria-label="Conversation"
      >
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {!turn.start && !turn.content && (
            <UploadDropzone
              workspaceId={workspaceId}
              {...(documentSettings?.max_upload_size
                ? { maxSizeMb: documentSettings.max_upload_size }
                : {})}
              {...(allowedTypes ? { allowedTypes } : {})}
              onUploaded={(id) => {
                setViewerDocId(id);
                setViewerOpen(true);
              }}
            />
          )}

          {turn.start && (
            <ContextPressureBar
              passagesIncluded={
                turn.citations?.passages_included ?? turn.start.passages
              }
              passagesDroppedBudget={
                turn.citations?.passages_dropped_budget ?? 0
              }
              passagesDroppedInjection={
                turn.citations?.passages_dropped_injection ?? 0
              }
              warnings={turn.start.warnings}
            />
          )}

          <MessageStream
            content={turn.content}
            start={turn.start}
            citations={turn.citations}
            done={turn.done}
            error={turn.error}
            connection={turn.connection}
            onCitationClick={handleCitationClick}
            activeClaimId={activeClaimId}
          />
        </div>

        <div className="border-t border-border p-3">
          <div className="flex items-end gap-2">
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder="Ask about your documents…"
              disabled={streaming}
              className="min-h-[2.75rem] flex-1 resize-y rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            />

            {streaming ? (
              <button
                type="button"
                onClick={stop}
                aria-label="Stop generating"
                className="rounded-md border border-border p-2.5 hover:bg-muted"
              >
                <Square className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void send()}
                disabled={prompt.trim().length === 0}
                aria-label="Send"
                className="rounded-md bg-primary p-2.5 text-primary-foreground hover:opacity-90 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            )}

            <button
              type="button"
              onClick={() => setViewerOpen((open) => !open)}
              aria-label={viewerOpen ? "Hide document" : "Show document"}
              className="rounded-md border border-border p-2.5 hover:bg-muted"
            >
              {viewerOpen ? (
                <PanelRightClose className="h-4 w-4" />
              ) : (
                <PanelRightOpen className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      </section>

      {viewerOpen && viewerDocId && (
        <aside
          className="hidden w-1/2 min-w-0 border-l border-border lg:block"
          aria-label="Source document"
        >
          <PdfViewer
            workspaceId={workspaceId}
            workItemId={viewerDocId}
            sources={sourcesForViewer}
            activeChunkId={activeChunkId}
            onBoxClick={(source) => setActiveChunkId(source.chunk_id)}
            className="h-full"
          />
        </aside>
      )}

      <ProvenanceDrawer
        open={drawerOpen}
        envelope={turn.citations}
        activeClaimId={activeClaimId}
        onClose={() => setDrawerOpen(false)}
        onOpenSource={handleOpenSource}
      />
    </div>
  );
};

export default AssistantCanvas;
