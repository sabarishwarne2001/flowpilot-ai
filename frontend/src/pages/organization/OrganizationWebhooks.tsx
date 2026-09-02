import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  PowerOff,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Trash2,
  Webhook as WebhookIcon,
} from "lucide-react";

import {
  createWebhookEndpoint,
  deleteWebhookEndpoint,
  listWebhookAttempts,
  listWebhookDeliveries,
  listWebhookEndpoints,
  redeliverWebhookDelivery,
  rotateWebhookSecret,
  updateWebhookEndpoint,
} from "@/services/api/webhooks";
import { webhookKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { canManageMembers } from "@/permissions/organizationPermissions";
import { WEBHOOK_EVENT_TYPES } from "@/types/webhook";
import type { WebhookEndpoint } from "@/types/webhook";

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function statusTone(status: string): string {
  const s = status.toUpperCase();
  if (s === "DELIVERED") {return "text-primary font-semibold";}
  if (s === "FAILED" || s === "DEAD") {return "text-destructive font-semibold";}
  return "text-muted-foreground font-semibold";
}

const DeliveryLog: React.FC<{
  organizationId: string;
  endpointId: string;
  canManage: boolean;
}> = ({ organizationId, endpointId, canManage }) => {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [redeliverError, setRedeliverError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: webhookKeys.deliveries(organizationId, endpointId, statusFilter),
    queryFn: () =>
      listWebhookDeliveries(
        organizationId,
        endpointId,
        statusFilter ? { status: statusFilter } : {},
      ),
    staleTime: 10_000,
  });

  const { data: attempts, isLoading: attemptsLoading } = useQuery({
    queryKey: webhookKeys.attempts(organizationId, expanded ?? ""),
    queryFn: () => listWebhookAttempts(organizationId, expanded as string),
    enabled: Boolean(expanded),
  });

  const redeliver = useMutation({
    mutationFn: (deliveryId: string) =>
      redeliverWebhookDelivery(organizationId, deliveryId),
    onSuccess: () => {
      setRedeliverError(null);
      void queryClient.invalidateQueries({
        queryKey: webhookKeys.all(organizationId),
      });
    },
    onError: (error) =>
      setRedeliverError(
        detailOf(error, "That delivery couldn't be requeued. Please try again."),
      ),
  });

  const deliveries = data ?? [];

  return (
    <div className="border-t border-border bg-muted/20 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Recent deliveries
        </span>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="Filter deliveries by status"
          className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
        >
          <option value="">All statuses</option>
          <option value="PENDING">Pending</option>
          <option value="DELIVERED">Delivered</option>
          <option value="FAILED">Failed</option>
          <option value="DEAD">Dead</option>
        </select>
      </div>

      {redeliverError && (
        <p
          role="alert"
          className="mb-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
        >
          {redeliverError}
        </p>
      )}

      {isLoading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading deliveries…
        </p>
      ) : deliveries.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No deliveries yet. Events appear here once something this endpoint
          subscribes to happens.
        </p>
      ) : (
        <ul className="space-y-1">
          {deliveries.map((delivery) => {
            const isOpen = expanded === delivery.id;
            const pending =
              redeliver.isPending && redeliver.variables === delivery.id;

            return (
              <li key={delivery.id} className="rounded-md bg-background border border-border">
                <div className="flex flex-wrap items-center gap-2 p-2">
                  <button
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : delivery.id)}
                    aria-expanded={isOpen}
                    aria-label={`Attempt history for ${delivery.event_type}`}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    {isOpen ? (
                      <ChevronDown className="h-3 w-3 flex-shrink-0" />
                    ) : (
                      <ChevronRight className="h-3 w-3 flex-shrink-0" />
                    )}
                    <span className="truncate font-mono text-xs text-foreground">
                      {delivery.event_type}
                    </span>
                    <span
                      className={`text-xs ${statusTone(delivery.status)}`}
                    >
                      {delivery.status}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {delivery.attempts}{" "}
                      {delivery.attempts === 1 ? "attempt" : "attempts"}
                      {delivery.last_response_status !== null &&
                        ` · HTTP ${delivery.last_response_status}`}
                      {` · ${new Date(delivery.created_at).toLocaleString()}`}
                    </span>
                  </button>

                  {canManage && delivery.status.toUpperCase() !== "DELIVERED" && (
                    <button
                      type="button"
                      onClick={() => {
                        setRedeliverError(null);
                        redeliver.mutate(delivery.id);
                      }}
                      disabled={pending}
                      aria-label={`Redeliver ${delivery.event_type}`}
                      className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs hover:bg-muted disabled:opacity-60 text-foreground"
                    >
                      {pending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <RotateCcw className="h-3 w-3" />
                      )}
                      Redeliver
                    </button>
                  )}
                </div>

                {isOpen && (
                  <div className="border-t border-border px-3 py-2 bg-muted/10">
                    {attemptsLoading ? (
                      <p className="text-xs text-muted-foreground">
                        Loading attempts…
                      </p>
                    ) : !attempts || attempts.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        No attempts recorded yet.
                      </p>
                    ) : (
                      <ol className="space-y-1.5">
                        {attempts.map((attempt) => (
                          <li key={attempt.id} className="text-xs">
                            <span className="font-semibold text-foreground">
                              #{attempt.attempt_number}
                            </span>{" "}
                            <span className={statusTone(attempt.disposition)}>
                              {attempt.disposition}
                            </span>
                            {attempt.response_status !== null &&
                              ` · HTTP ${attempt.response_status}`}
                            {` · ${attempt.duration_ms}ms`}
                            {attempt.resolved_ip && ` · ${attempt.resolved_ip}`}
                            {` · ${new Date(attempt.attempted_at).toLocaleString()}`}
                            {attempt.error && (
                              <p className="mt-0.5 text-destructive">
                                {attempt.error}
                              </p>
                            )}
                            {attempt.response_body_excerpt && (
                              <pre className="mt-0.5 overflow-x-auto rounded bg-muted p-1.5 font-mono text-[11px] text-foreground">
                                {attempt.response_body_excerpt}
                              </pre>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export const OrganizationWebhooks: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();
  const queryClient = useQueryClient();

  const canManage = canManageMembers(
    String(organizationRole).toUpperCase() as never,
  );

  const [creating, setCreating] = useState(false);
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [events, setEvents] = useState<Set<string>>(new Set());

  const [revealed, setRevealed] = useState<
    { secret: string; url: string; validUntil?: string } | null
  >(null);
  const [copied, setCopied] = useState(false);

  const [openLog, setOpenLog] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: webhookKeys.endpoints(organizationId),
    queryFn: () => listWebhookEndpoints(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: webhookKeys.all(organizationId) });

  const resetForm = () => {
    setCreating(false);
    setUrl("");
    setDescription("");
    setEvents(new Set());
  };

  const create = useMutation({
    mutationFn: () =>
      createWebhookEndpoint(organizationId, {
        url: url.trim(),
        event_types: [...events],
        description: description.trim() || null,
      }),
    onSuccess: (result) => {
      setActionError(null);
      setRevealed({ secret: result.secret, url: result.endpoint.url });
      resetForm();
      void invalidate();
    },
    onError: (error) =>
      setActionError(
        detailOf(
          error,
          "That endpoint couldn't be created. Check the URL and that at least one event is selected.",
        ),
      ),
  });

  const update = useMutation({
    mutationFn: ({
      endpointId,
      data: patch,
    }: {
      endpointId: string;
      data: Parameters<typeof updateWebhookEndpoint>[2];
    }) => updateWebhookEndpoint(organizationId, endpointId, patch),
    onSuccess: () => {
      setActionError(null);
      void invalidate();
    },
    onError: (error) =>
      setActionError(detailOf(error, "That endpoint couldn't be updated.")),
  });

  const rotate = useMutation({
    mutationFn: (endpointId: string) =>
      rotateWebhookSecret(organizationId, endpointId),
    onSuccess: (result, endpointId) => {
      const endpoint = (data ?? []).find((e) => e.id === endpointId);
      setActionError(null);
      setRevealed({
        secret: result.secret,
        url: endpoint?.url ?? "this endpoint",
        validUntil: result.previous_secret_valid_until,
      });
      void invalidate();
    },
    onError: (error) =>
      setActionError(detailOf(error, "The signing secret couldn't be rotated.")),
  });

  const remove = useMutation({
    mutationFn: (endpointId: string) =>
      deleteWebhookEndpoint(organizationId, endpointId),
    onSuccess: () => {
      setActionError(null);
      setConfirmingDelete(null);
      void invalidate();
    },
    onError: (error) =>
      setActionError(detailOf(error, "That endpoint couldn't be deleted.")),
  });

  const endpoints = data ?? [];

  const ordered = useMemo(
    () =>
      [...endpoints].sort((a, b) => {
        const aBroken = a.auto_disabled || a.status !== "ACTIVE";
        const bBroken = b.auto_disabled || b.status !== "ACTIVE";
        if (aBroken !== bBroken) {return aBroken ? -1 : 1;}
        return a.url.localeCompare(b.url);
      }),
    [endpoints],
  );

  const toggleEvent = (eventType: string) =>
    setEvents((current) => {
      const next = new Set(current);
      if (next.has(eventType)) {next.delete(eventType);}
      else {next.add(eventType);}
      return next;
    });

  const copySecret = async () => {
    if (!revealed) {return;}
    try {
      await navigator.clipboard.writeText(revealed.secret);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const urlInvalid = url.length > 0 && !url.startsWith("https://");

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading webhooks…
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          Webhook endpoints couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Webhooks</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {organization.organization_name}
            </p>
          </div>

          {canManage && !creating && (
            <button
              type="button"
              onClick={() => {
                setActionError(null);
                setCreating(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              <WebhookIcon className="h-3.5 w-3.5" />
              New endpoint
            </button>
          )}
        </header>

        {actionError && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {actionError}
          </p>
        )}

        {revealed && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Webhook signing secret"
            className="rounded-lg border-2 border-primary bg-card p-4"
          >
            <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <AlertTriangle className="h-4 w-4 text-primary" />
              Copy this signing secret now — it won&apos;t be shown again
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Use it to verify the signature on every request from FlowPilot to{" "}
              <span className="font-mono text-xs text-foreground font-semibold">{revealed.url}</span>.
              {revealed.validUntil && (
                <>
                  {" "}
                  The previous secret keeps working until{" "}
                  <strong>
                    {new Date(revealed.validUntil).toLocaleString()}
                  </strong>
                  , so you can switch over without dropping events.
                </>
              )}
            </p>

            <div className="mt-3 flex items-center gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto rounded bg-muted p-2 font-mono text-xs text-foreground">
                {revealed.secret}
              </code>
              <button
                type="button"
                onClick={() => void copySecret()}
                className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-md border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground hover:bg-muted"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>

            <button
              type="button"
              onClick={() => {
                setRevealed(null);
                setCopied(false);
              }}
              className="mt-3 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              I&apos;ve saved it
            </button>
          </div>
        )}

        {creating && (
          <div className="space-y-4 rounded-lg border border-border bg-card p-4">
            <div>
              <label htmlFor="hook-url" className="text-sm font-medium text-foreground">
                Endpoint URL
              </label>
              <input
                id="hook-url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                maxLength={2000}
                placeholder="https://example.com/hooks/flowpilot"
                aria-invalid={urlInvalid}
                className={`mt-1 w-full rounded-md border bg-background px-3 py-1.5 font-mono text-sm text-foreground focus:outline-none ${
                  urlInvalid ? "border-destructive" : "border-border focus:border-primary"
                }`}
              />
              {urlInvalid && (
                <p className="mt-1 text-xs text-destructive">
                  Must start with https://. Plain HTTP is refused — a signing
                  secret sent over an unencrypted connection is not a secret.
                </p>
              )}
            </div>

            <div>
              <label htmlFor="hook-desc" className="text-sm font-medium text-foreground">
                Description{" "}
                <span className="text-muted-foreground">(optional)</span>
              </label>
              <input
                id="hook-desc"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={500}
                placeholder="Order sync in the billing service"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              />
            </div>

            <fieldset>
              <legend className="text-sm font-medium text-foreground">Events</legend>
              <p className="mb-2 text-xs text-muted-foreground">
                Subscribe only to what this endpoint handles. Security events —
                sessions, API keys, audit entries, ownership — are never
                published to webhooks.
              </p>
              <div className="grid max-h-64 gap-1.5 overflow-y-auto rounded-md border border-border bg-background p-2 sm:grid-cols-2">
                {WEBHOOK_EVENT_TYPES.map((eventType) => (
                  <label
                    key={eventType}
                    className="flex items-center gap-2 text-sm text-foreground cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={events.has(eventType)}
                      onChange={() => toggleEvent(eventType)}
                      className="mt-0.5"
                    />
                    <span className="font-mono text-xs">{eventType}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
              <button
                type="button"
                onClick={() => create.mutate()}
                disabled={
                  create.isPending ||
                  urlInvalid ||
                  url.trim().length === 0 ||
                  events.size === 0
                }
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {create.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                Create endpoint
              </button>
              <button
                type="button"
                onClick={resetForm}
                disabled={create.isPending}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              >
                Cancel
              </button>
              {events.size === 0 && (
                <span className="text-xs text-muted-foreground">
                  Select at least one event.
                </span>
              )}
            </div>
          </div>
        )}

        {ordered.length === 0 && !creating ? (
          <div className="rounded-lg border border-border bg-card p-6 text-center">
            <WebhookIcon className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium text-foreground">No webhook endpoints</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Webhooks push events to your systems as they happen, so you
              don&apos;t have to poll for changes.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {ordered.map((endpoint: WebhookEndpoint) => {
              const disabled =
                endpoint.status !== "ACTIVE" || endpoint.auto_disabled;
              const overlapActive =
                endpoint.rotation_overlap_until !== null &&
                new Date(endpoint.rotation_overlap_until) > new Date();

              return (
                <li
                  key={endpoint.id}
                  className={`overflow-hidden rounded-lg border bg-card ${
                    disabled ? "border-destructive/40" : "border-border"
                  }`}
                >
                  <div className="p-4">
                    <div className="flex flex-wrap items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-sm font-medium text-foreground">
                          {endpoint.url}
                        </p>
                        {endpoint.description && (
                          <p className="mt-0.5 truncate text-sm text-muted-foreground">
                            {endpoint.description}
                          </p>
                        )}
                        <p className="mt-1 text-xs text-muted-foreground">
                          {endpoint.event_types.length}{" "}
                          {endpoint.event_types.length === 1 ? "event" : "events"}
                          {endpoint.last_success_at &&
                            ` · last delivered ${new Date(endpoint.last_success_at).toLocaleDateString()}`}
                          {endpoint.consecutive_failures > 0 &&
                            ` · ${endpoint.consecutive_failures} consecutive failures`}
                        </p>
                      </div>

                      {canManage && (
                        <div className="flex flex-shrink-0 flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              setOpenLog(
                                openLog === endpoint.id ? null : endpoint.id,
                              )
                            }
                            aria-expanded={openLog === endpoint.id}
                            className="rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted"
                          >
                            Deliveries
                          </button>

                          <button
                            type="button"
                            onClick={() => {
                              setActionError(null);
                              rotate.mutate(endpoint.id);
                            }}
                            disabled={rotate.isPending}
                            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted disabled:opacity-60"
                          >
                            <RefreshCw className="h-3 w-3" />
                            Rotate secret
                          </button>

                          {confirmingDelete === endpoint.id ? (
                            <>
                              <button
                                type="button"
                                onClick={() => remove.mutate(endpoint.id)}
                                disabled={remove.isPending}
                                className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground disabled:opacity-60"
                              >
                                {remove.isPending && (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                )}
                                Confirm delete
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmingDelete(null)}
                                className="rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground"
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => {
                                setActionError(null);
                                setConfirmingDelete(endpoint.id);
                              }}
                              aria-label={`Delete ${endpoint.url}`}
                              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted"
                            >
                              <Trash2 className="h-3 w-3" />
                              Delete
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {disabled && (
                      <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 p-3">
                        <p className="flex items-center gap-2 text-sm font-medium text-destructive">
                          <PowerOff className="h-4 w-4" />
                          {endpoint.auto_disabled
                            ? "Disabled automatically after repeated failures"
                            : "Disabled"}
                        </p>
                        <p className="mt-0.5 text-sm text-muted-foreground">
                          {endpoint.disabled_reason ?? "No reason recorded."}
                          {endpoint.disabled_at &&
                            ` (${new Date(endpoint.disabled_at).toLocaleString()})`}
                          {endpoint.auto_disabled &&
                            " Nothing has been delivered here since. Fix the receiver, then re-enable."}
                        </p>
                        {canManage && (
                          <button
                            type="button"
                            onClick={() =>
                              update.mutate({
                                endpointId: endpoint.id,
                                data: { status: "ACTIVE" },
                              })
                            }
                            disabled={update.isPending}
                            className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:opacity-60"
                          >
                            {update.isPending && (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            )}
                            Re-enable
                          </button>
                        )}
                      </div>
                    )}

                    {overlapActive && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        Both the current and previous signing secrets are
                        accepted until{" "}
                        {new Date(endpoint.rotation_overlap_until!).toLocaleString()}.
                      </p>
                    )}

                    <div className="mt-2 flex flex-wrap gap-1">
                      {endpoint.event_types.map((eventType) => (
                        <span
                          key={eventType}
                          className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground"
                        >
                          {eventType}
                        </span>
                      ))}
                    </div>
                  </div>

                  {openLog === endpoint.id && (
                    <DeliveryLog
                      organizationId={organizationId}
                      endpointId={endpoint.id}
                      canManage={canManage}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {!canManage && (
          <p className="border-t border-border pt-4 text-xs text-muted-foreground">
            Managing webhook endpoints requires an organization owner or
            administrator.
          </p>
        )}
      </div>
    </div>
  );
};

export default OrganizationWebhooks;
