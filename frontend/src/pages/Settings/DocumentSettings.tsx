import React, { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  getDocumentSettings,
  updateDocumentSettings,
} from "@/services/api/document-settings";
import { ApiError } from "@/services/api/client";
import InfoTooltip from "@/components/common/InfoTooltip";
import { DOCUMENT_FIELD_HELP } from "@/constants/documentFieldHelp";
import {
  documentSettingsSchema,
  type DocumentSettingsFormData,
} from "@/schemas/documentSettings";
import { canManageWorkspaceSettings } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import KnowledgeBaseReindex from "@/components/settings/KnowledgeBaseReindex";
import PresetGallery from "@/components/settings/PresetGallery";
import { useUnsavedChangesGuard } from "@/hooks/useUnsavedChangesGuard";

export const DocumentSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const { workspace, workspaceRole } = useResolvedTenant();

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
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

  useUnsavedChangesGuard(isDirty);
  const { data: documentSettings, isLoading: isLoadingDocumentSettings } =
    useQuery({
      queryKey: ["document-settings", workspace.id],
      queryFn: () => getDocumentSettings(workspace.id),
    });

  const canManageSettings = canManageWorkspaceSettings(workspaceRole);

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
      mutationFn: (data: DocumentSettingsFormData) => updateDocumentSettings(workspace.id, data),
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
          <p className="mt-2 text-sm text-muted-foreground">
            These parameters control how uploaded files are processed, vectorized, and parsed.
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

            {/* HARDENING-T2:D13. The embedding model is platform-wide (and pinned by a
                database constraint); OCR runs with one process-wide language. Both were
                free-text inputs nothing read. They are shown as the facts they are. */}
            <div className="space-y-2">
              <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("embedding_model", "Embedding Model")}
              </span>
              <div className="rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
                <span className="font-mono">
                  {documentSettings?.platform_embedding_model ?? "sentence-transformers/all-MiniLM-L6-v2"}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Platform-managed · {documentSettings?.platform_embedding_dimension ?? 384}-dimension vectors. Changing it
                  would require re-indexing every document, so it is not a workspace setting.
                </span>
              </div>
            </div>

            <div className="space-y-2">
              <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("ocr_language", "OCR Language")}
              </span>
              <div className="rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
                <span className="font-mono">{documentSettings?.platform_ocr_language ?? "en"}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Set for the OCR service as a whole by your administrator.
                </span>
              </div>
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

            {/* HARDENING-T2:D13. Chips over the platform's accepted types; the upload paths
                now enforce this list, and it can only narrow what the platform accepts. */}
            <div className="space-y-2">
              <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                {renderLabel("allowed_file_types", "Allowed File Types")}
              </span>
              <div role="group" aria-label="Allowed file types" className="flex flex-wrap gap-1.5">
                {(documentSettings?.supported_file_types ?? ["pdf", "png", "jpg", "jpeg"]).map((ext) => {
                  const current = (watch("allowed_file_types") || "")
                    .split(",")
                    .map((part) => part.trim().toLowerCase())
                    .filter(Boolean);
                  const selected = current.includes(ext);
                  const toggle = () => {
                    const next = selected ? current.filter((e) => e !== ext) : [...current, ext];
                    if (next.length === 0) {
                      toast.error("Allow at least one file type.");
                      return;
                    }
                    setValue("allowed_file_types", next.join(","), { shouldDirty: true, shouldValidate: true });
                  };
                  return (
                    <button
                      key={ext}
                      type="button"
                      aria-pressed={selected}
                      disabled={!canManageSettings}
                      onClick={toggle}
                      className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase transition disabled:opacity-50 ${
                        selected
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border bg-background text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {ext}
                    </button>
                  );
                })}
              </div>
              <input type="hidden" {...register("allowed_file_types")} />
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

      {/* ARCH38-S2:preset-gallery. The vertical packs live with the other
          document settings: a pack decides what is extracted from a document
          type, which is the same question the rest of this page answers. */}
      <div className="rounded-xl border border-border bg-card p-6">
        <PresetGallery
          workspaceId={workspace.id}
          canManage={canManageSettings}
        />
      </div>

      {/* Reindex Knowledge Base Section */}
      <KnowledgeBaseReindex
        workspaceId={workspace.id}
        canManage={canManageSettings}
      />
    </div>
  );
};

export default DocumentSettings;
