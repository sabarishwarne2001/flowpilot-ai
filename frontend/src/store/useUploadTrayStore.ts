import { create } from "zustand";

import {
  abortUploadSession,
  createBatch,
  uploadFileResumable,
  type BatchFileInput,
} from "@/services/api/ingestion";

/**
 * ARCH-38 — the upload tray's state.
 *
 * WHY A STORE AND NOT COMPONENT STATE
 * ===================================
 * The tray survives navigation. A person drops 150 files on the documents
 * page, goes to look at a dashboard while they process, and comes back: the
 * uploads are still running and the tray still shows them. Component state
 * dies at the first route change, which is why the pre-ARCH-38 dropzone could
 * only ever upload while you stared at it.
 *
 * CONCURRENCY 3
 * =============
 * Three files at a time. The `File` objects themselves live in a module-level
 * map rather than in the store, because a File is not serialisable and putting
 * one in a devtools-inspected store means every upload is retained in a
 * snapshot.
 *
 * WHAT IS DELIBERATELY NOT HERE
 * =============================
 * The list of parts already uploaded. The server owns that
 * (`upload_sessions.parts_received`), and `uploadFileResumable` asks for it on
 * resume. A browser-side copy is a second source of truth that goes stale on
 * exactly the failure this feature exists to survive.
 */

export type TrayFileStatus =
  | "queued"
  | "uploading"
  | "paused"
  | "processing"
  | "done"
  | "failed"
  | "cancelled";

export interface TrayFile {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly relativePath: string;
  readonly status: TrayFileStatus;
  readonly uploadedBytes: number;
  readonly sessionId?: string;
  readonly workItemId?: string;
  readonly batchItemId?: string;
  readonly error?: string;
}

export const UPLOAD_CONCURRENCY = 3;

/** Files are held outside the store: a File is not serialisable state. */
const handles = new Map<string, File>();
const controllers = new Map<string, AbortController>();

interface UploadTrayState {
  readonly isOpen: boolean;
  readonly workspaceId: string | null;
  readonly batchId: string | null;
  readonly files: readonly TrayFile[];
  /** Returns the tray ids created, so a caller can follow its own files. */
  readonly enqueue: (
    workspaceId: string,
    files: readonly File[],
    paths?: readonly string[],
  ) => Promise<string[]>;
  readonly pause: (id: string) => void;
  readonly resume: (id: string) => void;
  readonly retry: (id: string) => void;
  readonly cancel: (id: string) => void;
  readonly dismiss: () => void;
  readonly open: () => void;
  readonly close: () => void;
}

/**
 * `exactOptionalPropertyTypes` makes `Partial<TrayFile>` refuse an explicit
 * `undefined`, and clearing an error is exactly that: `{ error: undefined }`.
 * This mapped type admits the clear without widening every field to optional.
 */
type TrayPatch = { [K in keyof TrayFile]?: TrayFile[K] | undefined };

const patch = (
  files: readonly TrayFile[],
  id: string,
  change: TrayPatch,
): TrayFile[] =>
  files.map((file) => {
    if (file.id !== id) {
      return file;
    }
    // Spreading a TrayPatch widens every field to `| undefined`; the merged
    // object is a complete TrayFile by construction, so the assertion states
    // what the spread cannot express rather than hiding a real gap.
    return { ...file, ...change } as TrayFile;
  });

