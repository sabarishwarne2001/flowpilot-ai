import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  X,
} from "lucide-react";

import {
  trayCounts,
  useUploadTrayStore,
  type TrayFile,
} from "@/store/useUploadTrayStore";

/**
 * ARCH-38 — the upload tray.
 *
 * Bottom-right, fixed, and rendered from the app shell rather than a page, so
 * it survives navigation. Per file: progress, pause, resume, retry, cancel.
 * The summary line is the one the specification asks for, verbatim in shape:
 * "142 of 150 processed, 3 failed · View failures".
 */

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(0)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const StatusIcon: React.FC<{ status: TrayFile["status"] }> = ({ status }) => {
  if (status === "done") {
    return (
      <CheckCircle2
        className="h-4 w-4 shrink-0 text-emerald-600"
        aria-hidden="true"
      />
    );
  }
  if (status === "failed") {
    return (
      <AlertTriangle
        className="h-4 w-4 shrink-0 text-destructive"
        aria-hidden="true"
      />
    );
  }
  if (status === "uploading") {
    return (
      <Loader2
        className="h-4 w-4 shrink-0 animate-spin text-muted-foreground"
        aria-hidden="true"
      />
    );
  }
  if (status === "paused") {
    return <Pause className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />;
  }
  return (
    <div
      className="h-4 w-4 shrink-0 rounded-full border border-border"
      aria-hidden="true"
    />
  );
};

const TrayRow: React.FC<{ file: TrayFile }> = ({ file }) => {
  const pause = useUploadTrayStore((state) => state.pause);
  const resume = useUploadTrayStore((state) => state.resume);
  const retry = useUploadTrayStore((state) => state.retry);
  const cancel = useUploadTrayStore((state) => state.cancel);

  const percent =
    file.size > 0
      ? Math.min(100, Math.round((file.uploadedBytes / file.size) * 100))
      : 0;

  return (
    <li className="flex items-start gap-2 px-3 py-2 text-xs">
      <StatusIcon status={file.status} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium" title={file.relativePath}>
          {file.name}
        </p>
        {file.status === "failed" && file.error ? (
          <p className="mt-0.5 text-destructive">{file.error}</p>
        ) : (
          <p className="mt-0.5 text-muted-foreground">
            {formatBytes(file.uploadedBytes)} of {formatBytes(file.size)}
            {file.status === "paused" ? " · paused" : ""}
          </p>
        )}
        {(file.status === "uploading" || file.status === "paused") && (
          <div
            className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Uploading ${file.name}`}
          >
            <div
              className="h-full bg-primary transition-[width] duration-200"
              style={{ width: `${percent}%` }}
            />
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {file.status === "uploading" && (
          <button
            type="button"
            aria-label={`Pause ${file.name}`}
            onClick={() => pause(file.id)}
            className="rounded p-1 text-muted-foreground hover:bg-muted"
          >
            <Pause className="h-3.5 w-3.5" />
          </button>
        )}
        {file.status === "paused" && (
          <button
            type="button"
            aria-label={`Resume ${file.name}`}
            onClick={() => resume(file.id)}
            className="rounded p-1 text-muted-foreground hover:bg-muted"
          >
            <Play className="h-3.5 w-3.5" />
          </button>
        )}
        {file.status === "failed" && (
          <button
            type="button"
            aria-label={`Retry ${file.name}`}
            onClick={() => retry(file.id)}
            className="rounded p-1 text-muted-foreground hover:bg-muted"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
        {file.status !== "done" && file.status !== "cancelled" && (
          <button
            type="button"
            aria-label={`Cancel ${file.name}`}
            onClick={() => cancel(file.id)}
            className="rounded p-1 text-muted-foreground hover:bg-muted"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </li>
  );
};

export const UploadTray: React.FC = () => {
  const files = useUploadTrayStore((state) => state.files);
  const isOpen = useUploadTrayStore((state) => state.isOpen);
  const dismiss = useUploadTrayStore((state) => state.dismiss);
  const [expanded, setExpanded] = useState(true);
  const [failuresOnly, setFailuresOnly] = useState(false);

  const counts = useMemo(() => trayCounts(files), [files]);
  const visible = useMemo(
    () => (failuresOnly ? files.filter((file) => file.status === "failed") : files),
    [failuresOnly, files],
  );

  if (!isOpen || files.length === 0) {
    return null;
  }

  return (
    <aside
      aria-label="Uploads"
      className="fixed bottom-4 right-4 z-50 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border bg-background shadow-lg"
    >
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium" aria-live="polite">
            {counts.done} of {counts.total} processed
            {counts.failed > 0 ? `, ${counts.failed} failed` : ""}
            {counts.failed > 0 && (
              <>
                {" · "}
                <button
                  type="button"
                  onClick={() => setFailuresOnly((value) => !value)}
                  className="font-medium text-primary hover:underline"
                >
                  {failuresOnly ? "Show all" : "View failures"}
                </button>
              </>
            )}
          </p>
        </div>
        <button
          type="button"
          aria-label={expanded ? "Collapse uploads" : "Expand uploads"}
          onClick={() => setExpanded((value) => !value)}
          className="rounded p-1 text-muted-foreground hover:bg-muted"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronUp className="h-4 w-4" />
          )}
        </button>
        <button
          type="button"
          aria-label="Clear finished uploads"
          onClick={dismiss}
          disabled={counts.active > 0}
          className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-40"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {expanded && (
        <ul className="max-h-72 divide-y divide-border overflow-y-auto">
          {visible.map((file) => (
            <TrayRow key={file.id} file={file} />
          ))}
        </ul>
      )}
    </aside>
  );
};

export default UploadTray;
