import React, { useMemo } from "react";
import {
  AlertTriangle,
  CircleSlash,
  Loader2,
  ShieldCheck,
  WifiOff,
} from "lucide-react";

import MarkdownRenderer from "@/components/chat/MarkdownRenderer";
import type {
  CitationEnvelope,
  ConnectionState,
  DoneFrame,
  ErrorFrame,
  StartFrame,
} from "@/services/streaming/resumableStream";

interface FinishPresentation {
  readonly label: string;
  readonly detail: string;
  readonly tone: "warning" | "danger" | "muted";
}

const FINISH_PRESENTATION: Record<string, FinishPresentation> = {
  output_ceiling: {
    label: "Cut off at the length limit",
    detail:
      "The model reached its maximum output length. Ask a narrower question to get the rest.",
    tone: "warning",
  },
  spend_limit: {
    label: "Stopped at the usage limit",
    detail:
      "This workspace reached its AI usage limit mid-answer. Raise the limit to continue.",
    tone: "danger",
  },
  provider_error: {
    label: "Ended early",
    detail:
      "The model provider stopped responding. What arrived before that is kept below.",
    tone: "danger",
  },
  client_disconnected: {
    label: "Connection lost",
    detail:
      "The connection closed while the answer was still arriving. This is what was saved.",
    tone: "warning",
  },
};

const resolveFinish = (reason: string | null | undefined): FinishPresentation | null => {
  if (!reason) {
    return null;
  }
  const key = reason.toLowerCase();
  if (key === "completed") {
    return null;
  }
  return (
    FINISH_PRESENTATION[key] ?? {
      label: "Incomplete answer",
      detail: `The generation ended with status "${reason}".`,
      tone: "warning",
    }
  );
};

export interface MessageStreamProps {
  readonly content: string;
  readonly start?: StartFrame | null;
  readonly citations?: CitationEnvelope | null;
  readonly done?: DoneFrame | null;
  readonly error?: ErrorFrame | null;
  readonly connection?: ConnectionState;
  readonly onCitationClick?: (claimId: string) => void;
  readonly activeClaimId?: string | null;
}

export const MessageStream: React.FC<MessageStreamProps> = ({
  content,
  start = null,
  citations = null,
  done = null,
  error = null,
  connection = "closed",
  onCitationClick,
  activeClaimId = null,
}) => {
  const isStreaming = connection === "streaming" || connection === "connecting";
  const isReconnecting = connection === "reconnecting";

  const finish = useMemo(
    () => resolveFinish(done?.finish_reason ?? citations?.finish_reason ?? null),
    [done, citations],
  );

  const approxTokens = useMemo(
    () => (content.length === 0 ? 0 : Math.max(1, Math.round(content.length / 4))),
    [content],
  );

  const claims = citations?.claims ?? [];
  const showCursor = isStreaming && content.length > 0;

  return (
    <div className="space-y-3">
      {isReconnecting && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
        >
          <WifiOff className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            Connection dropped. Picking up where the answer left off — it
            won&apos;t be generated twice.
          </span>
        </div>
      )}

      {content.length === 0 && isStreaming && (
        <div
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          <span>
            {start
              ? `Reading ${start.passages} ${start.passages === 1 ? "passage" : "passages"}…`
              : "Thinking…"}
          </span>
        </div>
      )}

      {content.length > 0 && (
        <div className="relative">
          <MarkdownRenderer
            content={content}
            claims={claims}
            {...(onCitationClick ? { onCitationClick } : {})}
            activeClaimId={activeClaimId}
          />
          {showCursor && (
            <span
              aria-hidden="true"
              className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-foreground align-text-bottom"
            />
          )}
        </div>
      )}

      {start && start.warnings.length > 0 && (
        <ul className="space-y-1">
          {start.warnings.map((warning) => (
            <li
              key={warning}
              className="flex items-start gap-2 text-xs text-muted-foreground"
            >
              <AlertTriangle
                className="mt-0.5 h-3.5 w-3.5 shrink-0"
                aria-hidden="true"
              />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}

      {finish && (
        <div
          role="note"
          className={[
            "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
            finish.tone === "danger"
              ? "border-destructive/40 bg-destructive/5 text-destructive"
              : "border-border bg-muted/40 text-muted-foreground",
          ].join(" ")}
        >
          <CircleSlash className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            <strong className="font-medium">{finish.label}.</strong>{" "}
            {finish.detail}
          </span>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {error.message}
            {typeof error.retry_after === "number" && (
              <> Try again in {error.retry_after}s.</>
            )}
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {content.length > 0 && (
          <span title="Approximate. Billed usage is measured by the provider.">
            ~{approxTokens.toLocaleString()} tokens
          </span>
        )}

        {start && (
          <span>
            {start.provider} · {start.model}
          </span>
        )}

        {citations?.context_hash && citations.audit_log_id && (
          <span className="inline-flex items-center gap-1">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Sealed
          </span>
        )}

        {(done?.usage_estimated ?? citations?.usage_estimated) && (
          <span title="The provider returned no token counts, so usage was estimated.">
            Usage estimated
          </span>
        )}
      </div>
    </div>
  );
};

export default MessageStream;
