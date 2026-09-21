/**
 * ARCH40-S2:email-settings-page. Workspace email: the override, and why.
 *
 * Two things this page must answer, and before ARCH-40 answered neither:
 *
 *   What address does this workspace's mail actually come from?
 *     The resolution panel, from GET .../email-settings/resolution. It shows
 *     the resolved sender, reply-to and relay, and the trail: every layer the
 *     resolver consulted (workspace override, verified branding domain,
 *     organization settings, platform relay), whether it applied, and why not.
 *
 *   How do I change it for this workspace only?
 *     The override form. It saves half-finished — the state `email_settings`
 *     could not hold — and only an ENABLED override must be complete, which
 *     the database enforces. Leaving the password blank keeps the stored one.
 *
 * "Why did this email come from the wrong address" used to need a database
 * session. It now needs this page.
 */

import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, Mail, Send, ShieldCheck } from "lucide-react";

import {
  getEmailResolution,
  getEmailSettings,
  saveEmailSettings,
  testEmailSettings,
} from "@/services/api/emailSettings";
import { ApiError } from "@/services/api/client";
import { settingsKeys } from "@/services/api/queryKeys";
import TestEmailDialog from "@/components/settings/TestEmailDialog";
import { canManageWorkspaceSettings } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import type {
  EmailEncryption,
  EmailResolution,
  WorkspaceEmailOverrideUpdate,
} from "@/types/emailSettings";

const LAYER_LABELS: Readonly<Record<string, string>> = {
  WORKSPACE_OVERRIDE: "This workspace's override",
  BRANDING_SENDER: "Verified branding domain",
  ORGANIZATION: "Organization email settings",
  PLATFORM: "FlowPilot platform relay",
};

interface FormValues {
  is_enabled: boolean;
  smtp_host: string;
  smtp_port: string;
  smtp_username: string;
  smtp_password: string;
  sender_name: string;
  encryption: EmailEncryption;
  from_address: string;
  reply_to_address: string;
}

const EMPTY: FormValues = {
  is_enabled: false,
  smtp_host: "",
  smtp_port: "587",
  smtp_username: "",
  smtp_password: "",
  sender_name: "",
  encryption: "TLS",
  from_address: "",
  reply_to_address: "",
};

const blankToNull = (value: string): string | null => (value.trim() === "" ? null : value.trim());

