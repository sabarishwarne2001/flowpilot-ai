import React, { useState, useEffect, useCallback } from "react";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { useForm } from "react-hook-form";

import { zodResolver } from "@hookform/resolvers/zod";

import { z } from "zod";

import { toast } from "sonner";

import {
  MessageSquare,
  Plus,
  Trash2,
  Edit2,
  Check,
  MoreVertical,
  X,
  Loader2,
  AlertCircle,
  RefreshCw,
} from "lucide-react";

import { assistantApi } from "@/services/api/assistant";

import { ChatPanel } from "@/components/assistant/ChatPanel";

import { SkeletonSidebar } from "@/components/common/skeletons/SkeletonSidebar";

import { ApiError } from "@/services/api/client";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";

import type { ConversationSummary } from "@/types/assistant";

/* ============================================================================
   Constants
============================================================================ */

const CONVERSATIONS_QUERY_KEY = ["conversations"] as const;

/* ============================================================================
   Validation
============================================================================ */

const renameFormSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "Conversation title cannot be empty.")
    .max(150, "Conversation title is too long."),
});

type RenameFormInput = z.infer<typeof renameFormSchema>;

/* ============================================================================
   Rename Form
============================================================================ */

interface RenameFormProps {
  readonly conversation: ConversationSummary;

  readonly onCancel: () => void;

  readonly onSave: (title: string) => Promise<void>;

  readonly isPending: boolean;
}

const RenameForm: React.FC<RenameFormProps> = ({
  conversation,
  onCancel,
  onSave,
  isPending,
}) => {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RenameFormInput>({
    resolver: zodResolver(renameFormSchema),

    defaultValues: {
      title: conversation.title,
    },
  });

  const submit = async (data: RenameFormInput): Promise<void> => {
    await onSave(data.title);
  };

  return (
    <form
      onSubmit={handleSubmit(submit)}
      noValidate
      className="
        flex
        flex-1
        flex-col
        space-y-2
      "
    >
      <div className="flex items-center gap-2">
        <input
          {...register("title")}
          type="text"
          disabled={isPending}
          className={`
            flex-1
            rounded
            border
            bg-background
            px-2
            py-1
            text-xs
            font-medium
            focus:outline-none
            focus:ring-1
            focus:ring-primary/20
            ${errors.title ? "border-destructive" : "border-border"}
          `}
        />
        <div className="flex items-center gap-1">
          <button
            type="submit"
            disabled={isPending}
            className="
              rounded
              p-1
              text-emerald-600
              transition-colors
              hover:bg-emerald-500/10
              disabled:cursor-not-allowed
              disabled:opacity-50
            "
            aria-label="Save conversation title"
            title="Save"
          >
            {isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="h-3.5 w-3.5" />
            )}
          </button>

          <button
            type="button"
            onClick={onCancel}
            disabled={isPending}
            className="
              rounded
              p-1
              text-muted-foreground
              transition-colors
              hover:bg-muted
              hover:text-foreground
              disabled:cursor-not-allowed
              disabled:opacity-50
            "
            aria-label="Cancel rename"
            title="Cancel"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {errors.title && (
        <p
          role="alert"
          className="
            pl-1
            text-[10px]
            font-medium
            text-destructive
          "
        >
          {errors.title.message}
        </p>
      )}
    </form>
  );
};

/* ============================================================================
   Assistant Page
============================================================================ */

