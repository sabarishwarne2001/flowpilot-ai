import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Mail, Send, ShieldAlert, XCircle } from "lucide-react";

import {
  getOrganizationEmailSettings,
  testOrganizationEmailSettings,
  updateOrganizationEmailSettings,
} from "@/services/api/organizationEmail";
import { orgEmailKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { canManageMembers } from "@/permissions/organizationPermissions";
import type {
  EmailEncryption,
  OrganizationEmailSettingsUpdate,
} from "@/types/organizationEmail";
import type { OrganizationRole } from "@/types/tenancy";

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return fallback;
}

export const OrganizationEmailSettings: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();
  const queryClient = useQueryClient();

  const canManage = canManageMembers(
    String(organizationRole).toUpperCase() as OrganizationRole,
  );

  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [senderName, setSenderName] = useState("");
  const [senderEmail, setSenderEmail] = useState("");
  const [encryption, setEncryption] = useState<EmailEncryption>("TLS");
  const [isEnabled, setIsEnabled] = useState(false);
  const [dirty, setDirty] = useState(false);

  const [recipient, setRecipient] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] =
    useState<{ ok: boolean; message: string } | null>(null);

  const { data: settings, isLoading, isError, refetch } = useQuery({
    queryKey: orgEmailKeys.settings(organizationId),
    queryFn: () => getOrganizationEmailSettings(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!settings || dirty) return;
    setHost(settings.smtp_host ?? "");
    setPort(settings.smtp_port ? String(settings.smtp_port) : "");
    setUsername(settings.smtp_username ?? "");
    setSenderName(settings.sender_name ?? "");
    setSenderEmail(settings.sender_email ?? "");
    setEncryption(settings.encryption ?? "TLS");
    setIsEnabled(settings.is_enabled);
  }, [settings, dirty]);

  const touch = () => {
    setDirty(true);
    setSaved(false);
    setTestResult(null);
  };

  const save = useMutation({
    mutationFn: () => {
      const payload: OrganizationEmailSettingsUpdate = {
        smtp_host: host.trim() ? host.trim() : undefined,
        smtp_port: port ? Number.parseInt(port, 10) : undefined,
        smtp_username: username.trim() ? username.trim() : undefined,
        sender_name: senderName.trim() ? senderName.trim() : undefined,
        sender_email: senderEmail.trim() ? senderEmail.trim() : undefined,
        encryption,
        is_enabled: isEnabled,
      };
      if (password.length > 0) {
        return updateOrganizationEmailSettings(organizationId, {
          ...payload,
          smtp_password: password,
        });
      }
      return updateOrganizationEmailSettings(organizationId, payload);
    },
    onSuccess: (updated) => {
      setError(null);
      setDirty(false);
      setPassword("");
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      queryClient.setQueryData(orgEmailKeys.settings(organizationId), updated);
    },
    onError: (err) =>
      setError(detailOf(err, "Those settings couldn't be saved.")),
  });

  const test = useMutation({
    mutationFn: () =>
      testOrganizationEmailSettings(organizationId, recipient.trim()),
    onSuccess: (result) =>
      setTestResult({ ok: result.success, message: result.message }),
    onError: (err) =>
      setTestResult({
        ok: false,
        message: detailOf(err, "The test message couldn't be sent."),
      }),
  });

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading email settings…
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <p role="alert" className="text-sm text-destructive">
          Email settings couldn&apos;t be loaded.
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

  const readOnly = !canManage;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
        <header>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
            <Mail className="h-5 w-5" />
            Email delivery
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {organization.organization_name} · Configure custom SMTP for outgoing mail.
          </p>
        </header>

        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {error}
          </p>
        )}

        <div className="space-y-4 rounded-lg border border-border bg-card p-4">
          <label className="flex items-start gap-2 text-sm text-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={isEnabled}
              disabled={readOnly}
              onChange={(event) => {
                setIsEnabled(event.target.checked);
                touch();
              }}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Use custom server for outgoing mail</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {isEnabled
                  ? "Mail for this organization goes through the server below."
                  : "Mail goes through FlowPilot's default sender."}
              </span>
            </span>
          </label>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="sm:col-span-2">
              <label htmlFor="smtp-host" className="text-sm font-medium text-foreground">
                Server host
              </label>
              <input
                id="smtp-host"
                value={host}
                disabled={readOnly}
                onChange={(event) => {
                  setHost(event.target.value);
                  touch();
                }}
                maxLength={255}
                placeholder="smtp.example.com"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-sm text-foreground disabled:opacity-60"
              />
            </div>

            <div>
              <label htmlFor="smtp-port" className="text-sm font-medium text-foreground">
                Port
              </label>
              <input
                id="smtp-port"
                type="number"
                min={1}
                max={65535}
                value={port}
                disabled={readOnly}
                onChange={(event) => {
                  setPort(event.target.value);
                  touch();
                }}
                placeholder="587"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              />
            </div>
          </div>

          <div>
            <label htmlFor="smtp-encryption" className="text-sm font-medium text-foreground">
              Encryption
            </label>
            <select
              id="smtp-encryption"
              value={encryption}
              disabled={readOnly}
              onChange={(event) => {
                setEncryption(event.target.value as EmailEncryption);
                touch();
              }}
              className="mt-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
            >
              <option value="TLS">STARTTLS (port 587)</option>
              <option value="SSL">SSL/TLS (port 465)</option>
              <option value="NONE">None</option>
            </select>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="smtp-user" className="text-sm font-medium text-foreground">
                Username
              </label>
              <input
                id="smtp-user"
                value={username}
                disabled={readOnly}
                onChange={(event) => {
                  setUsername(event.target.value);
                  touch();
                }}
                maxLength={255}
                autoComplete="off"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              />
            </div>

            <div>
              <label htmlFor="smtp-pass" className="text-sm font-medium text-foreground">
                Password
              </label>
              <input
                id="smtp-pass"
                type="password"
                value={password}
                disabled={readOnly}
                onChange={(event) => {
                  setPassword(event.target.value);
                  touch();
                }}
                maxLength={255}
                autoComplete="new-password"
                placeholder={
                  settings?.has_password
                    ? "••••••••  (leave blank to keep)"
                    : "Not set"
                }
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                {settings?.has_password
                  ? "A password is stored. Type only to replace."
                  : "No password stored yet."}
              </p>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="sender-name" className="text-sm font-medium text-foreground">
                Sender name
              </label>
              <input
                id="sender-name"
                value={senderName}
                disabled={readOnly}
                onChange={(event) => {
                  setSenderName(event.target.value);
                  touch();
                }}
                maxLength={255}
                placeholder={organization.organization_name}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              />
            </div>

            <div>
              <label htmlFor="sender-email" className="text-sm font-medium text-foreground">
                Sender address
              </label>
              <input
                id="sender-email"
                type="email"
                value={senderEmail}
                disabled={readOnly}
                onChange={(event) => {
                  setSenderEmail(event.target.value);
                  touch();
                }}
                placeholder="no-reply@example.com"
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground disabled:opacity-60"
              />
            </div>
          </div>

          {canManage && (
            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
              <button
                type="button"
                onClick={() => save.mutate()}
                disabled={save.isPending || !dirty}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {save.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                Save settings
              </button>
              {saved && (
                <span role="status" className="text-xs text-muted-foreground">
                  Saved.
                </span>
              )}
            </div>
          )}
        </div>

        {canManage && (
          <div className="space-y-3 rounded-lg border border-border bg-card p-4">
            <div>
              <p className="text-sm font-medium text-foreground">Send a test message</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Uses the saved configuration.
              </p>
            </div>

            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1">
                <label htmlFor="test-to" className="text-sm text-foreground">
                  Send to
                </label>
                <input
                  id="test-to"
                  type="email"
                  value={recipient}
                  onChange={(event) => setRecipient(event.target.value)}
                  placeholder="you@example.com"
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground"
                />
              </div>
              <button
                type="button"
                onClick={() => test.mutate()}
                disabled={test.isPending || recipient.trim().length === 0}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-60"
              >
                {test.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Send className="h-3.5 w-3.5" />
                )}
                Send test
              </button>
            </div>

            {testResult && (
              <p
                role="status"
                className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                  testResult.ok
                    ? "border-border bg-muted/30 text-foreground"
                    : "border-destructive/40 bg-destructive/5 text-destructive"
                }`}
              >
                {testResult.ok ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                ) : (
                  <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                )}
                {testResult.message}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default OrganizationEmailSettings;
