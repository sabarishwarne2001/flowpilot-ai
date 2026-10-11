/**
 * The upload tray on the workspace overview and the Documents page.
 *
 * F-160: each selected file is uploaded on its own. One file the server refuses (a corrupt PDF, a
 * type the workspace does not accept) used to throw out of the loop: the files after it were never
 * sent and silently dropped from the list, the page did not refresh for the ones already uploaded,
 * and the error escaped as an unhandled promise rejection. Now every file gets its own outcome; a
 * refused file stays in the list with the server's reason, so it can be removed or retried.
 *
 * The accepted types and the size limit are the workspace's own (Settings → Documents), not a
 * hard-coded "PDF, PNG, JPG, 100 MB" that disagreed with what the server enforces.
 */
import React, {
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, FileText, Loader2, RotateCw, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";

import { workItemApi } from "@/services/api/workItem";
import { ApiError } from "@/services/api/client";
import { getDocumentSettings } from "@/services/api/document-settings";
import { settingsKeys, usageKeys } from "@/services/api/queryKeys";
import { getPlanAllowance } from "@/services/api/billing";
import PlanAllowance from "@/components/billing/PlanAllowance";
import { formatBytes } from "@/utils/formatters";
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";

/** What the server accepts when a workspace has not narrowed it (file_validation_service). */
const DEFAULT_EXTENSIONS = ["pdf", "png", "jpg", "jpeg"] as const;
const DEFAULT_MAX_MB = 100;

const EXTENSION_ALIASES: Readonly<Record<string, readonly string[]>> = {
  jpg: ["jpg", "jpeg"],
  jpeg: ["jpg", "jpeg"],
  tif: ["tif", "tiff"],
  tiff: ["tif", "tiff"],
};

interface UploadTrayProps {
  readonly onUploadSuccess?: () => void;
  readonly className?: string;
  /** One row instead of a tall drop area (the overview, where the numbers matter more). */
  readonly compact?: boolean;
}

interface QueuedFile {
  readonly file: File;
  readonly key: string;
  readonly error: string | null;
}

const fileKey = (file: File): string => `${file.name}:${file.size}:${file.lastModified}`;

const extensionOf = (name: string): string => {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
};

const describeError = (error: unknown, name: string): string => {
  if (error instanceof ApiError && error.message) {
    return error.message;
  }
  return `"${name}" could not be uploaded. Check your connection and try again.`;
};

export const UploadTray: React.FC<UploadTrayProps> = ({
  onUploadSuccess,
  className = "",
  compact = false,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const workspaceId = useActiveWorkspaceId();

  const [isDragActive, setIsDragActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const [queue, setQueue] = useState<readonly QueuedFile[]>([]);

  const settings = useQuery({
    queryKey: settingsKeys.document(workspaceId ?? ""),
    queryFn: () => getDocumentSettings(workspaceId!),
    enabled: Boolean(workspaceId),
    staleTime: 60_000,
    retry: false,
  });

  const extensions = useMemo(() => {
    const configured = (settings.data?.allowed_file_types ?? "")
      .split(",")
      .map((value) => value.trim().replace(/^\./, "").toLowerCase())
      .filter(Boolean);
    const base = configured.length > 0 ? configured : [...DEFAULT_EXTENSIONS];
    return Array.from(new Set(base.flatMap((ext) => EXTENSION_ALIASES[ext] ?? [ext])));
  }, [settings.data?.allowed_file_types]);
  // Campaign session 1: the plan's file size bounds the workspace setting (Free: 10 MB).
  const allowance = useQuery({
    queryKey: usageKeys.allowance(workspaceId ?? ""),
    queryFn: () => getPlanAllowance(workspaceId!),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
    retry: false,
  });
  const workspaceMb = settings.data?.max_upload_size ?? DEFAULT_MAX_MB;
  const planMb = allowance.data?.max_file_mb;
  const maxMb = planMb !== undefined ? Math.min(workspaceMb, planMb) : workspaceMb;
  const planBinds = planMb !== undefined && planMb < workspaceMb;
  const maxBytes = maxMb * 1024 * 1024;
  const typesLabel = useMemo(
    () => Array.from(new Set(extensions.map((ext) => (ext === "jpeg" ? "jpg" : ext === "tif" ? "tiff" : ext))))
      .map((ext) => ext.toUpperCase())
      .join(", "),
    [extensions],
  );

  const validateFile = (file: File): string | null => {
    if (!extensions.includes(extensionOf(file.name))) {
      return `"${file.name}" is not a supported file type. This workspace accepts ${typesLabel}.`;
    }
    if (file.size > maxBytes) {
      return planBinds
        ? `"${file.name}" is larger than the ${maxMb} MB your ${allowance.data?.plan_name ?? ""} plan accepts.`
        : `"${file.name}" is larger than this workspace's ${maxMb} MB limit.`;
    }
    if (file.size === 0) {
      return `"${file.name}" is empty.`;
    }
    return null;
  };

  const processFiles = (files: FileList): void => {
    const accepted: QueuedFile[] = [];
    const known = new Set(queue.map((entry) => entry.key));
    for (const file of Array.from(files)) {
      const validationError = validateFile(file);
      if (validationError) {
        toast.error(validationError);
        continue;
      }
      const key = fileKey(file);
      if (known.has(key)) {
        toast.error(`"${file.name}" has already been selected.`);
        continue;
      }
      known.add(key);
      accepted.push({ file, key, error: null });
    }
    if (accepted.length > 0) {
      setQueue((previous) => [...previous, ...accepted]);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    if (!isUploading) {
      setIsDragActive(true);
    }
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    setIsDragActive(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    setIsDragActive(false);
    if (!isUploading && event.dataTransfer.files.length > 0) {
      processFiles(event.dataTransfer.files);
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    if (event.target.files) {
      processFiles(event.target.files);
    }
  };

  const triggerFileSelect = (): void => {
    if (!isUploading) {
      fileInputRef.current?.click();
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      triggerFileSelect();
    }
  };

  const removeFile = (key: string): void => {
    setQueue((previous) => previous.filter((entry) => entry.key !== key));
  };

  const handleUploadSubmit = async (): Promise<void> => {
    if (isUploading || queue.length === 0 || !workspaceId) {
      return;
    }
    setIsUploading(true);
    const failed = new Map<string, string>();
    let uploaded = 0;
    try {
      for (const entry of queue) {
        setCurrent(entry.key);
        try {
          const created = await workItemApi.uploadDocument(workspaceId, entry.file);
          uploaded += 1;
          if (created.duplicate_of) {
            toast.info(
              `"${entry.file.name}" is identical to "${created.duplicate_of.original_filename}", already in this workspace. It was uploaded and marked as a duplicate.`,
            );
          }
        } catch (error) {
          failed.set(entry.key, describeError(error, entry.file.name));
        }
      }
    } finally {
      setCurrent(null);
      setIsUploading(false);
    }

    setQueue((previous) =>
      previous
        .filter((entry) => failed.has(entry.key))
        .map((entry) => ({ ...entry, error: failed.get(entry.key) ?? null })),
    );
    if (uploaded > 0) {
      toast.success(
        uploaded === 1 ? "1 document uploaded. Processing has started." : `${uploaded} documents uploaded. Processing has started.`,
      );
      onUploadSuccess?.();
    }
    if (failed.size > 0) {
      toast.error(
        failed.size === 1
          ? "1 file was not uploaded. Its reason is shown in the list."
          : `${failed.size} files were not uploaded. Their reasons are shown in the list.`,
      );
    }
  };

  const failedCount = queue.filter((entry) => entry.error).length;

  return (
    <div className={`space-y-4 ${className}`}>
      {workspaceId && <PlanAllowance workspaceId={workspaceId} meters={["document.upload", "ocr.page"]} />}
      <div
        role="button"
        tabIndex={isUploading ? -1 : 0}
        aria-label="Upload documents"
        aria-disabled={isUploading}
        onClick={triggerFileSelect}
        onKeyDown={handleKeyDown}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`group relative flex cursor-pointer overflow-hidden rounded-xl border border-dashed transition-all duration-200 ${
          compact
            ? "flex-row items-center gap-3 px-4 py-3 text-left"
            : "flex-col items-center justify-center space-y-3 px-8 py-9 text-center"
        } ${
          isUploading
            ? "cursor-not-allowed opacity-60"
            : isDragActive
            ? "border-primary bg-primary/[0.06] shadow-glow"
            : "border-border-strong/80 bg-card/40 hover:border-primary/50 hover:bg-primary/[0.03]"
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          accept={extensions.map((ext) => `.${ext}`).join(",")}
          onChange={handleFileChange}
          aria-label="Upload document picker"
        />

        <div className={`flex shrink-0 items-center justify-center rounded-xl border border-border bg-gradient-to-b from-card to-muted/60 text-muted-foreground shadow-elevation-1 transition-colors group-hover:text-primary ${compact ? "h-9 w-9" : "h-11 w-11"}`}>
          <UploadCloud className={compact ? "h-4 w-4" : "h-5 w-5"} />
        </div>

        <div className={`select-none ${compact ? "min-w-0 space-y-0.5" : "space-y-1"}`}>
          <p className="text-sm font-medium tracking-tight text-foreground">
            Click to upload or drag & drop files
          </p>
          <p className="text-xs text-muted-foreground" data-testid="upload-limits">
            {typesLabel} • Maximum {maxMb} MB per file
          </p>
        </div>
      </div>

      {queue.length > 0 && (
        <div className="space-y-3 rounded-xl border border-border/60 bg-card p-4 dark:border-border/40">
          <h3 className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground select-none">
            Selected Files ({queue.length})
          </h3>

          <ul className="space-y-2">
            {queue.map((entry) => {
              const uploadingThis = current === entry.key;
              return (
                <li
                  key={entry.key}
                  data-testid="upload-queue-item"
                  className={`flex items-center justify-between gap-3 rounded-lg border p-2.5 ${
                    entry.error
                      ? "border-destructive/30 bg-destructive/[0.04]"
                      : "border-border/40 bg-muted/20 dark:bg-muted/5"
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-3">
                    {uploadingThis ? (
                      <Loader2 className="h-5 w-5 flex-shrink-0 animate-spin text-primary" aria-label="Uploading" />
                    ) : entry.error ? (
                      <AlertCircle className="h-5 w-5 flex-shrink-0 text-destructive" aria-hidden />
                    ) : (
                      <FileText className="h-5 w-5 flex-shrink-0 text-primary/80" aria-hidden />
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{entry.file.name}</p>
                      {entry.error ? (
                        <p className="mt-0.5 text-[11px] font-medium text-destructive" role="alert">
                          {entry.error}
                        </p>
                      ) : (
                        <p className="mt-1 text-[10px] font-semibold text-muted-foreground">
                          {formatBytes(entry.file.size)}
                        </p>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    disabled={isUploading}
                    onClick={(event) => {
                      event.stopPropagation();
                      removeFile(entry.key);
                    }}
                    aria-label={`Remove ${entry.file.name}`}
                    className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground disabled:opacity-50"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="flex justify-end pt-2">
            <button
              type="button"
              onClick={() => void handleUploadSubmit()}
              disabled={isUploading || queue.length === 0}
              className="fp-btn fp-btn-primary min-w-[130px] text-xs font-semibold"
            >
              {isUploading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Uploading...
                </>
              ) : failedCount === queue.length ? (
                <>
                  <RotateCw className="h-3.5 w-3.5" />
                  Retry
                </>
              ) : (
                "Start Ingestion"
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default UploadTray;
