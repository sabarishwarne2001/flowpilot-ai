import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileUp, X } from "lucide-react";

import { ApiError } from "@/services/api/errors";
import { useUploadTrayStore } from "@/store/useUploadTrayStore";

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

  const enqueue = useUploadTrayStore((state) => state.enqueue);
  const trayFiles = useUploadTrayStore((state) => state.files);

  // ARCH38-S2:tray-handoff. The tray owns the upload now, so `onUploaded`
  // fires from the tray's state rather than from a resolved POST. `notified`
  // keeps it to once per file: the store updates on every progress tick.
  const watching = useRef<Set<string>>(new Set());
  const notified = useRef<Set<string>>(new Set());

  const [dragging, setDragging] = useState(false);
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (!onUploaded) {
      return;
    }
    trayFiles.forEach((file) => {
      if (
        file.status === "done" &&
        file.workItemId &&
        watching.current.has(file.id) &&
        !notified.current.has(file.id)
      ) {
        notified.current.add(file.id);
        onUploaded(file.workItemId);
      }
    });
  }, [onUploaded, trayFiles]);

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
    async (files: FileList | readonly File[] | null, relativePaths?: readonly string[]) => {
      if (!files || files.length === 0 || disabled) {
        return;
      }

      setRejections([]);
      setFailure(null);

      const accepted: File[] = [];
      const refused: Rejection[] = [];

      Array.from(files as ArrayLike<File>).forEach((file) => {
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

      // ARCH38-S2:tray-handoff. Files go to the tray, which uploads three at a
      // time with per-file state and its own failure isolation.
      //
      // What this replaces: a `for` loop whose `try` wrapped the WHOLE loop.
      // The first failure jumped to the catch and silently abandoned every
      // file after it -- a fifty-file drop could produce eleven documents and
      // one generic message about none of them.
      try {
        const ids = await enqueue(workspaceId, accepted, relativePaths);
        ids.forEach((id) => watching.current.add(id));
      } catch (error) {
        setFailure(
          error instanceof ApiError
            ? error.message
            : "The upload couldn't be started. Try again.",
        );
      } finally {
        if (inputRef.current) {
          inputRef.current.value = "";
        }
      }
    },
    [disabled, enqueue, validate, workspaceId],
  );

  /**
   * ARCH38-S2:folder-drop. A dropped folder arrives as a DataTransferItem with
   * a FileSystemEntry behind it; `dataTransfer.files` alone holds nothing for
   * a directory. Walking the entry tree is the only way to accept a folder.
   */
  const collectEntries = useCallback(
    async (items: DataTransferItemList): Promise<{ files: File[]; paths: string[] }> => {
      const files: File[] = [];
      const paths: string[] = [];

      const readDirectory = (reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> =>
        new Promise((resolve) => {
          reader.readEntries(
            (entries) => resolve(entries),
            () => resolve([]),
          );
        });

      const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
        if (entry.isFile) {
          const fileEntry = entry as FileSystemFileEntry;
          const file = await new Promise<File | null>((resolve) => {
            fileEntry.file(
              (value) => resolve(value),
              () => resolve(null),
            );
          });
          if (file) {
            files.push(file);
            paths.push(`${prefix}${file.name}`);
          }
          return;
        }
        if (entry.isDirectory) {
          const reader = (entry as FileSystemDirectoryEntry).createReader();
          // readEntries returns at most 100 at a time and signals the end with
          // an empty batch, so one call is not enough for a real folder.
          for (;;) {
            const batch = await readDirectory(reader);
            if (batch.length === 0) {
              break;
            }
            for (const child of batch) {
              await walk(child, `${prefix}${entry.name}/`);
            }
          }
        }
      };

      const roots: FileSystemEntry[] = [];
      for (let index = 0; index < items.length; index += 1) {
        const entry = items[index]?.webkitGetAsEntry?.();
        if (entry) {
          roots.push(entry);
        }
      }
      for (const root of roots) {
        await walk(root, "");
      }
      return { files, paths };
    },
    [],
  );

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      const items = event.dataTransfer.items;
      const supportsEntries =
        items && items.length > 0 && typeof items[0]?.webkitGetAsEntry === "function";
      if (supportsEntries) {
        void collectEntries(items).then(({ files, paths }) => {
          if (files.length > 0) {
            void handleFiles(files, paths);
          }
        });
        return;
      }
      void handleFiles(event.dataTransfer.files);
    },
    [collectEntries, handleFiles],
  );

  const busy = disabled;

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

        {
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
        }
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
