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
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { assistantKeys } from "@/services/api/queryKeys";

import { ChatPanel } from "@/components/assistant/ChatPanel";
import { SkeletonSidebar } from "@/components/common/skeletons/SkeletonSidebar";
import { ApiError } from "@/services/api/client";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import type { ConversationSummary } from "@/types/assistant";

const renameFormSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "Conversation title cannot be empty.")
    .max(150, "Conversation title is too long."),
});

type RenameFormInput = z.infer<typeof renameFormSchema>;

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
    <form onSubmit={handleSubmit(submit)} noValidate className="flex flex-1 flex-col space-y-2">
      <div className="flex items-center gap-2">
        <input
          {...register("title")}
          type="text"
          disabled={isPending}
          className={`
            flex-1 rounded border bg-background px-2 py-1 text-xs font-medium focus:outline-none focus:ring-1 focus:ring-primary/20
            ${errors.title ? "border-destructive" : "border-border"}
          `}
        />
        <div className="flex items-center gap-1">
          <button
            type="submit"
            disabled={isPending}
            className="rounded p-1 text-emerald-600 transition-colors hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-50"
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
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Cancel rename"
            title="Cancel"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {errors.title && (
        <p role="alert" className="pl-1 text-[10px] font-medium text-destructive">
          {errors.title.message}
        </p>
      )}
    </form>
  );
};

