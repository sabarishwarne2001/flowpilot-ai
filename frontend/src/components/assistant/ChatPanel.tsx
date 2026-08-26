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
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { assistantKeys, keepPreviousWithinWorkspace } from "@/services/api/queryKeys";
import type { ConversationMessage, SourceCitation } from "@/types/assistant";

const messageFormSchema = z.object({
  message: z.string().trim().min(1, "Message content cannot be empty."),
});

type MessageFormInput = z.infer<typeof messageFormSchema>;

interface ChatPanelProps {
  readonly mode: "global" | "document";
  readonly conversationId?: string;
  readonly workItemId?: string;
  readonly className?: string;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  mode,
  conversationId,
  workItemId: _workItemId,
  className = "",
}) => {
  const queryClient = useQueryClient();
  const workspaceId = useActiveWorkspaceId();
  const scrollAnchorRef = useRef<HTMLDivElement>(null);

  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [activeCitation, setActiveCitation] = useState<SourceCitation | null>(null);
  const [localMessages, setLocalMessages] = useState<ConversationMessage[]>([]);

  const {
    data: historyData,
    isLoading: isHistoryLoading,
    error: historyError,
  } = useQuery({
    queryKey: assistantKeys.history(workspaceId!, conversationId!),
    queryFn: () => {
      if (!conversationId || !workspaceId) {
        return Promise.resolve({
          messages: [],
          total_messages: 0,
          has_more: false,
          next_cursor: null,
        });
      }
      return assistantApi.getConversationHistory(workspaceId, conversationId, {
        limit: 100,
      });
    },
    enabled: Boolean(workspaceId && conversationId),
    staleTime: 1000 * 30,
    placeholderData: keepPreviousWithinWorkspace(workspaceId!),
  });

  useEffect(() => {
    if (!historyData) {
      return;
    }
    setLocalMessages([...historyData.messages]);
  }, [historyData]);

  const scrollToBottom = useCallback((smooth = true): void => {
    scrollAnchorRef.current?.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [localMessages, scrollToBottom]);

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

  const handleCitationClick = useCallback((citation: SourceCitation): void => {
    setActiveCitation(citation);
    setIsDrawerOpen(true);
  }, []);

  const handleDrawerClose = useCallback((): void => {
    setActiveCitation(null);
    setIsDrawerOpen(false);
  }, []);

  const handleQuerySubmit = async (data: MessageFormInput): Promise<void> => {
    if (!conversationId || !workspaceId) {
      toast.error("Please select or create a conversation first.");
      return;
    }

    const queryContent = data.message.trim();
    if (!queryContent) {
      return;
    }

    reset();
    const timestamp = new Date().toISOString();

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
      await assistantApi.sendChatMessage(workspaceId, conversationId, queryContent);

      await queryClient.invalidateQueries({
        queryKey: assistantKeys.history(workspaceId, conversationId),
      });

      await queryClient.invalidateQueries({
        queryKey: assistantKeys.conversations(workspaceId),
      });
    } catch (err) {
      setLocalMessages((previous) =>
        previous.filter((message) => !message.id.startsWith("optimistic-"))
      );

      if (err instanceof ApiError) {
        switch (err.status) {
          case 429:
            toast.error(err.detail ?? "The AI service is temporarily busy.");
            break;
          case 503:
            toast.error("The AI service is temporarily unavailable. Please try again later.");
            break;
          case 500:
            toast.error("An unexpected server error occurred.");
            break;
          default:
            toast.error(err.message ?? "Failed to send message.");
        }
      } else {
        toast.error("Unable to reach the server.");
      }
    }
  };

  const isAssistantResponding = localMessages.some(
    (message) =>
      message.id.startsWith("optimistic-assistant-") && message.content === ""
  );

  if (isHistoryLoading && !historyData) {
    return <SkeletonChat messagesCount={5} />;
  }

  if (historyError) {
    return (
      <div className="mx-auto flex h-[400px] max-w-md flex-col items-center justify-center rounded-xl border border-border/40 bg-card p-6 text-center shadow-sm">
        <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <ShieldAlert className="h-5 w-5" />
        </div>
        <h3 className="mb-2 text-sm font-bold">Unable to load conversation</h3>
        <p className="mb-5 text-xs font-medium leading-relaxed text-muted-foreground">
          Conversation history could not be synchronized with the server.
        </p>
        <button
          type="button"
          onClick={() => {
            if (workspaceId) {
              queryClient.invalidateQueries({
                queryKey: assistantKeys.history(workspaceId, conversationId!),
              });
            }
          }}
          className="rounded-lg bg-primary px-4 py-2 text-xs font-bold text-primary-foreground transition-colors hover:bg-primary/90"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!conversationId) {
    return (
      <div className="flex min-h-[350px] sm:min-h-[450px] flex-col items-center justify-center p-6 text-center">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <MessageSquare className="h-6 w-6" />
        </div>
        <h2 className="mb-1 text-base sm:text-lg font-bold">AI Assistant</h2>
        <p className="max-w-md text-xs sm:text-sm leading-relaxed text-muted-foreground">
          {mode === "global"
            ? "Create or select a conversation to start chatting with your knowledge base."
            : "Create a conversation to ask questions about this document."}
        </p>
      </div>
    );
  }

  return (
    <div className={`relative flex h-full min-h-[450px] w-full flex-col overflow-hidden rounded-xl border border-border/40 bg-card ${className}`}>
      <div className="flex-1 space-y-3.5 overflow-y-auto bg-muted/10 p-3 sm:p-4 min-h-0">
        {localMessages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground">
            <MessageSquare className="mb-2 h-7 w-7 opacity-40" />
            <p className="text-xs sm:text-sm font-medium">
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
                <div key={message.id} className="flex items-start gap-2.5 sm:gap-3">
                  <div className="flex h-8 w-8 sm:h-9 sm:w-9 flex-shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10 text-primary">
                    <MessageSquare className="h-4 w-4" />
                  </div>
                  <div className="rounded-2xl rounded-tl-none border border-border/40 bg-card p-3.5 shadow-sm">
                    <div className="flex gap-1.5">
                      <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/60" />
                      <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:150ms]" />
                      <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:300ms]" />
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

      <form onSubmit={handleSubmit(handleQuerySubmit)} noValidate className="border-t border-border/40 bg-card p-2.5 sm:p-3.5">
        <div className="flex items-end gap-2 sm:gap-3">
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
              className="w-full rounded-lg border border-border bg-background px-3.5 py-2.5 sm:py-3 text-xs sm:text-sm transition-colors placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
            />
            {errors.message && (
              <p role="alert" className="mt-1 text-xs font-medium text-destructive">
                {errors.message.message}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={isSubmitting || isAssistantResponding}
            className="flex h-10 w-10 sm:h-11 sm:w-11 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Send message"
          >
            {isSubmitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
      </form>

      <CitationDrawer
        isOpen={isDrawerOpen}
        onClose={handleDrawerClose}
        citation={activeCitation}
      />
    </div>
  );
};

export default React.memo(ChatPanel);
