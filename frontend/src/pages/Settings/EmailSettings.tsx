import React, { useEffect, useState } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getEmailSettings,
  saveEmailSettings,
  testEmailSettings,
} from "@/services/api/emailSettings";

import TestEmailDialog from "@/components/settings/TestEmailDialog";

import { ApiError } from "@/services/api/client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  emailSettingsSchema,
  type EmailSettingsFormData,
} from "@/schemas/emailSettings";

export const EmailSettings: React.FC = () => {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<EmailSettingsFormData>({
    resolver: zodResolver(emailSettingsSchema),
    defaultValues: {
      sender_name: "",
      smtp_host: "",
      smtp_port: 587,
      smtp_username: "",
      smtp_password: "",
      encryption: "TLS",
    },
  });

  const [isTestDialogOpen, setIsTestDialogOpen] = useState(false);

  const [testRecipient, setTestRecipient] = useState("");

  const { data: emailSettings, isLoading: isLoadingSettings } = useQuery({
    queryKey: ["email-settings"],

    queryFn: getEmailSettings,
  });

  useEffect(() => {
    if (!emailSettings) {
      return;
    }

    reset({
      sender_name: emailSettings.sender_name,
      smtp_host: emailSettings.smtp_host,
      smtp_port: emailSettings.smtp_port,
      smtp_username: emailSettings.smtp_username,

      // Password is intentionally left blank.
      smtp_password: "",

      encryption: emailSettings.encryption,
    });
  }, [emailSettings, reset]);

  const { mutateAsync: saveSettings, isPending: isSaving } = useMutation({
    mutationFn: saveEmailSettings,

    onSuccess: () => {
      toast.success("Email settings saved successfully.");
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message);
        return;
      }

      toast.error("Failed to save email settings.");
    },
  });

  const { mutateAsync: sendTestEmail, isPending: isSendingTest } = useMutation({
    mutationFn: testEmailSettings,

    onSuccess: () => {
      toast.success("Test email sent successfully.");
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message);
        return;
      }

      toast.error("Failed to send test email.");
    },
  });

  const onSubmit = async (data: EmailSettingsFormData): Promise<void> => {
    await saveSettings(data);
  };

  const handleSendTestEmail = async (): Promise<void> => {
    await sendTestEmail({
      recipient: testRecipient.trim(),
    });

    setIsTestDialogOpen(false);

    setTestRecipient("");
  };

  if (isLoadingSettings) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="space-y-6 animate-pulse">
          <div className="h-8 w-56 rounded bg-muted" />

          <div className="h-4 w-80 rounded bg-muted" />

          <div className="space-y-4 pt-4">
            <div className="space-y-2">
              <div className="h-3 w-24 rounded bg-muted" />
              <div className="h-10 rounded bg-muted" />
            </div>

            <div className="space-y-2">
              <div className="h-3 w-24 rounded bg-muted" />
              <div className="h-10 rounded bg-muted" />
            </div>

            <div className="grid grid-cols-2 gap-6">
              <div className="space-y-2">
                <div className="h-3 w-24 rounded bg-muted" />
                <div className="h-10 rounded bg-muted" />
              </div>

              <div className="space-y-2">
                <div className="h-3 w-24 rounded bg-muted" />
                <div className="h-10 rounded bg-muted" />
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <h1 className="text-2xl font-bold">Email Settings</h1>

        <p className="mt-2 text-sm text-muted-foreground">
          Configure SMTP settings used by FlowPilot AI to send automation
          emails.
        </p>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-6">
          {/* Sender Name */}
          <div className="space-y-2">
            <label
              htmlFor="sender_name"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              Sender Name
            </label>

            <input
              id="sender_name"
              type="text"
              placeholder="FlowPilot AI"
              {...register("sender_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.sender_name && (
              <p className="text-xs text-destructive">
                {errors.sender_name.message}
              </p>
            )}
          </div>

          {/* SMTP Host */}
          <div className="space-y-2">
            <label
              htmlFor="smtp_host"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              SMTP Host
            </label>

            <input
              id="smtp_host"
              type="text"
              placeholder="smtp.gmail.com"
              {...register("smtp_host")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.smtp_host && (
              <p className="text-xs text-destructive">
                {errors.smtp_host.message}
              </p>
            )}
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {/* SMTP Port */}
            <div className="space-y-2">
              <label
                htmlFor="smtp_port"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                SMTP Port
              </label>

              <input
                id="smtp_port"
                type="number"
                {...register("smtp_port", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.smtp_port && (
                <p className="text-xs text-destructive">
                  {errors.smtp_port.message}
                </p>
              )}
            </div>

            {/* Encryption */}
            <div className="space-y-2">
              <label
                htmlFor="encryption"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Encryption
              </label>

              <select
                id="encryption"
                {...register("encryption")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              >
                <option value="TLS">TLS</option>
                <option value="SSL">SSL</option>
                <option value="NONE">None</option>
              </select>

              {errors.encryption && (
                <p className="text-xs text-destructive">
                  {errors.encryption.message}
                </p>
              )}
            </div>
          </div>

          {/* SMTP Username */}
          <div className="space-y-2">
            <label
              htmlFor="smtp_username"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              SMTP Username
            </label>

            <input
              id="smtp_username"
              type="email"
              placeholder="your-email@gmail.com"
              {...register("smtp_username")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.smtp_username && (
              <p className="text-xs text-destructive">
                {errors.smtp_username.message}
              </p>
            )}
          </div>

          {/* SMTP Password */}
          <div className="space-y-2">
            <label
              htmlFor="smtp_password"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              SMTP Password
            </label>

            <input
              id="smtp_password"
              type="password"
              placeholder="Google App Password"
              {...register("smtp_password")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.smtp_password && (
              <p className="text-xs text-destructive">
                {errors.smtp_password.message}
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-2">
            <button
              type="submit"
              disabled={isSaving || isDirty}
              className="rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSaving ? "Saving..." : "Save Settings"}
            </button>

            <button
              type="button"
              onClick={() => setIsTestDialogOpen(true)}
              disabled={isSendingTest}
              className="rounded-lg border border-border bg-card px-5 py-2 text-sm font-semibold transition-colors hover:bg-muted"
            >
              {isSendingTest ? "Sending..." : "Send Test Email"}
            </button>
          </div>
        </form>

        {Object.keys(errors).length > 0 && (
          <p className="mt-4 text-sm text-destructive">Validation is active.</p>
        )}
        <TestEmailDialog
          isOpen={isTestDialogOpen}
          recipient={testRecipient}
          isSending={isSendingTest}
          onRecipientChange={setTestRecipient}
          onCancel={() => {
            setIsTestDialogOpen(false);
            setTestRecipient("");
          }}
          onSend={handleSendTestEmail}
        />
      </div>
    </div>
  );
};

export default EmailSettings;
