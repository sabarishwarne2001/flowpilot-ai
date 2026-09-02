import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Monitor, ShieldAlert, Smartphone } from "lucide-react";

import {
  listSessionsRequest,
  logoutAllRequest,
  revokeSessionRequest,
} from "@/services/api/auth";
import { sessionKeys } from "@/services/api/queryKeys";
import { useAuthStore } from "@/store/useAuthStore";
import type { SessionResponse } from "@/types/auth";

function currentSessionId(token: string | null): string | null {
  if (!token) {return null;}
  try {
    const payload = token.split(".")[1];
    if (!payload) {return null;}
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const decoded = JSON.parse(
      atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=")),
    ) as { sid?: unknown };
    return typeof decoded.sid === "string" ? decoded.sid : null;
  } catch {
    return null;
  }
}

function describeDevice(userAgent: string | null): { label: string; mobile: boolean } {
  if (!userAgent) {return { label: "Unknown device", mobile: false };}

  const ua = userAgent.toLowerCase();
  const mobile = /iphone|ipad|android|mobile/.test(ua);

  const browser = ua.includes("edg/")
    ? "Edge"
    : ua.includes("chrome") && !ua.includes("chromium")
      ? "Chrome"
      : ua.includes("firefox")
        ? "Firefox"
        : ua.includes("safari")
          ? "Safari"
          : null;

  const platform = ua.includes("iphone")
    ? "iPhone"
    : ua.includes("ipad")
      ? "iPad"
      : ua.includes("android")
        ? "Android"
        : ua.includes("mac os")
          ? "macOS"
          : ua.includes("windows")
            ? "Windows"
            : ua.includes("linux")
              ? "Linux"
              : null;

  if (browser && platform) {return { label: `${browser} on ${platform}`, mobile };}
  if (platform) {return { label: platform, mobile };}
  if (browser) {return { label: browser, mobile };}
  return { label: "Unknown device", mobile };
}

function formatWhen(value: string | null): string {
  if (!value) {return "never";}
  const then = new Date(value).getTime();
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) {return "just now";}
  if (minutes < 60) {return `${minutes}m ago`;}
  const hours = Math.round(minutes / 60);
  if (hours < 24) {return `${hours}h ago`;}
  return new Date(value).toLocaleDateString();
}

export const SessionManagement: React.FC = () => {
  const queryClient = useQueryClient();
  const token = useAuthStore((state) => state.token);
  const clearAuth = useAuthStore((state) => state.clearAuth);

  const [confirmingAll, setConfirmingAll] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const activeSessionId = useMemo(() => currentSessionId(token), [token]);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: sessionKeys.list(),
    queryFn: listSessionsRequest,
    staleTime: 15_000,
  });

  const revoke = useMutation({
    mutationFn: (sessionId: string) => revokeSessionRequest(sessionId),
    onSuccess: (_result, sessionId) => {
      setActionError(null);
      if (sessionId === activeSessionId) {
        clearAuth();
        window.location.assign("/login");
        return;
      }
      void queryClient.invalidateQueries({ queryKey: sessionKeys.all });
    },
    onError: () =>
      setActionError("That session couldn't be ended. Please try again."),
  });

  const revokeAll = useMutation({
    mutationFn: logoutAllRequest,
    onSuccess: () => {
      clearAuth();
      window.location.assign("/login");
    },
    onError: () => {
      setConfirmingAll(false);
      setActionError("Sessions couldn't be ended. Please try again.");
    },
  });

  const sessions: SessionResponse[] = data ?? [];

  const ordered = useMemo(() => {
    return [...sessions].sort((a, b) => {
      if (a.id === activeSessionId) {return -1;}
      if (b.id === activeSessionId) {return 1;}
      const at = new Date(a.last_used_at ?? a.created_at).getTime();
      const bt = new Date(b.last_used_at ?? b.created_at).getTime();
      return bt - at;
    });
  }, [sessions, activeSessionId]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground p-6">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading sessions…
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-6">
        <p role="alert" className="text-sm text-destructive">
          Your active sessions couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
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
        <h2 className="text-lg font-bold tracking-tight text-foreground">Active sessions</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Every device currently signed in to your account. If you don&apos;t
          recognise one, end it and change your password.
        </p>
      </div>

      {actionError && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
          {actionError}
        </p>
      )}

      {ordered.length === 0 ? (
        <p className="rounded-lg border border-border bg-card p-6 text-center text-sm text-muted-foreground">
          No active sessions found.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border bg-card">
          {ordered.map((session) => {
            const isCurrent = session.id === activeSessionId;
            const device = describeDevice(session.user_agent);
            const Icon = device.mobile ? Smartphone : Monitor;
            const pending = revoke.isPending && revoke.variables === session.id;

            return (
              <li
                key={session.id}
                className="flex flex-wrap items-center gap-3 p-4"
              >
                <Icon className="h-5 w-5 flex-shrink-0 text-muted-foreground" />

                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-foreground">
                    {device.label}
                    {isCurrent && (
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
                        This device
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {session.ip_address ?? "IP unknown"} · last used{" "}
                    {formatWhen(session.last_used_at)} · signed in{" "}
                    {new Date(session.created_at).toLocaleDateString()}
                  </p>
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setActionError(null);
                    revoke.mutate(session.id);
                  }}
                  disabled={pending}
                  aria-label={
                    isCurrent
                      ? "Sign out of this device"
                      : `End session on ${device.label}`
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted disabled:opacity-60"
                >
                  {pending && <Loader2 className="h-3 w-3 animate-spin" />}
                  {isCurrent ? "Sign out" : "End session"}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="rounded-xl border border-border bg-card p-4">
        <p className="text-sm font-semibold text-foreground">Sign out everywhere</p>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Ends every session, including this one. You&apos;ll need to sign in
          again. Do this if you think someone else has access to your account.
        </p>

        {confirmingAll ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => revokeAll.mutate()}
              disabled={revokeAll.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg bg-destructive px-3 py-1.5 text-sm font-semibold text-destructive-foreground disabled:opacity-60"
            >
              {revokeAll.isPending && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              Yes, sign out everywhere
            </button>
            <button
              type="button"
              onClick={() => setConfirmingAll(false)}
              disabled={revokeAll.isPending}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-60"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => {
              setActionError(null);
              setConfirmingAll(true);
            }}
            className="mt-3 rounded-lg border border-destructive/40 px-3 py-1.5 text-sm font-semibold text-destructive hover:bg-destructive/5"
          >
            Sign out everywhere
          </button>
        )}
      </div>
    </div>
  );
};

export default SessionManagement;
