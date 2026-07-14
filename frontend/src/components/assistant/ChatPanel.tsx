import React, { useState, useEffect, useRef, useCallback } from "react";

import { useForm } from "react-hook-form";

import { zodResolver } from "@hookform/resolvers/zod";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { z } from "zod";

import { Send, Loader2, MessageSquare, ShieldAlert } from "lucide-react";

import { toast } from "sonner";

import { assistantApi } from "@/services/api/assistant";

import { ChatBubble } from "@/components/assistant/ChatBubble";
import { CitationDrawer } from "@/components/assistant/CitationDrawer";
import { SkeletonChat } from "@/components/common/skeletons/SkeletonChat";

import { ApiError } from "@/services/api/client";

import type { ConversationMessage, SourceCitation } from "@/types/assistant";

/* ============================================================================
   Validation
============================================================================ */

const messageFormSchema = z.object({
  message: z.string().trim().min(1, "Message content cannot be empty."),
});

type MessageFormInput = z.infer<typeof messageFormSchema>;

/* ============================================================================
   Props
============================================================================ */

interface ChatPanelProps {
  /**
   * Global assistant
   * or document assistant.
   */
  readonly mode: "global" | "document";

  /**
   * Active conversation.
   */
  readonly conversationId?: string;

  /**
   * Optional Work Item
   * restriction.
   */
  readonly workItemId?: string;

  readonly className?: string;
}

/* ============================================================================
   Component
============================================================================ */