export const Assistant: React.FC = () => {
  const queryClient = useQueryClient();
  const workspaceId = useActiveWorkspaceId();

  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [editingConversationId, setEditingConversationId] = useState<string | null>(null);
  const [openConversationMenu, setOpenConversationMenu] = useState<string | null>(null);
  const [conversationToDelete, setConversationToDelete] = useState<ConversationSummary | null>(null);

  useEffect(() => {
    setSelectedConversationId(null);
    setEditingConversationId(null);
    setOpenConversationMenu(null);
  }, [workspaceId]);

  const {
    data: conversations = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: assistantKeys.conversations(workspaceId!),
    queryFn: () => assistantApi.getConversations(workspaceId!),
    enabled: Boolean(workspaceId),
    staleTime: 1000 * 15,
    placeholderData: (previousData) => previousData,
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    if (conversations.length === 0) {
      setSelectedConversationId(null);
      return;
    }
    if (!selectedConversationId) {
      const firstConversation = conversations.at(0);
      if (firstConversation) {
        setSelectedConversationId(firstConversation.id);
      }
      return;
    }
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

  const { mutate: createConversation, isPending: isCreatingConversation } =
    useMutation({
      mutationFn: () => assistantApi.createConversation(workspaceId!, null),
      onSuccess: async (conversation) => {
        toast.success("Conversation created.");
        await queryClient.invalidateQueries({
          queryKey: assistantKeys.conversations(workspaceId!),
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

  const { mutateAsync: renameConversation, isPending: isRenamingConversation } =
    useMutation({
      mutationFn: ({ id, title }: { id: string; title: string }) =>
        assistantApi.renameConversation(workspaceId!, id, title),
      onSuccess: async () => {
        toast.success("Conversation renamed.");
        await queryClient.invalidateQueries({
          queryKey: assistantKeys.conversations(workspaceId!),
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

  const { mutate: deleteConversation } =
    useMutation({
      mutationFn: (conversationId: string) => assistantApi.deleteConversation(workspaceId!, conversationId),
      onSuccess: async (_, deletedConversationId) => {
        toast.success("Conversation deleted.");
        await queryClient.invalidateQueries({
          queryKey: assistantKeys.conversations(workspaceId!),
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
        setSelectedConversationId(remainingConversation ? remainingConversation.id : null);
      }
      deleteConversation(conversationId);
    },
    [conversations, deleteConversation, selectedConversationId]
  );

  if (isLoading && conversations.length === 0) {
    return (
      <div className="space-y-4">
        <header className="space-y-1">
          <h1 className="text-xl font-bold">AI Assistant</h1>
          <div className="h-4 w-72 animate-pulse rounded bg-muted" />
        </header>
        <section className="flex flex-col lg:grid lg:grid-cols-12 gap-4 h-[calc(100vh-13rem)] min-h-[500px]">
          <div className="lg:col-span-4">
            <SkeletonSidebar />
          </div>
          <div className="flex-1 lg:col-span-8 rounded-xl border border-border/40 bg-card" />
        </section>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto flex max-w-xl flex-col items-center justify-center rounded-xl border border-border/40 bg-card p-8 text-center">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertCircle className="h-6 w-6" />
        </div>
        <h2 className="mb-2 text-lg font-bold">Unable to load conversations</h2>
        <p className="mb-6 text-sm text-muted-foreground">We couldn't retrieve your assistant sessions.</p>
        <button
          type="button"
          onClick={() => {
            if (workspaceId) {
              queryClient.invalidateQueries({
                queryKey: assistantKeys.conversations(workspaceId),
              });
            }
          }}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <RefreshCw className="h-4 w-4" />
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col space-y-3 h-full">
      <header className="shrink-0 space-y-0.5">
        <h1 className="text-xl sm:text-2xl font-bold tracking-tight">AI Assistant</h1>
        <p className="text-xs sm:text-sm text-muted-foreground">
          Chat with your business knowledge base, compare documents, and inspect grounded RAG citations.
        </p>
      </header>

      {/* Main Container: Stacks vertically on mobile with tight height, Side-by-side on desktop */}
      <section className="flex flex-col lg:grid lg:grid-cols-12 gap-3 sm:gap-4 flex-1 min-h-0 lg:h-[calc(100vh-13rem)]">
        {/* Conversations Drawer/Sidebar (Auto-height max-h on mobile, full-height on desktop) */}
        <aside className="flex flex-col shrink-0 rounded-xl border border-border/40 bg-card p-3 lg:col-span-4 max-h-40 sm:max-h-48 lg:max-h-none lg:h-full" aria-label="Conversation history">
          <div className="mb-2 flex items-center justify-between border-b border-border/20 pb-2">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Conversations</p>
              <p className="text-[11px] text-muted-foreground">{conversations.length} total</p>
            </div>
            <button
              type="button"
              onClick={handleCreateConversation}
              disabled={isCreatingConversation}
              className="flex h-7 w-7 sm:h-8 sm:w-8 items-center justify-center rounded-lg border border-border bg-background transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Create conversation"
            >
              {isCreatingConversation ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plus className="h-3.5 w-3.5" />
              )}
            </button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-1.5 pr-1 min-h-0">
            {conversations.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground py-3">
                <MessageSquare className="mb-1.5 h-6 w-6 opacity-40" />
                <p className="text-xs font-medium">No conversations yet.</p>
              </div>
            ) : (
              conversations.map((conversation) => {
                const isSelected = selectedConversationId === conversation.id;
                const isEditing = editingConversationId === conversation.id;

                return (
                  <div
                    key={conversation.id}
                    className={`group rounded-lg border p-2 transition-colors ${
                      isSelected ? "border-primary/20 bg-primary/5" : "border-transparent hover:bg-muted/40"
                    }`}
                  >
                    {isEditing ? (
                      <RenameForm
                        conversation={conversation}
                        onCancel={handleCancelRename}
                        onSave={(title) => handleRenameConversation(conversation.id, title)}
                        isPending={isRenamingConversation}
                      />
                    ) : (
                      <div
                        role="button"
                        tabIndex={0}
                        aria-selected={isSelected}
                        onClick={() => setSelectedConversationId(conversation.id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setSelectedConversationId(conversation.id);
                          }
                        }}
                        className="flex cursor-pointer items-center justify-between gap-2"
                      >
                        <div className="flex min-w-0 flex-1 items-center gap-2.5">
                          <MessageSquare className={`h-3.5 w-3.5 flex-shrink-0 ${isSelected ? "text-primary" : "text-muted-foreground"}`} />
                          <span className={`truncate text-xs sm:text-sm ${isSelected ? "font-semibold text-foreground" : "text-muted-foreground"}`} title={conversation.title}>
                            {conversation.title}
                          </span>
                        </div>
                        <div className="relative">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setOpenConversationMenu((previous) => previous === conversation.id ? null : conversation.id);
                            }}
                            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground opacity-70 group-hover:opacity-100"
                          >
                            <MoreVertical className="h-3.5 w-3.5" />
                          </button>
                          {openConversationMenu === conversation.id && (
                            <div className="absolute right-0 top-6 z-50 w-36 rounded-lg border border-border bg-card shadow-lg p-1">
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setEditingConversationId(conversation.id);
                                  setOpenConversationMenu(null);
                                }}
                                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-xs rounded hover:bg-muted"
                              >
                                <Edit2 className="h-3.5 w-3.5" />
                                Rename
                              </button>
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setConversationToDelete(conversation);
                                  setOpenConversationMenu(null);
                                }}
                                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-xs rounded text-destructive hover:bg-destructive/10"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
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

        {/* Chat Area: Fills remaining height on mobile, full-height column on desktop */}
        <div className="flex flex-1 min-h-[480px] lg:min-h-0 lg:h-full lg:col-span-8">
          <div className="relative w-full h-full">
            {isLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-background/70 backdrop-blur-sm">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            )}
            <ChatPanel
              mode="global"
              {...(selectedConversationId ? { conversationId: selectedConversationId } : {})}
              className="w-full h-full shadow-sm"
            />
          </div>
        </div>
      </section>

      <ConfirmDialog
        open={conversationToDelete !== null}
        title="Delete Conversation"
        message={conversationToDelete ? `Delete "${conversationToDelete.title}"? This action cannot be undone.` : ""}
        confirmText="Delete"
        cancelText="Cancel"
        loading={false}
        onCancel={() => setConversationToDelete(null)}
        onConfirm={() => {
          if (!conversationToDelete) {return;}
          handleDeleteConversation(conversationToDelete.id);
          setConversationToDelete(null);
        }}
      />
    </div>
  );
};

Assistant.displayName = "Assistant";
export default React.memo(Assistant);
