import React from "react";

interface SkeletonChatProps {
  readonly messagesCount?: number;
  readonly className?: string;
}

export const SkeletonChat: React.FC<SkeletonChatProps> = ({
  messagesCount = 4,
  className = "",
}) => {
  const count = Math.max(1, Math.min(10, messagesCount));

  const dummyMessages = Array.from({
    length: count,
  });

  return (
    <div
      role="presentation"
      aria-hidden="true"
      aria-label="Loading conversation"
      className={`pointer-events-none flex min-h-[550px] w-full flex-col overflow-hidden rounded-xl border border-border/60 bg-card select-none dark:border-border/40 ${className}`}
    >
      {/* ===========================
          Header
      =========================== */}

      <div className="flex items-center space-x-3 border-b border-border/40 bg-muted/5 p-4">
        <div className="h-9 w-9 flex-shrink-0 animate-pulse rounded-full bg-muted/40 dark:bg-muted/10" />

        <div className="flex-1 space-y-1.5">
          <div className="h-3.5 w-32 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />

          <div className="h-2.5 w-20 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
        </div>
      </div>

      {/* ===========================
          Messages
      =========================== */}

      <div className="flex-1 space-y-6 overflow-y-auto bg-muted/10 p-4 dark:bg-background/20">
        {dummyMessages.map((_, index) => {
          const isUser = index % 2 === 0;

          return (
            <div
              key={`skeleton-chat-${index}`}
              className={`flex w-full items-start space-x-3 ${
                isUser ? "justify-end" : "justify-start"
              }`}
            >
              {!isUser && (
                <div className="h-8 w-8 flex-shrink-0 animate-pulse rounded-full bg-muted/40 dark:bg-muted/10" />
              )}

              <div
                className={`max-w-[70%] space-y-2 rounded-2xl border p-4 ${
                  isUser
                    ? "rounded-tr-none border-primary/10 bg-primary/5 text-right"
                    : "rounded-tl-none border-border/40 bg-card text-left"
                }`}
              >
                <div className="space-y-1.5">
                  <div
                    className={`h-3 animate-pulse rounded bg-muted/60 dark:bg-muted/15 ${
                      isUser ? "ml-auto w-48" : "w-64"
                    }`}
                  />

                  {!isUser && (
                    <>
                      <div className="h-3 w-56 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />

                      <div className="h-3 w-40 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
                    </>
                  )}
                </div>
                {!isUser && (
                  <div className="mt-2 flex items-center space-x-2 border-t border-border/20 pt-2">
                    <div className="h-2.5 w-12 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />

                    <div className="h-5 w-16 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* ===========================
          Input Area
      =========================== */}

      <div className="flex items-center space-x-3 border-t border-border/40 bg-card p-4">
        <div className="h-9 w-9 flex-shrink-0 animate-pulse rounded-lg bg-muted/40 dark:bg-muted/10" />

        <div className="h-9 flex-1 animate-pulse rounded-lg bg-muted/40 dark:bg-muted/10" />

        <div className="h-9 w-16 flex-shrink-0 animate-pulse rounded-lg bg-muted/60 dark:bg-muted/15" />
      </div>
    </div>
  );
};

export default SkeletonChat;
