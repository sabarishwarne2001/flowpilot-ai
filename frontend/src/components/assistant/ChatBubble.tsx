import React from "react";
import { Bot, User, FileText } from "lucide-react";

import type { ConversationMessage, SourceCitation } from "@/types/assistant";

interface ChatBubbleProps {
  /**
   * Message to render.
   */
  readonly message: ConversationMessage;

  /**
   * Fired when the user clicks a citation.
   * Parent ChatPanel owns the CitationDrawer state.
   */
  readonly onCitationClick?: (citation: SourceCitation) => void;

  readonly className?: string;
}

/* ============================================================================
 * Constants
 * ========================================================================== */

const INLINE_CITATION_REGEX = /\[(\d+)\]/g;

/* ============================================================================
 * Helper Functions
 * ========================================================================== */

/**
 * Parses assistant messages and converts
 * inline references like [1] into clickable
 * citation buttons.
 */
function renderMessageContent(
  message: ConversationMessage,
  onCitationClick?: (citation: SourceCitation) => void
): React.ReactNode {
  const content = message.content;
  const sources = message.sources ?? [];

  if (sources.length === 0) {
    return (
      <p className="whitespace-pre-wrap break-words text-sm leading-7">
        {content}
      </p>
    );
  }

  const parts = content.split(INLINE_CITATION_REGEX);

  return (
    <p className="whitespace-pre-wrap break-words text-sm leading-7">
      {parts.map((part, index) => {
        if (index % 2 === 1) {
          const citation = sources[Number(part) - 1];

          if (!citation) {
            return `[${part}]`;
          }

          return (
            <button
              key={citation.citation_id}
              type="button"
              onClick={() => onCitationClick?.(citation)}
              className="
                  mx-0.5
                  align-super
                  rounded
                  px-0.5
                  text-[10px]
                  font-black
                  text-primary
                  transition-colors
                  hover:underline
                  focus:outline-none
                  focus:ring-1
                  focus:ring-primary/30
                "
              aria-label={`Open citation ${part}`}
            >
              [{part}]
            </button>
          );
        }

        return <React.Fragment key={`text-${index}`}>{part}</React.Fragment>;
      })}
    </p>
  );
}

/* ============================================================================
 * Component
 * ========================================================================== */

export const ChatBubble: React.FC<ChatBubbleProps> = React.memo(
  ({ message, onCitationClick, className = "" }) => {
    const isUser = message.role === "user";
    return (
      <article
        className={`
          flex
          w-full
          items-start
          gap-3
          ${isUser ? "justify-end" : "justify-start"}
          ${className}
        `}
        aria-label={`${message.role} message`}
      >
        {/* ======================================================
            Assistant Avatar
        ====================================================== */}

        {!isUser && (
          <div
            className="
              flex
              h-10
              w-10
              flex-shrink-0
              items-center
              justify-center
              rounded-full
              border
              border-primary/20
              bg-primary/10
              text-primary
            "
            aria-hidden="true"
          >
            <Bot className="h-5 w-5" />
          </div>
        )}

        {/* ======================================================
            Chat Bubble
        ====================================================== */}

        <div
          className={`
            flex
            max-w-[75%]
            flex-col
            rounded-2xl
            border
            p-4
            shadow-sm
            ${
              isUser
                ? `
                  rounded-tr-none
                  border-primary/20
                  bg-primary/5
                  text-right
                `
                : `
                  rounded-tl-none
                  border-border/40
                  bg-card
                  text-left
                `
            }
          `}
        >
          {/* ======================================================
              Header
          ====================================================== */}

          <header className="mb-3 flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              {isUser ? (
                <User className="h-4 w-4 text-primary" />
              ) : (
                <Bot className="h-4 w-4 text-primary" />
              )}

              <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {isUser ? "You" : "FlowPilot AI"}
              </span>
            </div>

            <time
              className="text-[10px] font-medium text-muted-foreground"
              dateTime={message.created_at}
            >
              {new Date(message.created_at).toLocaleString()}
            </time>
          </header>

          {/* ======================================================
              Message Content
          ====================================================== */}

          <div className="text-foreground">
            {renderMessageContent(message, onCitationClick)}
          </div>
          {/* ======================================================
              Future Agent Metadata
          ====================================================== */}

          {(message.agent_name || message.model) && (
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/20 pt-3">
              {message.agent_name && (
                <span className="rounded-full border border-border bg-muted/40 px-2 py-1 text-[10px] font-semibold text-muted-foreground">
                  Agent: {message.agent_name}
                </span>
              )}

              {message.model && (
                <span className="rounded-full border border-border bg-muted/40 px-2 py-1 text-[10px] font-semibold text-muted-foreground">
                  Model: {message.model}
                </span>
              )}
            </div>
          )}

          {/* ======================================================
              Source Citations
          ====================================================== */}

          {!isUser && (message.sources ?? []).length > 0 && (
            <section
              className="mt-4 border-t border-border/20 pt-4"
              aria-label="Referenced source documents"
            >
              <div className="flex flex-wrap gap-2">
                {(message.sources ?? []).map((citation, index) => (
                  <button
                    key={citation.citation_id}
                    type="button"
                    onClick={() => onCitationClick?.(citation)}
                    className="
                          inline-flex
                          items-center
                          gap-1.5
                          rounded-lg
                          border
                          border-border/40
                          bg-muted/40
                          px-2.5
                          py-1.5
                          text-[10px]
                          font-bold
                          text-muted-foreground
                          transition-colors
                          hover:bg-muted
                          hover:text-foreground
                          focus:outline-none
                          focus:ring-2
                          focus:ring-primary/20
                        "
                    aria-label={`Open citation ${index + 1}`}
                  >
                    <FileText className="h-3 w-3 flex-shrink-0" />

                    <span>[{index + 1}]</span>

                    <span className="max-w-[140px] truncate">
                      {citation.document_display_name ??
                        citation.original_filename}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
          {/* ======================================================
              Future Metadata Footer
          ====================================================== */}

          <footer className="mt-4 border-t border-border/20 pt-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground">
              <span>
                {(message.sources ?? []).length > 0
                  ? `${(message.sources ?? []).length} source${
                      (message.sources ?? []).length === 1 ? "" : "s"
                    } referenced`
                  : "No document sources"}
              </span>

              <span>
                {message.created_at
                  ? new Date(message.created_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })
                  : ""}
              </span>
            </div>
          </footer>
        </div>

        {/* ======================================================
            User Avatar
        ====================================================== */}

        {isUser && (
          <div
            className="
              flex
              h-10
              w-10
              flex-shrink-0
              items-center
              justify-center
              rounded-full
              border
              border-primary/20
              bg-primary/10
              text-primary
            "
            aria-hidden="true"
          >
            <User className="h-5 w-5" />
          </div>
        )}
      </article>
    );
  }
);

ChatBubble.displayName = "ChatBubble";

export default ChatBubble;
