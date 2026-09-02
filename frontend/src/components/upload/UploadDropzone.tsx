import React, { useCallback, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileUp, Loader2, X } from "lucide-react";

import { uploadDocument } from "@/services/api/workItem";
import { ApiError } from "@/services/api/errors";

const resolveWorkItemId = (response: unknown): string | null => {
  if (typeof response !== "object" || response === null) {
    return null;
  }

  const record = response as Record<string, unknown>;

  if (typeof record.id === "string") {
    return record.id;
  }

  /* ARCH-0V: the `work_item` wrapper branch was removed. The upload
     route is `@router.post("", response_model=WorkItemResponse)` in
     app/api/v1/work_items.py and has always returned the flat object.
     The branch below it was defending against a shape the server has
     never sent, which is how a phantom field survives four phases. */
  return null;
};

const DEFAULT_MAX_SIZE_MB = 25;

const DEFAULT_ACCEPT = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/webp",
] as const;

export interface UploadDropzoneProps {
  readonly workspaceId: string;
  readonly maxSizeMb?: number;
  readonly allowedTypes?: readonly string[];
  readonly onUploaded?: (workItemId: string) => void;
  readonly disabled?: boolean;
  readonly className?: string;
}

interface Rejection {
  readonly fileName: string;
  readonly reason: string;
}

export const UploadDropzone: React.FC<UploadDropzoneProps> = ({
  workspaceId,
  maxSizeMb = DEFAULT_MAX_SIZE_MB,
  allowedTypes = DEFAULT_ACCEPT,
  onUploaded,
  disabled = false,
  className = "",
}) => {
  const inputRef = useRef<HTMLInputElement>(null);

  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [failure, setFailure] = useState<string | null>(null);

  const dragDepth = useRef(0);
  const maxBytes = useMemo(() => maxSizeMb * 1024 * 1024, [maxSizeMb]);
  const acceptAttr = useMemo(() => allowedTypes.join(","), [allowedTypes]);

  const validate = useCallback(
    (file: File): string | null => {
      if (file.size === 0) {
        return "The file is empty.";
      }
      if (file.size > maxBytes) {
        const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
        return `${sizeMb}MB exceeds the ${maxSizeMb}MB limit for this workspace.`;
      }
      if (file.type && !allowedTypes.includes(file.type)) {
        return `${file.type} isn't accepted here.`;
      }
      return null;
    },
    [allowedTypes, maxBytes, maxSizeMb],
  );

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || disabled) {
        return;
      }

      setRejections([]);
      setFailure(null);

      const accepted: File[] = [];
      const refused: Rejection[] = [];

      Array.from(files).forEach((file) => {
        const reason = validate(file);
        if (reason) {
          refused.push({ fileName: file.name, reason });
        } else {
          accepted.push(file);
        }
      });

      setRejections(refused);

      if (accepted.length === 0) {
        return;
      }

      setUploading(true);
      setProgress(0);

      try {
        for (let index = 0; index < accepted.length; index += 1) {
          const file = accepted[index];
          if (!file) {
            continue;
          }

          const result = await uploadDocument(workspaceId, file, (event) => {
            if (event.total) {
              const fileFraction = event.loaded / event.total;
              const overall = (index + fileFraction) / accepted.length;
              setProgress(Math.round(overall * 100));
            }
          });

          const workItemId = resolveWorkItemId(result);
          if (workItemId) {
            onUploaded?.(workItemId);
          }
        }

        setProgress(100);
      } catch (error) {
        setFailure(
          error instanceof ApiError
            ? error.message
            : "The upload didn't finish. Try again.",
        );
      } finally {
        setUploading(false);
        if (inputRef.current) {
          inputRef.current.value = "";
        }
      }
    },
    [disabled, onUploaded, validate, workspaceId],
  );

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      void handleFiles(event.dataTransfer.files);
    },
    [handleFiles],
  );

  const busy = uploading || disabled;

  return (
    <div className={className}>
      <div
        onDragEnter={(event) => {
          event.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragging(false);
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
        }}
        onDrop={onDrop}
        className={[
          "rounded-lg border-2 border-dashed p-6 text-center transition-colors",
          dragging ? "border-primary bg-primary/5" : "border-border",
          busy ? "opacity-60" : "",
        ].join(" ")}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={acceptAttr}
          disabled={busy}
          onChange={(event) => void handleFiles(event.target.files)}
          className="sr-only"
          id="fp-upload-input"
        />

        {uploading ? (
          <div role="status" className="space-y-3">
            <Loader2
              className="mx-auto h-6 w-6 animate-spin text-muted-foreground"
              aria-hidden="true"
            />
            <p className="text-sm text-muted-foreground">
              Uploading… {progress}%
            </p>
            <div
              className="mx-auto h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuenow={progress}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full bg-primary transition-[width] duration-200"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        ) : (
          <>
            <FileUp
              className="mx-auto h-6 w-6 text-muted-foreground"
              aria-hidden="true"
            />
            <p className="mt-2 text-sm">
              <label
                htmlFor="fp-upload-input"
                className="cursor-pointer font-medium text-primary hover:underline"
              >
                Choose a file
              </label>{" "}
              or drag it here
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Up to {maxSizeMb}MB ·{" "}
              {allowedTypes
                .map((type) => type.split("/")[1]?.toUpperCase() ?? type)
                .join(", ")}
            </p>
          </>
        )}
      </div>

      {rejections.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {rejections.map((rejection) => (
            <li
              key={rejection.fileName}
              className="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs"
            >
              <AlertTriangle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1">
                <span className="font-medium">{rejection.fileName}</span> —{" "}
                {rejection.reason}
              </span>
              <button
                type="button"
                aria-label={`Dismiss ${rejection.fileName}`}
                onClick={() =>
                  setRejections((current) =>
                    current.filter((r) => r.fileName !== rejection.fileName),
                  )
                }
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {failure && (
        <p
          role="alert"
          className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
        >
          {failure}
        </p>
      )}
    </div>
  );
};

export default UploadDropzone;