const ResolutionPanel: React.FC<{ readonly resolution: EmailResolution | undefined; readonly loading: boolean; readonly failed: boolean }> = ({
  resolution,
  loading,
  failed,
}) => (
  <section aria-labelledby="email-resolution-title" className="rounded-xl border border-border bg-card">
    <header className="border-b border-border/60 px-5 py-4">
      <h2 id="email-resolution-title" className="text-sm font-extrabold uppercase tracking-wider">What this workspace sends as</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Resolved now, most specific layer first. Password resets and verification always use the platform relay.
      </p>
    </header>
    {loading && (
      <p className="flex items-center gap-2 p-5 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" /> Resolving…
      </p>
    )}
    {failed && !loading && (
      <p className="p-5 text-sm text-destructive">The sender could not be resolved. The platform relay may not be configured.</p>
    )}
    {resolution && (
      <div className="space-y-4 p-5">
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">From</dt>
            <dd className="mt-0.5 break-all font-mono text-sm">
              {resolution.sender_name} &lt;{resolution.from_address}&gt;
            </dd>
          </div>
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Reply-to</dt>
            <dd className="mt-0.5 break-all font-mono text-sm">{resolution.reply_to ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Relay</dt>
            <dd className="mt-0.5 break-all font-mono text-sm">{resolution.smtp_host}</dd>
            <dd className="text-[11px] text-muted-foreground">{LAYER_LABELS[resolution.transport_layer] ?? resolution.transport_layer}</dd>
          </div>
        </dl>
        {resolution.degraded_reason && (
          <p className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300" role="status">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {resolution.degraded_reason}
          </p>
        )}
        <ol className="space-y-2" aria-label="Resolution trail">
          {resolution.trail.map((step) => (
            <li
              key={step.layer}
              className={`flex items-start gap-3 rounded-lg border px-3 py-2 ${
                step.applied ? "border-emerald-500/40 bg-emerald-500/5" : "border-border/60"
              }`}
            >
              {step.applied ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="Applied" />
              ) : (
                <CircleDashed className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-label="Not applied" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-semibold">
                  {LAYER_LABELS[step.layer] ?? step.layer}
                  {step.detail && <span className="ml-2 font-mono text-xs text-muted-foreground">{step.detail}</span>}
                </p>
                {!step.applied && step.reason && <p className="text-xs text-muted-foreground">{step.reason}</p>}
              </div>
            </li>
          ))}
        </ol>
      </div>
    )}
  </section>
);

export const EmailSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const { workspace, workspaceRole } = useResolvedTenant();
  const workspaceId = workspace.id;
  const canManage = canManageWorkspaceSettings(workspaceRole);

  const [testOpen, setTestOpen] = useState(false);
  const [recipient, setRecipient] = useState("");

  const overrideQuery = useQuery({
    queryKey: settingsKeys.email(workspaceId),
    queryFn: () => getEmailSettings(workspaceId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
  const resolutionQuery = useQuery({
    queryKey: settingsKeys.emailResolution(workspaceId),
    queryFn: () => getEmailResolution(workspaceId),
    retry: false,
  });

  const stored = overrideQuery.data;
  const { register, handleSubmit, reset, watch, formState: { isDirty } } = useForm<FormValues>({ defaultValues: EMPTY });

  useEffect(() => {
    if (!stored) {
      return;
    }
    reset({
      is_enabled: stored.is_enabled,
      smtp_host: stored.smtp_host ?? "",
      smtp_port: stored.smtp_port !== null ? String(stored.smtp_port) : "",
      smtp_username: stored.smtp_username ?? "",
      smtp_password: "",
      sender_name: stored.sender_name ?? "",
      encryption: stored.encryption,
      from_address: stored.from_address ?? "",
      reply_to_address: stored.reply_to_address ?? "",
    });
  }, [stored, reset]);

  const enabled = watch("is_enabled");

  const save = useMutation({
    mutationFn: (values: FormValues) => {
      const port = values.smtp_port.trim() === "" ? null : Number(values.smtp_port);
      const payload: WorkspaceEmailOverrideUpdate = {
        is_enabled: values.is_enabled,
        smtp_host: blankToNull(values.smtp_host),
        smtp_port: port !== null && Number.isFinite(port) ? port : null,
        smtp_username: blankToNull(values.smtp_username),
        sender_name: blankToNull(values.sender_name),
        encryption: values.encryption,
        from_address: blankToNull(values.from_address),
        reply_to_address: blankToNull(values.reply_to_address),
        // Blank keeps the stored password; the server never returns it.
        smtp_password: blankToNull(values.smtp_password),
      };
      return saveEmailSettings(workspaceId, payload);
    },
    onSuccess: async () => {
      toast.success("Email override saved.");
      await queryClient.invalidateQueries({ queryKey: settingsKeys.all(workspaceId) });
    },
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : "The email override could not be saved.");
    },
  });

  const sendTest = useMutation({
    mutationFn: () => testEmailSettings(workspaceId, { recipient: recipient.trim() }),
    onSuccess: (result) => {
      setTestOpen(false);
      if (result.success) {
        toast.success(`Sent through ${LAYER_LABELS[result.transport_layer ?? ""] ?? "the resolved relay"} as ${result.from_address ?? "the resolved sender"}.`);
      } else {
        toast.error(result.message);
      }
    },
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : "The test message could not be sent.");
    },
  });

  const disabled = !canManage || save.isPending;
  const input = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-60";
  const label = "mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground";

  return (
    <div className="space-y-6">
      <ResolutionPanel
        resolution={resolutionQuery.data}
        loading={resolutionQuery.isLoading}
        failed={resolutionQuery.isError}
      />

      <form onSubmit={handleSubmit((values) => save.mutate(values))} className="rounded-xl border border-border bg-card" aria-labelledby="email-override-title">
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/60 px-5 py-4">
          <div>
            <h2 id="email-override-title" className="flex items-center gap-2 text-sm font-extrabold uppercase tracking-wider">
              <Mail className="h-4 w-4 text-primary" aria-hidden="true" /> Workspace override
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Send this workspace&apos;s notifications and automation mail through its own relay, instead of the organization&apos;s.
            </p>
          </div>
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm font-semibold">
            <input type="checkbox" className="h-4 w-4 accent-primary" disabled={disabled} {...register("is_enabled")} />
            {enabled ? "Override on" : "Override off"}
          </label>
        </header>

        <div className="grid grid-cols-1 gap-4 p-5 md:grid-cols-2">
          <div>
            <label htmlFor="email-host" className={label}>SMTP host</label>
            <input id="email-host" disabled={disabled} className={input} placeholder="smtp.example.com" {...register("smtp_host")} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="email-port" className={label}>Port</label>
              <input id="email-port" inputMode="numeric" disabled={disabled} className={input} {...register("smtp_port")} />
            </div>
            <div>
              <label htmlFor="email-encryption" className={label}>Encryption</label>
              <select id="email-encryption" disabled={disabled} className={input} {...register("encryption")}>
                <option value="TLS">TLS (STARTTLS)</option>
                <option value="SSL">SSL</option>
                <option value="NONE">None</option>
              </select>
            </div>
          </div>
          <div>
            <label htmlFor="email-username" className={label}>Username</label>
            <input id="email-username" autoComplete="off" disabled={disabled} className={input} {...register("smtp_username")} />
          </div>
          <div>
            <label htmlFor="email-password" className={label}>Password</label>
            <input
              id="email-password"
              type="password"
              autoComplete="new-password"
              disabled={disabled}
              className={input}
              placeholder={stored?.has_password ? "Stored — leave blank to keep it" : "Not set"}
              {...register("smtp_password")}
            />
            {stored?.has_password && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
                <ShieldCheck className="h-3 w-3" aria-hidden="true" /> A password is stored. It is never shown.
              </p>
            )}
          </div>
          <div>
            <label htmlFor="email-sender" className={label}>Sender name</label>
            <input id="email-sender" disabled={disabled} className={input} placeholder="Acme Finance" {...register("sender_name")} />
          </div>
          <div>
            <label htmlFor="email-from" className={label}>From address (optional)</label>
            <input id="email-from" type="email" disabled={disabled} className={input} placeholder="Defaults to the username" {...register("from_address")} />
          </div>
          <div className="md:col-span-2">
            <label htmlFor="email-reply-to" className={label}>Reply-to (optional)</label>
            <input id="email-reply-to" type="email" disabled={disabled} className={input} {...register("reply_to_address")} />
          </div>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 px-5 py-4">
          <p className="text-[11px] text-muted-foreground">
            {canManage
              ? "An override can be saved half-finished while it is off. Turning it on needs host, port, username, password and sender name."
              : "You can view these; a workspace admin can change them."}
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!canManage}
              onClick={() => setTestOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm font-semibold hover:bg-muted disabled:opacity-50"
            >
              <Send className="h-4 w-4" /> Send test
            </button>
            <button
              type="submit"
              disabled={disabled || !isDirty}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save override
            </button>
          </div>
        </footer>
      </form>

      <TestEmailDialog
        isOpen={testOpen}
        recipient={recipient}
        onRecipientChange={setRecipient}
        onCancel={() => setTestOpen(false)}
        onSend={() => sendTest.mutate()}
        isSending={sendTest.isPending}
      />
    </div>
  );
};

export default EmailSettings;
