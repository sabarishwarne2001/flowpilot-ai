import React, { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getDocumentSettings,
  updateDocumentSettings,
} from "@/services/api/document-settings";

import { ApiError } from "@/services/api/client";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import InfoTooltip from "@/components/common/InfoTooltip";
import { DOCUMENT_FIELD_HELP } from "@/constants/documentFieldHelp";

import {
  documentSettingsSchema,
  type DocumentSettingsFormData,
} from "@/schemas/documentSettings";
import { getMyMembership } from "@/services/api/workspace";

export const DocumentSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<DocumentSettingsFormData>({
    resolver: zodResolver(documentSettingsSchema),
    defaultValues: {
      chunk_size: 500,
      chunk_overlap: 100,
      embedding_model: "sentence-transformers/all-MiniLM-L6-v2",
      ocr_language: "eng",
      max_upload_size: 50,
      allowed_file_types: "pdf,png,jpg,jpeg",
      duplicate_detection: true,
      automatic_classification: true,
      automatic_summarization: false,
      automatic_entity_extraction: false,
    },
  });

  const { data: documentSettings, isLoading: isLoadingDocumentSettings } =
    useQuery({
      queryKey: ["document-settings"],
      queryFn: getDocumentSettings,
    });

  // Query workspace membership role to secure controls
  const { data: myMembership } = useQuery({
    queryKey: ["workspace_membership_me"],
    queryFn: getMyMembership,
    retry: false,
  });

  const canManageSettings = myMembership?.role === "OWNER" || myMembership?.role === "MANAGER";

  useEffect(() => {
    if (!documentSettings) {
      return;
    }

    reset({
      chunk_size: documentSettings.chunk_size,
      chunk_overlap: documentSettings.chunk_overlap,
      embedding_model: documentSettings.embedding_model,
      ocr_language: documentSettings.ocr_language,
      max_upload_size: documentSettings.max_upload_size,
      allowed_file_types: documentSettings.allowed_file_types,
      duplicate_detection: documentSettings.duplicate_detection,
      automatic_classification: documentSettings.automatic_classification,
      automatic_summarization: documentSettings.automatic_summarization,
      automatic_entity_extraction: documentSettings.automatic_entity_extraction,
    });
  }, [documentSettings, reset]);

  const { mutateAsync: saveDocumentSettings, isPending: isSaving } =
    useMutation({
      mutationFn: updateDocumentSettings,
      onSuccess: async () => {
        toast.success("Document settings saved successfully.");
        await queryClient.invalidateQueries({ queryKey: ["document-settings"] });
      },
      onError: (error: unknown) => {
        if (error instanceof ApiError) {
          toast.error(error.message);
          return;
        }
        toast.error("Failed to save document settings.");
      },
    });

  const onSubmit = async (data: DocumentSettingsFormData): Promise<void> => {
    await saveDocumentSettings(data);
  };

  const renderLabel = (key: keyof typeof DOCUMENT_FIELD_HELP, label: string) => {
    const help = DOCUMENT_FIELD_HELP[key]!;

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

  if (isLoadingDocumentSettings) {
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
        <h1 className="text-2xl font-bold">Document Settings</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Configure document ingestion, chunking, OCR, and automated extraction behaviors used throughout FlowPilot AI.
        </p>

        <div className="mt-6 rounded-lg border border-blue-900/50 bg-blue-950/20 p-4">
          <h3 className="text-sm font-semibold text-blue-300">Ingestion & Processing Parameters</h3>
          <p className="mt-2 text-sm text-slate-300">
            These parameters control how uploaded files are processed, vectorized, and parsed. Default options are highly optimized for baseline system flows.
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-6">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="chunk_size" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("chunk_size", "Chunk Size")}
              </label>
              <input
                id="chunk_size"
                type="number"
                disabled={!canManageSettings}
                {...register("chunk_size", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.chunk_size && <p className="text-xs text-destructive">{errors.chunk_size.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="chunk_overlap" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("chunk_overlap", "Chunk Overlap")}
              </label>
              <input
                id="chunk_overlap"
                type="number"
                disabled={!canManageSettings}
                {...register("chunk_overlap", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.chunk_overlap && <p className="text-xs text-destructive">{errors.chunk_overlap.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="embedding_model" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("embedding_model", "Embedding Model")}
              </label>
              <input
                id="embedding_model"
                type="text"
                disabled={!canManageSettings}
                placeholder="sentence-transformers/all-MiniLM-L6-v2"
                {...register("embedding_model")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.embedding_model && <p className="text-xs text-destructive">{errors.embedding_model.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="ocr_language" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("ocr_language", "OCR Language")}
              </label>
              <input
                id="ocr_language"
                type="text"
                disabled={!canManageSettings}
                placeholder="eng"
                {...register("ocr_language")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.ocr_language && <p className="text-xs text-destructive">{errors.ocr_language.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="max_upload_size" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("max_upload_size", "Max Upload Size (MB)")}
              </label>
              <input
                id="max_upload_size"
                type="number"
                disabled={!canManageSettings}
                {...register("max_upload_size", { valueAsNumber: true })}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.max_upload_size && <p className="text-xs text-destructive">{errors.max_upload_size.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="allowed_file_types" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("allowed_file_types", "Allowed File Types")}
              </label>
              <input
                id="allowed_file_types"
                type="text"
                disabled={!canManageSettings}
                placeholder="pdf,png,jpg,jpeg"
                {...register("allowed_file_types")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              />
              {errors.allowed_file_types && <p className="text-xs text-destructive">{errors.allowed_file_types.message}</p>}
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">{renderLabel("duplicate_detection", "Duplicate Detection")}</h3>
                <p className="text-sm text-muted-foreground mt-1">Detect and flag potential duplicate documents during upload.</p>
              </div>
              <input
                type="checkbox"
                disabled={!canManageSettings}
                {...register("duplicate_detection")}
                className="h-5 w-5 disabled:opacity-50"
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">{renderLabel("automatic_classification", "Automatic Classification")}</h3>
                <p className="text-sm text-muted-foreground mt-1">Automatically classify uploaded documents into categories.</p>
              </div>
              <input
                type="checkbox"
                disabled={!canManageSettings}
                {...register("automatic_classification")}
                className="h-5 w-5 disabled:opacity-50"
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">{renderLabel("automatic_summarization", "Automatic Summarization")}</h3>
                <p className="text-sm text-muted-foreground mt-1">Generate AI summaries immediately after document processing.</p>
              </div>
              <input
                type="checkbox"
                disabled={!canManageSettings}
                {...register("automatic_summarization")}
                className="h-5 w-5 disabled:opacity-50"
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <h3 className="font-medium">{renderLabel("automatic_entity_extraction", "Automatic Entity Extraction")}</h3>
                <p className="text-sm text-muted-foreground mt-1">Extract organizations, people, and locations automatically.</p>
              </div>
              <input
                type="checkbox"
                disabled={!canManageSettings}
                {...register("automatic_entity_extraction")}
                className="h-5 w-5 disabled:opacity-50"
              />
            </div>
          </div>

          <div className="flex justify-end gap-3">
            <button
              type="submit"
              disabled={!isDirty || isSaving || !canManageSettings}
              className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? "Saving..." : "Save Document Settings"}
            </button>
          </div>
        </form>

        {Object.keys(errors).length > 0 && <p className="mt-4 text-sm text-destructive">Validation is active.</p>}
      </div>
    </div>
  );
};

export default DocumentSettings;
