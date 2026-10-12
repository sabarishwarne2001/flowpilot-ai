/**
 * Campaign session 3 (N-062). Which notifications are also emailed to me.
 *
 * Every notification appears in the app whatever is chosen here. Security and
 * Billing are always emailed (the server refuses to turn them off), so they show
 * as "Always on" with no switch rather than a switch that does nothing. The
 * optional categories are on until the person turns them off, and the choice is
 * theirs alone, for every organization they belong to.
 */

import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Lock } from "lucide-react";

import { getNotificationPreferences, setNotificationPreference } from "@/services/api/profile";
import { profileKeys } from "@/services/api/queryKeys";
import type { NotificationCategoryKey, NotificationPreferences as Preferences } from "@/types/profile";

const NotificationPreferences: React.FC = () => {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: profileKeys.notificationPreferences(),
    queryFn: getNotificationPreferences,
  });
  const mutation = useMutation({
    mutationFn: ({ key, email }: { key: NotificationCategoryKey; email: boolean }) =>
      setNotificationPreference(key, email),
    onSuccess: (data: Preferences) => {
      queryClient.setQueryData(profileKeys.notificationPreferences(), data);
    },
  });

  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading your email choices…
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div className="p-6">
        <p role="alert" className="text-sm text-destructive">Your email choices could not be loaded.</p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="mt-2 rounded-lg border border-border px-3 py-1.5 text-sm font-semibold hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold tracking-tight text-foreground">Notifications</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything appears in the app. Choose what is also emailed to you, for every organization you belong to.
        </p>
      </div>

      {mutation.isError && (
        <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          That change was not saved. Try again.
        </p>
      )}

      <ul className="divide-y divide-border fp-card" aria-label="Email notifications">
        {query.data.categories.map((category) => {
          const busy = mutation.isPending && mutation.variables?.key === category.key;
          return (
            <li key={category.key} className="flex items-start gap-4 p-4">
              <div className="min-w-0 flex-1">
                <p id={`notification-${category.key}`} className="text-sm font-semibold text-foreground">
                  {category.label}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">{category.description}</p>
              </div>
              {category.locked ? (
                <span
                  className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs font-semibold text-muted-foreground"
                  title="About your account's security or billing, so always emailed"
                >
                  <Lock className="h-3 w-3" aria-hidden="true" /> Always emailed
                </span>
              ) : (
                <button
                  type="button"
                  role="switch"
                  aria-checked={category.email}
                  aria-labelledby={`notification-${category.key}`}
                  disabled={busy}
                  onClick={() => mutation.mutate({ key: category.key, email: !category.email })}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary/20 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${
                    category.email ? "bg-emerald-500" : "bg-muted-foreground/30"
                  }`}
                >
                  <span className="sr-only">Email me about {category.label.toLowerCase()}</span>
                  <span
                    aria-hidden="true"
                    className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                      category.email ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default NotificationPreferences;