export const useUploadTrayStore = create<UploadTrayState>((set, get) => {
  /** Run one file, isolating its failure from every other file. */
  const runOne = async (entry: TrayFile): Promise<void> => {
    const state = get();
    const workspaceId = state.workspaceId;
    const handle = handles.get(entry.id);
    if (!workspaceId || !handle) {
      return;
    }

    const controller = new AbortController();
    controllers.set(entry.id, controller);
    set({ files: patch(get().files, entry.id, { status: "uploading" }) });

    try {
      const result = await uploadFileResumable(workspaceId, handle, {
        ...(entry.sessionId ? { sessionId: entry.sessionId } : {}),
        ...(entry.batchItemId ? { batchItemId: entry.batchItemId } : {}),
        signal: controller.signal,
        onSession: (sessionId) => {
          set({ files: patch(get().files, entry.id, { sessionId }) });
        },
        onProgress: (progress) => {
          set({
            files: patch(get().files, entry.id, {
              uploadedBytes: progress.uploadedBytes,
            }),
          });
        },
      });
      set({
        files: patch(get().files, entry.id, {
          status: "done",
          uploadedBytes: entry.size,
          workItemId: result.workItemId,
        }),
      });
    } catch (error) {
      const aborted =
        error instanceof DOMException && error.name === "AbortError";
      const current = get().files.find((file) => file.id === entry.id);
      if (aborted && current?.status === "paused") {
        return;
      }
      set({
        files: patch(get().files, entry.id, {
          status: aborted ? "cancelled" : "failed",
          error: aborted
            ? undefined
            : error instanceof Error
              ? error.message
              : "Upload failed.",
        }),
      });
    } finally {
      controllers.delete(entry.id);
    }
  };

  /**
   * Keep three uploads in flight.
   *
   * Each file is awaited inside its own `runOne`, which catches its own
   * failure. A rejection here would abandon the rest of the queue, which is
   * the pre-ARCH-38 defect this whole feature replaces.
   */
  const pump = async (): Promise<void> => {
    for (;;) {
      const files = get().files;
      const active = files.filter((file) => file.status === "uploading").length;
      const next = files.find((file) => file.status === "queued");
      if (!next || active >= UPLOAD_CONCURRENCY) {
        if (!files.some((file) => file.status === "uploading" || file.status === "queued")) {
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 150));
        continue;
      }
      void runOne(next);
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  };

  return {
    isOpen: false,
    workspaceId: null,
    batchId: null,
    files: [],

    enqueue: async (workspaceId, incoming, paths) => {
      if (incoming.length === 0) {
        return [];
      }
      const entries: TrayFile[] = incoming.map((file, index) => {
        const id = `${Date.now()}-${index}-${file.name}`;
        handles.set(id, file);
        return {
          id,
          name: file.name,
          size: file.size,
          relativePath: paths?.[index] ?? file.name,
          status: "queued",
          uploadedBytes: 0,
        };
      });

      set({
        isOpen: true,
        workspaceId,
        files: [...get().files, ...entries],
      });

      const payload: BatchFileInput[] = entries.map((entry) => ({
        client_key: entry.id,
        filename: entry.name,
        size_bytes: entry.size,
      }));

      try {
        const usedFolder = entries.some((entry) => entry.relativePath.includes("/"));
        const batch = await createBatch(
          workspaceId,
          payload,
          usedFolder ? "FOLDER" : "FILES",
        );
        set({
          batchId: batch.id,
          files: get().files.map((file) => {
            const item = batch.items.find((row) => row.client_key === file.id);
            return item ? { ...file, batchItemId: item.id } : file;
          }),
        });
      } catch {
        // A batch is bookkeeping. Without one the files still upload; they
        // just are not grouped, which is better than refusing the drop.
      }

      void pump();
      return entries.map((entry) => entry.id);
    },

    pause: (id) => {
      controllers.get(id)?.abort();
      set({ files: patch(get().files, id, { status: "paused" }) });
    },

    resume: (id) => {
      set({ files: patch(get().files, id, { status: "queued", error: undefined }) });
      void pump();
    },

    retry: (id) => {
      set({
        files: patch(get().files, id, {
          status: "queued",
          error: undefined,
          uploadedBytes: 0,
        }),
      });
      void pump();
    },

    cancel: (id) => {
      const entry = get().files.find((file) => file.id === id);
      controllers.get(id)?.abort();
      const workspaceId = get().workspaceId;
      if (workspaceId && entry?.sessionId) {
        void abortUploadSession(workspaceId, entry.sessionId).catch(() => {
          // The sweeper reclaims an abandoned session within the hour.
        });
      }
      handles.delete(id);
      set({ files: patch(get().files, id, { status: "cancelled" }) });
    },

    dismiss: () => {
      get().files.forEach((file) => {
        if (file.status === "uploading" || file.status === "queued") {
          return;
        }
        handles.delete(file.id);
      });
      set({
        files: get().files.filter(
          (file) => file.status === "uploading" || file.status === "queued" || file.status === "paused",
        ),
      });
    },

    open: () => set({ isOpen: true }),
    close: () => set({ isOpen: false }),
  };
});

export const trayCounts = (
  files: readonly TrayFile[],
): { done: number; failed: number; total: number; active: number } => ({
  done: files.filter((file) => file.status === "done").length,
  failed: files.filter((file) => file.status === "failed").length,
  total: files.length,
  active: files.filter(
    (file) => file.status === "uploading" || file.status === "queued",
  ).length,
});
