import React, {
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { FileText, Loader2, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";

import { workItemApi } from "@/services/api/workItem";
import { ApiError } from "@/services/api/client";
import { formatBytes } from "@/utils/formatters";
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";

const ALLOWED_MIME_TYPES = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/jpg",
] as const;

const MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024;

interface UploadTrayProps {
  readonly onUploadSuccess?: () => void;
  readonly className?: string;
}

export const UploadTray: React.FC<UploadTrayProps> = ({
  onUploadSuccess,
  className = "",
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const workspaceId = useActiveWorkspaceId();

  const [isDragActive, setIsDragActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<readonly File[]>([]);

  const validateFile = (file: File): string | null => {
    if (
      !ALLOWED_MIME_TYPES.includes(
        file.type as (typeof ALLOWED_MIME_TYPES)[number]
      )
    ) {
      return `File "${file.name}" has an unsupported format.`;
    }

    if (file.size > MAX_FILE_SIZE_BYTES) {
      return `File "${file.name}" exceeds the 100 MB upload limit.`;
    }

    return null;
  };

  const isDuplicateFile = (file: File): boolean => {
    return selectedFiles.some(
      (existing) =>
        existing.name === file.name &&
        existing.size === file.size &&
        existing.lastModified === file.lastModified
    );
  };

  const processFiles = (files: FileList): void => {
    const validFiles: File[] = [];

    for (const file of Array.from(files)) {
      const validationError = validateFile(file);

      if (validationError) {
        toast.error(validationError);
        continue;
      }

      if (isDuplicateFile(file)) {
        toast.error(`"${file.name}" has already been selected.`);
        continue;
      }

      validFiles.push(file);
    }

    if (validFiles.length > 0) {
      setSelectedFiles((previous) => [...previous, ...validFiles]);
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

    if (isUploading) {
      return;
    }

    if (event.dataTransfer.files.length > 0) {
      processFiles(event.dataTransfer.files);
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    if (!event.target.files) {
      return;
    }
    processFiles(event.target.files);
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

  const removeFile = (indexToRemove: number): void => {
    setSelectedFiles((previous) =>
      previous.filter((_, index) => index !== indexToRemove)
    );
  };

  const handleUploadSubmit = async (): Promise<void> => {
    if (isUploading || selectedFiles.length === 0 || !workspaceId) {
      return;
    }

    setIsUploading(true);

    try {
      for (const file of selectedFiles) {
        const uploadPromise = workItemApi.uploadDocument(workspaceId, file);

        toast.promise(uploadPromise, {
          loading: `Uploading "${file.name}"...`,
          success: `Successfully uploaded "${file.name}".`,
          error: (error: unknown) => {
            if (error instanceof ApiError) {
              return error.message ?? `Failed to upload "${file.name}".`;
            }
            return `Unexpected upload error for "${file.name}".`;
          },
        });

        await uploadPromise;
      }

      onUploadSuccess?.();
    } finally {
      setSelectedFiles([]);
      setIsUploading(false);
    }
  };

  return (
    <div className={`space-y-4 ${className}`}>
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
        className={`group relative flex cursor-pointer flex-col items-center justify-center space-y-3 overflow-hidden rounded-xl border border-dashed px-8 py-9 text-center transition-all duration-200 ${
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
          accept=".pdf,.png,.jpg,.jpeg"
          onChange={handleFileChange}
          aria-label="Upload document picker"
        />

        <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-gradient-to-b from-card to-muted/60 text-muted-foreground shadow-elevation-1 transition-colors group-hover:text-primary">
          <UploadCloud className="h-5 w-5" />
        </div>

        <div className="space-y-1 select-none">
          <p className="text-sm font-medium tracking-tight text-foreground">
            Click to upload or drag & drop files
          </p>
          <p className="text-xs text-muted-foreground">
            PDF, PNG, JPG, JPEG • Maximum 100 MB per file
          </p>
        </div>
      </div>

      {selectedFiles.length > 0 && (
        <div className="space-y-3 rounded-xl border border-border/60 bg-card p-4 dark:border-border/40">
          <h3 className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground select-none">
            Selected Files ({selectedFiles.length})
          </h3>

          <div className="space-y-2">
            {selectedFiles.map((file, index) => (
              <div
                key={`${file.name}-${file.lastModified}`}
                className="flex items-center justify-between rounded-lg border border-border/40 bg-muted/20 p-2.5 dark:bg-muted/5"
              >
                <div className="flex min-w-0 items-center space-x-3">
                  <FileText className="h-5 w-5 flex-shrink-0 text-primary/80" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{file.name}</p>
                    <p className="mt-1 text-[10px] font-semibold text-muted-foreground">
                      {formatBytes(file.size)}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  disabled={isUploading}
                  onClick={(event) => {
                    event.stopPropagation();
                    removeFile(index);
                  }}
                  aria-label={`Remove ${file.name}`}
                  className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>

          <div className="flex justify-end pt-2">
            <button
              type="button"
              onClick={handleUploadSubmit}
              disabled={isUploading || selectedFiles.length === 0}
              className="fp-btn fp-btn-primary min-w-[130px] text-xs font-semibold"
            >
              {isUploading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Uploading...
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
