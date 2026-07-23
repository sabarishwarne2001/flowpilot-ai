import React, { useEffect } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { getAISettings, updateAISettings } from "@/services/api/aiSettings";

import { ApiError } from "@/services/api/client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  aiSettingsSchema,
  type AISettingsFormData,
} from "@/schemas/aiSettings";

export const AISettings: React.FC = () => {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<AISettingsFormData>({
    resolver: zodResolver(aiSettingsSchema),

    defaultValues: {
      provider: "GROQ",
      model: "llama3-8b-8192",
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

  const { data: aiSettings, isLoading: isLoadingAISettings } = useQuery({
    queryKey: ["ai-settings"],
    queryFn: getAISettings,
  });

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

  const { mutateAsync: saveAISettings, isPending: isSaving } = useMutation({
    mutationFn: updateAISettings,

    onSuccess: () => {
      toast.success("AI settings saved successfully.");
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message);
        return;
      }

      toast.error("Failed to save AI settings.");
    },
  });

  const onSubmit = async (data: AISettingsFormData): Promise<void> => {
    await saveAISettings(data);
  };

  if (isLoadingAISettings) {
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

            <div className="space-y-4">
              <div className="h-12 rounded bg-muted" />
              <div className="h-12 rounded bg-muted" />
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
          Configure the default AI provider and model used throughout FlowPilot
          AI.
        </p>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-6">
          {/* Provider */}
          <div className="space-y-2">
            <label
              htmlFor="provider"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              AI Provider
            </label>

            <select
              id="provider"
              {...register("provider")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            >
              <option value="GROQ">Groq</option>
              <option value="GEMINI">Google Gemini</option>
              <option value="OPENAI">OpenAI</option>
              <option value="CLAUDE">Anthropic Claude</option>
            </select>

            {errors.provider && (
              <p className="text-xs text-destructive">
                {errors.provider.message}
              </p>
            )}
          </div>

          {/* Model */}
          <div className="space-y-2">
            <label
              htmlFor="model"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              Model
            </label>

            <input
              id="model"
              type="text"
              placeholder="llama3-8b-8192"
              {...register("model")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.model && (
              <p className="text-xs text-destructive">{errors.model.message}</p>
            )}
          </div>

          {/* AI Parameters */}
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {/* Temperature */}
            <div className="space-y-2">
              <label
                htmlFor="temperature"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Temperature
              </label>

              <input
                id="temperature"
                type="number"
                step="0.1"
                min="0"
                max="2"
                {...register("temperature", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.temperature && (
                <p className="text-xs text-destructive">
                  {errors.temperature.message}
                </p>
              )}
            </div>

            {/* Top P */}
            <div className="space-y-2">
              <label
                htmlFor="top_p"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Top P
              </label>

              <input
                id="top_p"
                type="number"
                step="0.1"
                min="0"
                max="1"
                {...register("top_p", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.top_p && (
                <p className="text-xs text-destructive">
                  {errors.top_p.message}
                </p>
              )}
            </div>

            {/* Max Output Tokens */}
            <div className="space-y-2">
              <label
                htmlFor="max_output_tokens"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Max Output Tokens
              </label>

              <input
                id="max_output_tokens"
                type="number"
                {...register("max_output_tokens", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.max_output_tokens && (
                <p className="text-xs text-destructive">
                  {errors.max_output_tokens.message}
                </p>
              )}
            </div>

            {/* Frequency Penalty */}
            <div className="space-y-2">
              <label
                htmlFor="frequency_penalty"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Frequency Penalty
              </label>

              <input
                id="frequency_penalty"
                type="number"
                step="0.1"
                min="0"
                max="2"
                {...register("frequency_penalty", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.frequency_penalty && (
                <p className="text-xs text-destructive">
                  {errors.frequency_penalty.message}
                </p>
              )}
            </div>

            {/* Presence Penalty */}
            <div className="space-y-2">
              <label
                htmlFor="presence_penalty"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Presence Penalty
              </label>

              <input
                id="presence_penalty"
                type="number"
                step="0.1"
                min="0"
                max="2"
                {...register("presence_penalty", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.presence_penalty && (
                <p className="text-xs text-destructive">
                  {errors.presence_penalty.message}
                </p>
              )}
            </div>

            {/* Input Cost per 1K Tokens */}
            <div className="space-y-2">
              <label
                htmlFor="input_cost_per_1k_tokens"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Input Cost per 1K Tokens
              </label>

              <input
                id="input_cost_per_1k_tokens"
                type="number"
                step="0.000001"
                min="0"
                {...register("input_cost_per_1k_tokens", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.input_cost_per_1k_tokens && (
                <p className="text-xs text-destructive">
                  {errors.input_cost_per_1k_tokens.message}
                </p>
              )}
            </div>

            {/* Output Cost per 1K Tokens */}
            <div className="space-y-2">
              <label
                htmlFor="output_cost_per_1k_tokens"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Output Cost per 1K Tokens
              </label>

              <input
                id="output_cost_per_1k_tokens"
                type="number"
                step="0.000001"
                min="0"
                {...register("output_cost_per_1k_tokens", {
                  valueAsNumber: true,
                })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.output_cost_per_1k_tokens && (
                <p className="text-xs text-destructive">
                  {errors.output_cost_per_1k_tokens.message}
                </p>
              )}
            </div>
          </div>

          {/* Version Settings */}
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {/* System Prompt Version */}
            <div className="space-y-2">
              <label
                htmlFor="system_prompt_version"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                System Prompt Version
              </label>

              <input
                id="system_prompt_version"
                type="text"
                {...register("system_prompt_version")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.system_prompt_version && (
                <p className="text-xs text-destructive">
                  {errors.system_prompt_version.message}
                </p>
              )}
            </div>

            {/* Prompt Version */}
            <div className="space-y-2">
              <label
                htmlFor="prompt_version"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Prompt Version
              </label>

              <input
                id="prompt_version"
                type="text"
                {...register("prompt_version")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />

              {errors.prompt_version && (
                <p className="text-xs text-destructive">
                  {errors.prompt_version.message}
                </p>
              )}
            </div>
          </div>

          {/* Feature Toggles */}
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">Enable Token Tracking</h3>
                <p className="text-sm text-muted-foreground">
                  Track token usage for requests.
                </p>
              </div>

              <input
                type="checkbox"
                {...register("enable_token_tracking")}
                className="h-5 w-5"
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">Enable Streaming</h3>
                <p className="text-sm text-muted-foreground">
                  Stream model responses when supported.
                </p>
              </div>

              <input
                type="checkbox"
                {...register("enable_streaming")}
                className="h-5 w-5"
              />
            </div>
          </div>

          {/* Save Button */}
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={!isDirty || isSaving}
              className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? "Saving..." : "Save AI Settings"}
            </button>
          </div>
        </form>

        {Object.keys(errors).length > 0 && (
          <p className="mt-4 text-sm text-destructive">Validation is active.</p>
        )}
      </div>
    </div>
  );
};

export default AISettings;