export const Assistant: React.FC = () => {
  const queryClient = useQueryClient();

  const [selectedConversationId, setSelectedConversationId] = useState<
    string | null
  >(null);

  const [editingConversationId, setEditingConversationId] = useState<
    string | null
  >(null);

  const [openConversationMenu, setOpenConversationMenu] = useState<
    string | null
  >(null);

  const [conversationToDelete, setConversationToDelete] =
    useState<ConversationSummary | null>(null);

  /* ==========================================================================
       Conversations Query
    ========================================================================== */

  const {
    data: conversations = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: CONVERSATIONS_QUERY_KEY,

    queryFn: assistantApi.getConversations,

    staleTime: 1000 * 15,

    placeholderData: (previousData) => previousData,

    refetchOnWindowFocus: true,
  });
  /* ==========================================================================
       Preserve Selected Conversation
    ========================================================================== */

  useEffect(() => {
    if (conversations.length === 0) {
      setSelectedConversationId(null);
      return;
    }

    // Auto-select first conversation on initial load
    if (!selectedConversationId) {
      const firstConversation = conversations.at(0);

      if (firstConversation) {
        setSelectedConversationId(firstConversation.id);
      }

      return;
    }

    // Clear invalid selection if deleted
    const exists = conversations.some(
      (conversation) => conversation.id === selectedConversationId
    );

    if (!exists) {
      const firstConversation = conversations.at(0);

      if (firstConversation) {
        setSelectedConversationId(firstConversation.id);
      }
    }
  }, [conversations, selectedConversationId]);

  /* ==========================================================================
       Create Conversation
    ========================================================================== */

  const { mutate: createConversation, isPending: isCreatingConversation } =
    useMutation({
      mutationFn: () => assistantApi.createConversation(null),

      onSuccess: async (conversation) => {
        toast.success("Conversation created.");

        await queryClient.invalidateQueries({
          queryKey: CONVERSATIONS_QUERY_KEY,
        });

        setSelectedConversationId(conversation.id);
      },

      onError: (error: unknown) => {
        if (error instanceof ApiError) {
          toast.error(error.message);
          return;
        }

        toast.error("Unable to create conversation.");
      },
    });

  /* ==========================================================================
       Rename Conversation
    ========================================================================== */

  const { mutateAsync: renameConversation, isPending: isRenamingConversation } =
    useMutation({
      mutationFn: ({ id, title }: { id: string; title: string }) =>
        assistantApi.renameConversation(id, title),

      onSuccess: async () => {
        toast.success("Conversation renamed.");

        await queryClient.invalidateQueries({
          queryKey: CONVERSATIONS_QUERY_KEY,
        });

        setEditingConversationId(null);
      },

      onError: (error: unknown) => {
        if (error instanceof ApiError) {
          toast.error(error.message);
          return;
        }

        toast.error("Unable to rename conversation.");
      },
    });

  /* ==========================================================================
   Delete Conversation
========================================================================== */

  const { mutate: deleteConversation, isPending: isDeletingConversation } =
    useMutation({
      mutationFn: assistantApi.deleteConversation,

      onSuccess: async (_, deletedConversationId) => {
        toast.success("Conversation deleted.");

        queryClient.setQueryData<ConversationSummary[]>(
          CONVERSATIONS_QUERY_KEY,
          (old = []) =>
            old.filter(
              (conversation) => conversation.id !== deletedConversationId
            )
        );

        await queryClient.invalidateQueries({
          queryKey: CONVERSATIONS_QUERY_KEY,
        });

        if (selectedConversationId === deletedConversationId) {
          setSelectedConversationId(null);
        }
      },

      onError: (error: unknown) => {
        if (error instanceof ApiError) {
          toast.error(error.message);
          return;
        }

        toast.error("Unable to delete conversation.");
      },
    });
  /* ==========================================================================
       Event Handlers
    ========================================================================== */

  const handleCreateConversation = useCallback((): void => {
    createConversation();
  }, [createConversation]);

  const handleRenameConversation = useCallback(
    async (conversationId: string, title: string): Promise<void> => {
      await renameConversation({
        id: conversationId,
        title: title.trim(),
      });
    },
    [renameConversation]
  );

  const handleCancelRename = useCallback((): void => {
    setEditingConversationId(null);
  }, []);

  const handleDeleteConversation = useCallback(
    (conversationId: string): void => {
      setOpenConversationMenu(null);

      if (selectedConversationId === conversationId) {
        const remainingConversation = conversations.find(
          (conversation) => conversation.id !== conversationId
        );

        setSelectedConversationId(
          remainingConversation ? remainingConversation.id : null
        );
      }

      deleteConversation(conversationId);
    },
    [conversations, deleteConversation, selectedConversationId]
  );

  /* ==========================================================================
       Loading State
    ========================================================================== */

  if (isLoading && conversations.length === 0) {
    return (
      <div className="space-y-6">
        <header className="space-y-2">
          <h1 className="text-2xl font-bold">AI Assistant</h1>

          <div className="h-4 w-80 animate-pulse rounded bg-muted" />
        </header>

        <section className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <SkeletonSidebar />
          </div>

          <div
            className="
                h-[600px]
                rounded-xl
                border
                border-border/40
                bg-card
              "
          />
        </section>
      </div>
    );
  }

  /* ==========================================================================
       Error State
    ========================================================================== */

  if (error) {
    return (
      <div
        className="
            mx-auto
            flex
            max-w-xl
            flex-col
            items-center
            justify-center
            rounded-xl
            border
            border-border/40
            bg-card
            p-8
            text-center
          "
      >
        <div
          className="
              mb-4
              flex
              h-12
              w-12
              items-center
              justify-center
              rounded-full
              bg-destructive/10
              text-destructive
            "
        >
          <AlertCircle className="h-6 w-6" />
        </div>

        <h2 className="mb-2 text-lg font-bold">Unable to load conversations</h2>

        <p className="mb-6 text-sm text-muted-foreground">
          We couldn't retrieve your assistant sessions.
        </p>

        <button
          type="button"
          onClick={() =>
            queryClient.invalidateQueries({
              queryKey: CONVERSATIONS_QUERY_KEY,
            })
          }
          className="
              inline-flex
              items-center
              gap-2
              rounded-lg
              bg-primary
              px-4
              py-2
              text-xs
              font-semibold
              text-primary-foreground
              transition-colors
              hover:bg-primary/90
            "
        >
          <RefreshCw className="h-4 w-4" />
          Retry
        </button>
      </div>
    );
  }

  /* ==========================================================================
       Main Layout
    ========================================================================== */

  return (
    <div className="space-y-6">
      {/* ======================================================
            Page Header
        ====================================================== */}

      <header className="space-y-2">
        <h1 className="text-2xl font-bold tracking-tight">AI Assistant</h1>

        <p className="text-sm text-muted-foreground">
          Chat with your business knowledge base, compare documents, and inspect
          grounded RAG citations.
        </p>
      </header>

      {/* ======================================================
            Split Layout
        ====================================================== */}

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* ======================================================
              Conversation Sidebar
          ====================================================== */}

        <aside
          className="
              flex
              h-[600px]
              flex-col
              rounded-xl
              border
              border-border/40
              bg-card
              p-4
              lg:col-span-4
            "
          aria-label="Conversation history"
        >
          {/* Sidebar Header */}

          <div className="mb-4 flex items-center justify-between border-b border-border/20 pb-3">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Conversations
              </p>

              <p className="mt-1 text-xs text-muted-foreground">
                {conversations.length} total
              </p>
            </div>

            <button
              type="button"
              onClick={handleCreateConversation}
              disabled={isCreatingConversation}
              className="
                  flex
                  h-9
                  w-9
                  items-center
                  justify-center
                  rounded-lg
                  border
                  border-border
                  bg-background
                  transition-colors
                  hover:bg-muted
                  disabled:cursor-not-allowed
                  disabled:opacity-50
                "
              aria-label="Create conversation"
            >
              {isCreatingConversation ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
            </button>
          </div>

          {/* Conversation List */}

          <div className="flex-1 overflow-y-auto space-y-2 pr-1">
            {conversations.length === 0 ? (
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

                <p className="text-sm font-medium">No conversations yet.</p>

                <p className="mt-1 text-xs">
                  Create your first conversation to begin chatting.
                </p>
              </div>
            ) : (
              conversations.map((conversation) => {
                const isSelected = selectedConversationId === conversation.id;

                const isEditing = editingConversationId === conversation.id;

                return (
                  <div
                    key={conversation.id}
                    className={`
                          group
                          rounded-lg
                          border
                          p-2.5
                          transition-colors
                          ${
                            isSelected
                              ? "border-primary/20 bg-primary/5"
                              : "border-transparent hover:bg-muted/40"
                          }
                        `}
                  >
                    {isEditing ? (
                      <RenameForm
                        conversation={conversation}
                        onCancel={handleCancelRename}
                        onSave={(title) =>
                          handleRenameConversation(conversation.id, title)
                        }
                        isPending={isRenamingConversation}
                      />
                    ) : (
                      <div
                        role="button"
                        tabIndex={0}
                        aria-selected={isSelected}
                        onClick={() =>
                          setSelectedConversationId(conversation.id)
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();

                            setSelectedConversationId(conversation.id);
                          }
                        }}
                        className="
                              flex
                              cursor-pointer
                              items-center
                              justify-between
                              gap-3
                            "
                      >
                        <div className="flex min-w-0 flex-1 items-center gap-3">
                          <MessageSquare
                            className={`
                                  h-4
                                  w-4
                                  flex-shrink-0
                                  ${
                                    isSelected
                                      ? "text-primary"
                                      : "text-muted-foreground"
                                  }
                                `}
                          />

                          <span
                            className={`
                                  truncate
                                  text-sm
                                  ${
                                    isSelected
                                      ? "font-semibold text-foreground"
                                      : "text-muted-foreground"
                                  }
                                `}
                            title={conversation.title}
                          >
                            {conversation.title}
                          </span>
                        </div>

                        <div className="relative">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();

                              setOpenConversationMenu((previous) =>
                                previous === conversation.id
                                  ? null
                                  : conversation.id
                              );
                            }}
                            className="
                              rounded
                              p-1
                              text-muted-foreground
                              transition-colors
                              hover:bg-muted
                              hover:text-foreground
                              opacity-0
                              group-hover:opacity-100
                            "
                          >
                            <MoreVertical className="h-4 w-4" />
                          </button>

                          {openConversationMenu === conversation.id && (
                            <div
                              className="
                                absolute
                                right-0
                                top-8
                                z-50
                                w-40
                                rounded-lg
                                border
                                border-border
                                bg-card
                                shadow-lg
                              "
                            >
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();

                                  setEditingConversationId(conversation.id);

                                  setOpenConversationMenu(null);
                                }}
                                className="
                                  flex
                                  w-full
                                  items-center
                                  gap-2
                                  px-3
                                  py-2
                                  text-sm
                                  hover:bg-muted
                                "
                              >
                                <Edit2 className="h-4 w-4" />
                                Rename
                              </button>

                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();

                                  setConversationToDelete(conversation);

                                  setOpenConversationMenu(null);
                                }}
                                className="
                                  flex
                                  w-full
                                  items-center
                                  gap-2
                                  px-3
                                  py-2
                                  text-sm
                                  text-destructive
                                  hover:bg-destructive/10
                                "
                              >
                                <Trash2 className="h-4 w-4" />
                                Delete
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </aside>

        {/* ======================================================
              Chat Workspace
          ====================================================== */}

        <div className="flex lg:col-span-8">
          <div className="relative w-full">
            {isLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-background/70 backdrop-blur-sm">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            )}

            <ChatPanel
              mode="global"
              {...(selectedConversationId
                ? { conversationId: selectedConversationId }
                : {})}
              className="w-full shadow-sm"
            />
          </div>
        </div>
      </section>

      <ConfirmDialog
        open={conversationToDelete !== null}
        title="Delete Conversation"
        message={
          conversationToDelete
            ? `Delete "${conversationToDelete.title}"? This action cannot be undone.`
            : ""
        }
        confirmText="Delete"
        cancelText="Cancel"
        loading={false}
        onCancel={() => setConversationToDelete(null)}
        onConfirm={() => {
          if (!conversationToDelete) return;

          handleDeleteConversation(conversationToDelete.id);

          setConversationToDelete(null);
        }}
      />
    </div>
  );
};

Assistant.displayName = "Assistant";

export default React.memo(Assistant);
