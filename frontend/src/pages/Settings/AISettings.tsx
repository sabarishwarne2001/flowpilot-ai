import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getAISettings,
  updateAISettings,
  getSupportedModels,
  getAvailableProviders,
  testAIConnection,
} from "@/services/api/aiSettings";

import { ApiError } from "@/services/api/client";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import InfoTooltip from "@/components/common/InfoTooltip";
import { AI_FIELD_HELP } from "@/constants/aiFieldHelp";

import {
  aiSettingsSchema,
  type AISettingsFormData,
} from "@/schemas/aiSettings";

import type { AIConnectionTestResponse } from "@/types/aiConnectionTest";
import { canManageWorkspaceSettings } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";

export const AISettings: React.FC = () => {
  const queryClient = useQueryClient();

  // Effective workspace role, already resolved by TenantGuard.
  const { workspace, workspaceRole } = useResolvedTenant();

  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    getValues,
    formState: { errors, isDirty },
  } = useForm<AISettingsFormData>({
    resolver: zodResolver(aiSettingsSchema),
    defaultValues: {
      provider: "GROQ",
      model: "llama3.3-70b-versatile",
      temperature: 0.7,
      max_output_tokens: 4096,
      top_p: 0.9,
      frequency_penalty: 0,
      presence_penalty: 0,
      input_cost_per_1k_tokens: 0,
      output_cost_per_1k_tokens: 0,
      system_prompt_version: "v1.2.0",
      prompt_version: "v1.0.0",
      enable_token_tracking: true,
      enable_streaming: true,
    },
  });

  const selectedProvider = useWatch({
    control,
    name: "provider",
  });

  const { data: aiSettings, isLoading: isLoadingAISettings } = useQuery({
    queryKey: ["ai-settings", workspace.id],
    queryFn: () => getAISettings(workspace.id),
  });

  const { data: supportedModels, isLoading: isLoadingModels } = useQuery({
    queryKey: ["supported-models", workspace.id],
    queryFn: () => getSupportedModels(workspace.id),
  });

  const {
    data: availableProviders,
    isLoading: isLoadingProviders,
  } = useQuery({
    queryKey: ["available-providers", workspace.id],
    queryFn: () => getAvailableProviders(workspace.id),
  });

  const canManageSettings = canManageWorkspaceSettings(workspaceRole);

  const [connectionResult, setConnectionResult] =
    useState<AIConnectionTestResponse | null>(null);

  useEffect(() => {
    if (!aiSettings) {
      return;
    }

    reset({
      provider: aiSettings.provider,
      model: aiSettings.model,
      temperature: aiSettings.temperature,
      max_output_tokens: aiSettings.max_output_tokens,
      top_p: aiSettings.top_p,
      frequency_penalty: aiSettings.frequency_penalty,
      presence_penalty: aiSettings.presence_penalty,
      input_cost_per_1k_tokens: aiSettings.input_cost_per_1k_tokens,
      output_cost_per_1k_tokens: aiSettings.output_cost_per_1k_tokens,
      system_prompt_version: aiSettings.system_prompt_version,
      prompt_version: aiSettings.prompt_version,
      enable_token_tracking: aiSettings.enable_token_tracking,
      enable_streaming: aiSettings.enable_streaming,
    });
  }, [aiSettings, reset]);

  // Automatically switch model to a valid one if provider changes
  useEffect(() => {
    if (!selectedProvider || !supportedModels) {return;}

    const availableModels =
      supportedModels[selectedProvider as keyof typeof supportedModels] ?? [];
    const currentModel = getValues("model");

    if (availableModels.length > 0 && !availableModels.includes(currentModel)) {
      setValue("model", availableModels[0]!);
    }
  }, [selectedProvider, supportedModels, getValues, setValue]);

  const { mutateAsync: saveAISettings, isPending: isSaving } = useMutation({
    mutationFn: (data: AISettingsFormData) => updateAISettings(workspace.id, data),
    onSuccess: async () => {
      toast.success("AI settings saved successfully.");
      await queryClient.invalidateQueries({ queryKey: ["ai-settings"] });
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message);
        return;
      }
      toast.error("Failed to save AI settings.");
    },
  });

  const { mutateAsync: testConnection, isPending: isTestingConnection } =
    useMutation({
      mutationFn: (data: AISettingsFormData) => testAIConnection(workspace.id, data),
    });

  const onSubmit = async (data: AISettingsFormData): Promise<void> => {
    await saveAISettings(data);
  };

  const handleTestConnection = async () => {
    try {
      const result = await testConnection(getValues());
      setConnectionResult(result);
      toast.success("Connection successful.");
    } catch (error) {
      setConnectionResult(null);
      if (error instanceof ApiError) {
        toast.error(error.message);
        return;
      }
      toast.error("Connection test failed.");
    }
  };

  const renderLabel = (key: keyof typeof AI_FIELD_HELP, label: string) => {
    const help = AI_FIELD_HELP[key]!;

    return (
      <div className="flex items-center">
        <span>{label}</span>
        <InfoTooltip
          title={help.title}
          description={help.description}
          recommended={help.recommended}
        />
      </div>
    );
  };

  if (
    isLoadingAISettings ||
    isLoadingModels ||
    isLoadingProviders
  ) {
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
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <h1 className="text-2xl font-bold">AI Settings</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Configure the default AI provider and model used throughout FlowPilot AI.
        </p>

        <div className="mt-6 rounded-lg border border-blue-900/50 bg-blue-950/20 p-4">
          <h3 className="text-sm font-semibold text-blue-300">Advanced AI Parameters</h3>
          <p className="mt-2 text-sm text-slate-300">
            These settings control how the AI model behaves. The default values are optimized for most business workflows.
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-6">
          <div className="space-y-2">
            <label htmlFor="provider" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              {renderLabel("provider", "Provider")}
            </label>
            <select
              id="provider"
              disabled={!canManageSettings}
              {...register("provider")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
            >
              {availableProviders?.providers.map((provider: string) => (
                <option key={provider} value={provider}>
                  {provider === "GROQ" ? "Groq" : provider === "GEMINI" ? "Google Gemini" : provider}
                </option>
              ))}
            </select>
            {errors.provider && <p className="text-xs text-destructive">{errors.provider.message}</p>}
          </div>

          <div className="space-y-2">
            <label htmlFor="model" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              {renderLabel("model", "Model")}
            </label>
            <select
              id="model"
              disabled={!canManageSettings}
              {...register("model")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
            >
              {(supportedModels?.[selectedProvider as keyof typeof supportedModels] ?? []).map((model: string) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
            {errors.model && <p className="text-xs text-destructive">{errors.model.message}</p>}
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="temperature" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("temperature", "Temperature")}
              </label>
              <input
                id="temperature"
                type="number"
                step="0.1"
                min="0"
                max="2"
                disabled={!canManageSettings}
                {...register("temperature", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.temperature && <p className="text-xs text-destructive">{errors.temperature.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="top_p" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("top_p", "Top P")}
              </label>
              <input
                id="top_p"
                type="number"
                step="0.1"
                min="0"
                max="1"
                disabled={!canManageSettings}
                {...register("top_p", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.top_p && <p className="text-xs text-destructive">{errors.top_p.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="max_output_tokens" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("max_output_tokens", "Max Output Tokens")}
              </label>
              <input
                id="max_output_tokens"
                type="number"
                disabled={!canManageSettings}
                {...register("max_output_tokens", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.max_output_tokens && <p className="text-xs text-destructive">{errors.max_output_tokens.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="frequency_penalty" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("frequency_penalty", "Frequency Penalty")}
              </label>
              <input
                id="frequency_penalty"
                type="number"
                step="0.1"
                min="0"
                max="2"
                disabled={!canManageSettings}
                {...register("frequency_penalty", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.frequency_penalty && <p className="text-xs text-destructive">{errors.frequency_penalty.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="presence_penalty" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("presence_penalty", "Presence Penalty")}
              </label>
              <input
                id="presence_penalty"
                type="number"
                step="0.1"
                min="0"
                max="2"
                disabled={!canManageSettings}
                {...register("presence_penalty", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.presence_penalty && <p className="text-xs text-destructive">{errors.presence_penalty.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="input_cost_per_1k_tokens" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("input_cost_per_1k_tokens", "Input Cost per 1K Tokens")}
              </label>
              <input
                id="input_cost_per_1k_tokens"
                type="number"
                step="0.000001"
                min="0"
                disabled={!canManageSettings}
                {...register("input_cost_per_1k_tokens", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.input_cost_per_1k_tokens && <p className="text-xs text-destructive">{errors.input_cost_per_1k_tokens.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="output_cost_per_1k_tokens" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("output_cost_per_1k_tokens", "Output Cost per 1K Tokens")}
              </label>
              <input
                id="output_cost_per_1k_tokens"
                type="number"
                step="0.000001"
                min="0"
                disabled={!canManageSettings}
                {...register("output_cost_per_1k_tokens", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.output_cost_per_1k_tokens && <p className="text-xs text-destructive">{errors.output_cost_per_1k_tokens.message}</p>}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="system_prompt_version" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("system_prompt_version", "System Prompt Version")}
              </label>
              <input
                id="system_prompt_version"
                type="text"
                disabled={!canManageSettings}
                {...register("system_prompt_version")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.system_prompt_version && <p className="text-xs text-destructive">{errors.system_prompt_version.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="prompt_version" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("prompt_version", "Prompt Version")}
              </label>
              <input
                id="prompt_version"
                type="text"
                disabled={!canManageSettings}
                {...register("prompt_version")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.prompt_version && <p className="text-xs text-destructive">{errors.prompt_version.message}</p>}
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">{renderLabel("enable_token_tracking", "Enable Token Tracking")}</h3>
                <p className="text-sm text-muted-foreground mt-1">Track token usage for requests.</p>
              </div>
              <input
                type="checkbox"
                disabled={!canManageSettings}
                {...register("enable_token_tracking")}
                className="h-5 w-5 disabled:opacity-50"
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">{renderLabel("enable_streaming", "Enable Streaming")}</h3>
                <p className="text-sm text-muted-foreground mt-1">Stream model responses when supported.</p>
              </div>
              <input
                type="checkbox"
                disabled={!canManageSettings}
                {...register("enable_streaming")}
                className="h-5 w-5 disabled:opacity-50"
              />
            </div>
          </div>

          <div className="flex justify-end gap-3">
            {canManageSettings && (
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={isTestingConnection}
                className="rounded-lg border border-border px-5 py-2 text-sm font-medium transition hover:bg-muted disabled:opacity-50"
              >
                {isTestingConnection ? "Testing..." : "Test Connection"}
              </button>
            )}

            <button
              type="submit"
              disabled={!isDirty || isSaving || !canManageSettings}
              className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? "Saving..." : "Save AI Settings"}
            </button>
          </div>
        </form>

        {connectionResult && (
          <div className="mt-6 rounded-lg border border-green-700/40 bg-green-950/20 p-4">
            <h3 className="font-semibold text-green-400">Connection Successful</h3>
            <div className="mt-3 space-y-2 text-sm">
              <p><strong>Provider:</strong> {connectionResult.provider}</p>
              <p><strong>Model:</strong> {connectionResult.model}</p>
              <p><strong>Latency:</strong> {connectionResult.latency_ms.toFixed(2)} ms</p>
              <p><strong>Response:</strong> {connectionResult.response}</p>
              <p><strong>Total Tokens:</strong> {connectionResult.token_usage.total_tokens}</p>
              <p><strong>Estimated Cost:</strong> ${connectionResult.token_usage.estimated_cost}</p>
            </div>
          </div>
        )}

        {Object.keys(errors).length > 0 && <p className="mt-4 text-sm text-destructive">Validation is active.</p>}
      </div>
    </div>
  );
};

export default AISettings;