export const ChatPanel: React.FC<ChatPanelProps> = ({
  mode,
  conversationId,
  workItemId: _workItemId,
  className = "",
}) => {
  const queryClient = useQueryClient();

  const scrollAnchorRef = useRef<HTMLDivElement>(null);

  /* ==========================================================================
     Citation Drawer State
  ========================================================================== */

  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  const [activeCitation, setActiveCitation] = useState<SourceCitation | null>(
    null
  );

  /* ==========================================================================
     Local Messages
  ========================================================================== */

  const [localMessages, setLocalMessages] = useState<ConversationMessage[]>([]);
  /* ==========================================================================
     Conversation History
  ========================================================================== */

  const {
    data: historyData,
    isLoading: isHistoryLoading,
    error: historyError,
  } = useQuery({
    queryKey: ["conversation-history", conversationId],

    queryFn: () => {
      if (!conversationId) {
        return Promise.resolve({
          messages: [],
          total_messages: 0,
          has_more: false,
          next_cursor: null,
        });
      }

      return assistantApi.getConversationHistory(conversationId, {
        limit: 100,
      });
    },

    enabled: Boolean(conversationId),

    staleTime: 1000 * 30,

    placeholderData: (previousData) => previousData,
  });

  /* ==========================================================================
     Synchronize Local Messages
  ========================================================================== */

  useEffect(() => {
    if (!historyData) {
      return;
    }

    setLocalMessages([...historyData.messages]);
  }, [historyData]);

  /* ==========================================================================
     Auto Scroll
  ========================================================================== */

  const scrollToBottom = useCallback((smooth = true): void => {
    scrollAnchorRef.current?.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [localMessages, scrollToBottom]);

  /* ==========================================================================
     React Hook Form
  ========================================================================== */

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<MessageFormInput>({
    resolver: zodResolver(messageFormSchema),

    shouldFocusError: true,

    defaultValues: {
      message: "",
    },
  });

  /* ==========================================================================
     Drawer Actions
  ========================================================================== */

  const handleCitationClick = useCallback((citation: SourceCitation): void => {
    setActiveCitation(citation);

    setIsDrawerOpen(true);
  }, []);

  const handleDrawerClose = useCallback((): void => {
    setActiveCitation(null);
    setIsDrawerOpen(false);
  }, []);

  /* ==========================================================================
     Message Submission
  ========================================================================== */

  const handleQuerySubmit = async (data: MessageFormInput): Promise<void> => {
    if (!conversationId) {
      toast.error("Please select or create a conversation first.");
      return;
    }

    const queryContent = data.message.trim();

    if (!queryContent) {
      return;
    }

    // Clear input immediately for responsive UX
    reset();

    const timestamp = new Date().toISOString();

    /* ------------------------------------------------------------------------
       Optimistic User Message
    ------------------------------------------------------------------------ */

    const optimisticUserMessage: ConversationMessage = {
      id: `optimistic-user-${Date.now()}`,
      conversation_id: conversationId,
      sequence_number: localMessages.length + 1,
      role: "user",
      content: queryContent,
      sources: [],
      created_at: timestamp,
      updated_at: timestamp,
    };

    /* ------------------------------------------------------------------------
       Assistant Placeholder
    ------------------------------------------------------------------------ */

    const optimisticAssistantMessage: ConversationMessage = {
      id: `optimistic-assistant-${Date.now()}`,
      conversation_id: conversationId,
      sequence_number: localMessages.length + 2,
      role: "assistant",
      content: "",
      sources: [],
      created_at: timestamp,
      updated_at: timestamp,
    };

    setLocalMessages((previous) => [
      ...previous,
      optimisticUserMessage,
      optimisticAssistantMessage,
    ]);

    scrollToBottom(false);

    try {
      await assistantApi.sendChatMessage(conversationId, queryContent);

      await queryClient.invalidateQueries({
        queryKey: ["conversation-history", conversationId],
      });

      await queryClient.invalidateQueries({
        queryKey: ["conversations"],
      });
    } catch (error) {
      setLocalMessages((previous) =>
        previous.filter((message) => !message.id.startsWith("optimistic-"))
      );

     if (error instanceof ApiError) {
      switch (error.status) {
        case 429:
          toast.error(
            error.detail ?? "The AI service is temporarily busy."
          );
          break;

        case 503:
          toast.error(
            "The AI service is temporarily unavailable. Please try again later."
          );
          break;

        case 500:
          toast.error(
            "An unexpected server error occurred."
          );
          break;

        default:
          toast.error(
            error.message ?? "Failed to send message."
          );
      }
    } else {
      toast.error(
        "Unable to reach the server."
      );
    }
    }
  };

  /* ==========================================================================
     Derived State
  ========================================================================== */

  const isAssistantResponding = localMessages.some(
    (message) =>
      message.id.startsWith("optimistic-assistant-") && message.content === ""
  );
  /* ==========================================================================
     Loading State
  ========================================================================== */

  if (isHistoryLoading && !historyData) {
    return <SkeletonChat messagesCount={5} />;
  }

  /* ==========================================================================
     Error State
  ========================================================================== */

  if (historyError) {
    return (
      <div
        className="
          mx-auto
          flex
          h-[400px]
          max-w-md
          flex-col
          items-center
          justify-center
          rounded-xl
          border
          border-border/40
          bg-card
          p-6
          text-center
          shadow-sm
        "
      >
        <div
          className="
            mb-4
            flex
            h-10
            w-10
            items-center
            justify-center
            rounded-full
            bg-destructive/10
            text-destructive
          "
        >
          <ShieldAlert className="h-5 w-5" />
        </div>

        <h3 className="mb-2 text-sm font-bold">Unable to load conversation</h3>

        <p className="mb-5 text-xs font-medium leading-relaxed text-muted-foreground">
          Conversation history could not be synchronized with the server.
        </p>

        <button
          type="button"
          onClick={() =>
            queryClient.invalidateQueries({
              queryKey: ["conversation-history", conversationId],
            })
          }
          className="
            rounded-lg
            bg-primary
            px-4
            py-2
            text-xs
            font-bold
            text-primary-foreground
            transition-colors
            hover:bg-primary/90
          "
        >
          Retry
        </button>
      </div>
    );
  }

  /* ==========================================================================
     Empty Conversation State
  ========================================================================== */

  if (!conversationId) {
    return (
      <div
        className="
          flex
          min-h-[500px]
          flex-col
          items-center
          justify-center
          p-8
          text-center
        "
      >
        <div
          className="
            mb-5
            flex
            h-14
            w-14
            items-center
            justify-center
            rounded-full
            bg-primary/10
            text-primary
          "
        >
          <MessageSquare className="h-7 w-7" />
        </div>

        <h2 className="mb-2 text-lg font-bold">AI Assistant</h2>

        <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
          {mode === "global"
            ? "Create or select a conversation to start chatting with your knowledge base."
            : "Create a conversation to ask questions about this document."}
        </p>
      </div>
    );
  }

  /* ==========================================================================
     Main Render
  ========================================================================== */

  return (
    <div
      className={`
        relative
        flex
        h-[600px]
        w-full
        flex-col
        overflow-hidden
        rounded-xl
        border
        border-border/40
        bg-card
        ${className}
      `}
    >
      {/* ======================================================
          Conversation Viewport
      ====================================================== */}

      <div
        className="
          flex-1
          space-y-4
          overflow-y-auto
          bg-muted/10
          p-4
        "
      >
        {localMessages.length === 0 ? (
          <div
            className="
              flex
              h-full
              flex-col
              items-center
              justify-center
              text-center
              text-muted-foreground
            "
          >
            <MessageSquare className="mb-3 h-8 w-8 opacity-40" />

            <p className="text-sm font-medium">
              Start the conversation by asking a question about your documents.
            </p>
          </div>
        ) : (
          localMessages.map((message) => {
            const isTypingPlaceholder =
              message.id.startsWith("optimistic-assistant-") &&
              message.content === "";

            if (isTypingPlaceholder) {
              return (
                <div key={message.id} className="flex items-start gap-3">
                  {/* Assistant Avatar */}

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
                  >
                    <MessageSquare className="h-5 w-5" />
                  </div>

                  {/* Typing Bubble */}

                  <div
                    className="
                      rounded-2xl
                      rounded-tl-none
                      border
                      border-border/40
                      bg-card
                      p-4
                      shadow-sm
                    "
                  >
                    <div className="flex gap-2">
                      <span
                        className="
                          h-2
                          w-2
                          animate-bounce
                          rounded-full
                          bg-muted-foreground/60
                        "
                      />

                      <span
                        className="
                          h-2
                          w-2
                          animate-bounce
                          rounded-full
                          bg-muted-foreground/60
                          [animation-delay:150ms]
                        "
                      />

                      <span
                        className="
                          h-2
                          w-2
                          animate-bounce
                          rounded-full
                          bg-muted-foreground/60
                          [animation-delay:300ms]
                        "
                      />
                    </div>
                  </div>
                </div>
              );
            }

            return (
              <ChatBubble
                key={message.id}
                message={message}
                onCitationClick={handleCitationClick}
              />
            );
          })
        )}

        <div ref={scrollAnchorRef} />
      </div>
      {/* ======================================================
          Message Input
      ====================================================== */}

      <form
        onSubmit={handleSubmit(handleQuerySubmit)}
        noValidate
        className="
          border-t
          border-border/40
          bg-card
          p-4
        "
      >
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <input
              {...register("message")}
              type="text"
              autoComplete="off"
              placeholder={
                mode === "global"
                  ? "Ask anything about your knowledge base..."
                  : "Ask about this document..."
              }
              disabled={isSubmitting || isAssistantResponding}
              aria-invalid={errors.message ? "true" : "false"}
              className="
                w-full
                rounded-lg
                border
                border-border
                bg-background
                px-4
                py-3
                text-sm
                transition-colors
                placeholder:text-muted-foreground
                focus:border-primary
                focus:outline-none
                focus:ring-2
                focus:ring-primary/20
                disabled:cursor-not-allowed
                disabled:opacity-50
              "
            />

            {errors.message && (
              <p
                role="alert"
                className="
                  mt-2
                  text-xs
                  font-medium
                  text-destructive
                "
              >
                {errors.message.message}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={isSubmitting || isAssistantResponding}
            className="
              flex
              h-12
              w-12
              items-center
              justify-center
              rounded-lg
              bg-primary
              text-primary-foreground
              shadow-sm
              transition-colors
              hover:bg-primary/90
              disabled:cursor-not-allowed
              disabled:opacity-50
            "
            aria-label="Send message"
          >
            {isSubmitting ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Send className="h-5 w-5" />
            )}
          </button>
        </div>
      </form>
      {/* ======================================================
          Citation Drawer
      ====================================================== */}

      <CitationDrawer
        isOpen={isDrawerOpen}
        onClose={handleDrawerClose}
        citation={activeCitation}
      />
    </div>
  );
};

export default React.memo(ChatPanel);
