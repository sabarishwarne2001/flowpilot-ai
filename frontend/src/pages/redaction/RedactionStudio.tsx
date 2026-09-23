import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Eye, Loader2, Square } from "lucide-react";

import RedactionLockCard from "@/components/redaction/RedactionLockCard";
import {
  BUTTON_GHOST,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  HINT,
  PAGE_TITLE,
  SECTION_TITLE,
  SURFACE,
  SURFACE_DIALOG,
  SURFACE_INSET,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
// ARCH41-S1:studio-authorized-preview. See the hook for why this is not
// useAuthenticatedImage.
import { useAuthorizedBlobUrl } from "@/hooks/useAuthorizedBlobUrl";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import {
  addRegion,
  applyRedaction,
  getBundle,
  getRedaction,
  pagePreviewUrl,
  redactionKeys,
  toggleRegion,
} from "@/services/api/redaction";
import { ApiError } from "@/services/api/errors";
import type { RedactionJob, RedactionRegion } from "@/types/redaction";
import {
  DETECTOR_LABELS,
  PROFILE_LABELS,
  precisionBadge,
  precisionTitle,
} from "@/types/redaction";
import { formatTimestamp } from "@/utils/displayTime";
import { workItemDetailsPath, workItemsPath } from "@/routes/tenantPaths";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";

const CAPABILITY_KEY = "capability.redaction";
const PREVIEW_DPI = 110;
const POINTS_PER_INCH = 72;

/** Keyboard step sizes in PDF points. Shift resizes instead of moving. */
const NUDGE = 1;
const NUDGE_COARSE = 10;

/**
 * ARCH-32 §3.6 — the Redaction Studio.
 *
 * WHY THIS DOES NOT USE `components/pdf/PdfViewer`
 * ------------------------------------------------
 * `PdfViewer` renders through pdf.js in the browser. That is right for
 * citation highlighting, where the overlay is decoration over a document the
 * reader can already see.
 *
 * It is wrong here. §3.6 requires that "Preview result" render "from the same
 * rasterize code path the apply job uses, so what is previewed is what is
 * produced". pdf.js and PDFium do not lay out identically — different font
 * substitution, different hinting, sub-pixel differences in glyph advance —
 * and a box that sits correctly over a pdf.js rendering can sit a few points
 * off over PDFium's. On a redaction that is not a cosmetic difference; it is
 * the difference between covering a digit and clipping it.
 *
 * So the studio renders server-side PNGs from the phase's own rasterizer, via
 * `GET .../pages/{n}.png`. The reviewer is looking at PDFium's output, which
 * is what will be burned. The cost is a network round trip per page and no
 * text selection in the studio; both are the right trade for WYSIWYG on a
 * screen whose whole job is deciding what gets destroyed.
 *
 * COORDINATES
 * -----------
 * Regions are PDF points, origin BOTTOM-left. The preview is pixels, origin
 * TOP-left. `toCss` and `toPoints` are the only two places that convert, and
 * they are inverses. Every other function in this file works in points.
 */
const RedactionStudio: React.FC = () => {
  const {
    jobId = "",
    orgSlug = "",
    workspaceSlug = "",
  } = useParams<{ jobId: string; orgSlug: string; workspaceSlug: string }>();
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const canManageBilling = workspace?.role === "ADMIN" || workspace?.role === "OWNER";
  const queryClient = useQueryClient();

  const capability = useCapabilityAccess(organizationId ?? "", CAPABILITY_KEY);

  const [page, setPage] = useState(1);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<Rect | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showBurned, setShowBurned] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const surfaceRef = useRef<HTMLDivElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);

  const jobQuery = useQuery({
    queryKey: redactionKeys.job(workspaceId ?? "", jobId),
    queryFn: () => getRedaction(workspaceId as string, jobId),
    enabled: Boolean(workspaceId && jobId && capability.granted),
    // DETECTING and APPLYING are worker states. Poll while one is running and
    // stop the moment it is not, rather than polling forever on a completed
    // job that will never change again.
    refetchInterval: (query) => {
      const status = (query.state.data as RedactionJob | undefined)?.status;
      return status === "DETECTING" || status === "APPLYING" ? 2_000 : false;
    },
  });

  const job = jobQuery.data;
  const pageCount = job?.page_count ?? 1;
  const regions = useMemo(() => job?.regions ?? [], [job]);
  const pageRegions = useMemo(
    () => regions.filter((r) => r.page_number === page),
    [regions, page],
  );
  const editable = job?.status === "REVIEW";

  // ARCH41-S1:bundle-on-click. The bundle carries presigned URLs that expire
  // after 15 minutes, and a cached query would hand out a dead link to a
  // reviewer who left the tab open. Mint them at the moment of the click.
  const downloadMutation = useMutation({
    mutationFn: async (which: "document" | "manifest") => {
      const bundle = await getBundle(workspaceId as string, jobId);
      const url = which === "document" ? bundle.document_url : bundle.manifest_url;
      if (!url) {
        throw new Error("The redacted file is not available yet.");
      }
      return url;
    },
    onSuccess: (url) => {
      window.location.assign(url);
    },
    onError: (caught: unknown) => report(caught),
  });

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({
      queryKey: redactionKeys.job(workspaceId ?? "", jobId),
    });
  }, [queryClient, workspaceId, jobId]);

  const report = useCallback((caught: unknown) => {
    // ApiError carries { code, message, details }. Reading `error.response`
    // would be reaching past the client's own contract into axios.
    setError(
      caught instanceof ApiError
        ? caught.message
        : "Something went wrong. Nothing was changed.",
    );
  }, []);

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleRegion(workspaceId as string, jobId, id, enabled),
    onSuccess: invalidate,
    onError: report,
  });

  const addMutation = useMutation({
    mutationFn: (rect: Rect) =>
      addRegion(workspaceId as string, jobId, {
        page_number: page,
        x0: rect.x0,
        y0: rect.y0,
        x1: rect.x1,
        y1: rect.y1,
      }),
    onSuccess: (created) => {
      setSelectedId(created.id);
      setDraft(null);
      invalidate();
    },
    onError: report,
  });

  const applyMutation = useMutation({
    mutationFn: () => applyRedaction(workspaceId as string, jobId),
    onSuccess: () => {
      setApplyOpen(false);
      invalidate();
    },
    onError: (caught) => {
      setApplyOpen(false);
      report(caught);
    },
  });

  // --- geometry -----------------------------------------------------------

  /**
   * Points -> CSS percentages of the rendered image.
   *
   * Percentages rather than pixels so the overlay tracks the image when the
   * container resizes, without a resize observer and without ever disagreeing
   * with the rendering by a scroll position.
   */
  const toCss = useCallback(
    (r: { x0: number; y0: number; x1: number; y1: number }, size: PageSize) => ({
      left: `${(r.x0 / size.widthPt) * 100}%`,
      width: `${((r.x1 - r.x0) / size.widthPt) * 100}%`,
      // y grows up in points and down in CSS.
      top: `${((size.heightPt - r.y1) / size.heightPt) * 100}%`,
      height: `${((r.y1 - r.y0) / size.heightPt) * 100}%`,
    }),
    [],
  );

  const [size, setSize] = useState<PageSize | null>(null);

  // ARCH41-S1:studio-authorized-preview.
  //
  // The burned preview for a page has the same URL before and after a region
  // is toggled, so `rev` carries a digest of what is being burned. It changes
  // exactly when the result would. The server ignores the parameter.
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const burnRevision = useMemo(
    () =>
      regionDigest(
        regions
          .filter((r) => r.page_number === page && r.enabled)
          .map((r) => `${r.id}:${r.x0},${r.y0},${r.x1},${r.y1}`)
          .sort()
          .join("|"),
      ),
    [regions, page],
  );
  const previewPath = useMemo(() => {
    if (!workspaceId || !jobId || !capability.granted || !jobQuery.data) {
      return null;
    }
    const base = pagePreviewUrl(workspaceId, jobId, page, {
      dpi: PREVIEW_DPI,
      burn: showBurned,
    });
    const revision = showBurned ? `&rev=${burnRevision}` : "";
    const attempt = previewAttempt > 0 ? `&attempt=${previewAttempt}` : "";
    return `${base}${revision}${attempt}`;
  }, [
    workspaceId,
    jobId,
    capability.granted,
    jobQuery.data,
    page,
    showBurned,
    burnRevision,
    previewAttempt,
  ]);
  const preview = useAuthorizedBlobUrl(previewPath);

  const toPoints = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } | null => {
      const node = surfaceRef.current;
      if (!node || !size) {
        return null;
      }
      const box = node.getBoundingClientRect();
      const fx = (clientX - box.left) / box.width;
      const fy = (clientY - box.top) / box.height;
      return {
        x: Math.max(0, Math.min(1, fx)) * size.widthPt,
        y: (1 - Math.max(0, Math.min(1, fy))) * size.heightPt,
      };
    },
    [size],
  );

  // --- drawing ------------------------------------------------------------

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!drawing || !editable) {
      return;
    }
    const point = toPoints(event.clientX, event.clientY);
    if (!point) {
      return;
    }
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    dragStart.current = point;
    setDraft({ x0: point.x, y0: point.y, x1: point.x, y1: point.y });
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStart.current) {
      return;
    }
    const point = toPoints(event.clientX, event.clientY);
    if (!point) {
      return;
    }
    const start = dragStart.current;
    setDraft({
      x0: Math.min(start.x, point.x),
      y0: Math.min(start.y, point.y),
      x1: Math.max(start.x, point.x),
      y1: Math.max(start.y, point.y),
    });
  };

  const onPointerUp = () => {
    const rect = draft;
    dragStart.current = null;
    // Below about four points square the drag was a click, not a rectangle.
    // Committing it would put an invisible region in the list that the
    // reviewer cannot find to delete.
    if (rect && rect.x1 - rect.x0 > 4 && rect.y1 - rect.y0 > 4) {
      addMutation.mutate(rect);
    } else {
      setDraft(null);
    }
  };

  /**
   * Keyboard editing. §3.6 requires the studio to be usable without a mouse.
   *
   * Arrows move the selected region, shift-arrows resize it from the
   * top-right corner, and holding no modifier moves by one point (a hair) or
   * ten with Alt (a visible nudge). A reviewer on a trackpad-less machine, or
   * anyone using a screen reader with the region list, can place a box
   * exactly without ever dragging.
   *
   * The commit is deliberately on key-up of a completed edit rather than per
   * keystroke: arrow-key repeat would otherwise fire one PATCH per frame.
   */
  const nudge = useCallback(
    (region: RedactionRegion, dx: number, dy: number, resize: boolean) => {
      const next = resize
        ? { x0: region.x0, y0: region.y0, x1: region.x1 + dx, y1: region.y1 + dy }
        : {
            x0: region.x0 + dx,
            y0: region.y0 + dy,
            x1: region.x1 + dx,
            y1: region.y1 + dy,
          };
      if (next.x1 <= next.x0 || next.y1 <= next.y0) {
        return;
      }
      // A nudge produces a NEW manual region rather than editing a detected
      // one in place: a detected region's box is evidence of what the
      // detector found, and silently moving it would make the audit trail
      // describe a rectangle nobody chose.
      addMutation.mutate(next);
    },
    [addMutation],
  );

  useEffect(() => {
    if (!editable) {
      return undefined;
    }
    const handler = (event: KeyboardEvent) => {
      if (!selectedId) {
        return;
      }
      const region = regions.find((r) => r.id === selectedId);
      if (!region) {
        return;
      }
      const step = event.altKey ? NUDGE_COARSE : NUDGE;
      const map: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, step],
        ArrowDown: [0, -step],
      };
      const delta = map[event.key];
      if (!delta) {
        return;
      }
      event.preventDefault();
      nudge(region, delta[0], delta[1], event.shiftKey);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [editable, selectedId, regions, nudge]);

  // --- render -------------------------------------------------------------

  if (capability.isLoading) {
    return <Centered><Loader2 className="h-5 w-5 animate-spin" aria-hidden /></Centered>;
  }
  if (!capability.granted) {
    return (
      <div className="mx-auto max-w-2xl space-y-3 p-6">
        <BackLink to={backTarget(orgSlug, workspaceSlug, null)} label="Back to documents" />
        <RedactionLockCard canChangePlan={Boolean(canManageBilling)} />
      </div>
    );
  }
  // HARDENING-T1:D26. A failed request rendered as a blank or permanent spinner.
  if (jobQuery.isError) {
    return (
      <div className="space-y-3 p-4">
        <BackLink to={backTarget(orgSlug, workspaceSlug, null)} label="Back to documents" />
      <ErrorState
        title="The redaction job could not be loaded"
        description={errorMessage(jobQuery.error, "The server did not return the redaction job. Check your connection and try again.")}
        onRetry={() => void jobQuery.refetch()}
      />
      </div>
    );
  }

  if (jobQuery.isLoading || !job || !workspaceId) {
    return <Centered><Loader2 className="h-5 w-5 animate-spin" aria-hidden /></Centered>;
  }

  const enabledCount = regions.filter((r) => r.enabled).length;
  const blockCount = regions.filter((r) => r.geometry_precision === "BLOCK").length;

  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-3">
          {/* ARCH41-S1:studio-back-link. The only way out used to be the
              browser's back button, which after a draw-and-apply session can
              land somewhere other than the document. */}
          <BackLink
            to={backTarget(orgSlug, workspaceSlug, job.work_item_id)}
            label="Back to document"
          />
          <h1 className={PAGE_TITLE}>Redact document</h1>
        </div>
        <p className={HINT}>
          {PROFILE_LABELS[job.profile_key] ?? job.profile_key} ·{" "}
          {job.render_dpi} DPI · started {formatTimestamp(job.created_at)}
        </p>
      </header>

      {error ? (
        <p role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {job.names_limit_applies ? (
        <p className={`${SURFACE_INSET} p-3 text-sm text-muted-foreground`}>
          This profile looks for personal names. Names are found only where the
          document&apos;s own extracted parties already name them — anything
          else is down to your review. Handwriting the scanner did not read
          cannot be found at all, only drawn over.
        </p>
      ) : null}

      {job.status === "DETECTING" ? (
        <p className={`${SURFACE_INSET} flex items-center gap-2 p-3 text-sm`}>
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Finding
          identifiers in this document…
        </p>
      ) : null}

      {job.status === "FAILED" ? (
        <p role="alert" className={`${SURFACE_INSET} flex items-start gap-2 p-3 text-sm`}>
          <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" aria-hidden />
          <span>{job.failure_reason}</span>
        </p>
      ) : null}

      {job.status === "COMPLETED" ? (
        <section className={`${SURFACE} space-y-2 p-4`}>
          <h2 className={SECTION_TITLE}>
            <CheckCircle2 className="mr-2 inline h-4 w-4 text-emerald-600" aria-hidden />
            Redaction complete
          </h2>
          {/* The sentence, not a JSON blob. §3.6. */}
          <p className="text-sm">{job.leak_check.sentence}</p>
          <p className={`${HINT} font-mono break-all`}>
            SHA-256 {job.output_sha256}
          </p>
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              className={BUTTON_PRIMARY}
              onClick={() => downloadMutation.mutate("document")}
              disabled={downloadMutation.isPending}
            >
              Download redacted PDF
            </button>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              onClick={() => downloadMutation.mutate("manifest")}
              disabled={downloadMutation.isPending}
            >
              Download manifest
            </button>
          </div>
        </section>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        {/* ---- page + overlay ---- */}
        <section className={`${SURFACE} space-y-3 p-3`}>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={BUTTON_GHOST}
              onClick={() => setPage((n) => Math.max(1, n - 1))}
              disabled={page <= 1}
            >
              Previous
            </button>
            <span className="text-sm">
              Page {page} of {pageCount}
            </span>
            <button
              type="button"
              className={BUTTON_GHOST}
              onClick={() => setPage((n) => Math.min(pageCount, n + 1))}
              disabled={page >= pageCount}
            >
              Next
            </button>
            <span className="flex-1" />
            <button
              type="button"
              className={showBurned ? BUTTON_PRIMARY : BUTTON_SECONDARY}
              onClick={() => setShowBurned((v) => !v)}
            >
              <Eye className="mr-1 inline h-4 w-4" aria-hidden />
              {showBurned ? "Showing result" : "Preview result"}
            </button>
            <button
              type="button"
              className={drawing ? BUTTON_PRIMARY : BUTTON_SECONDARY}
              onClick={() => setDrawing((v) => !v)}
              disabled={!editable}
            >
              <Square className="mr-1 inline h-4 w-4" aria-hidden />
              {drawing ? "Drawing" : "Draw a box"}
            </button>
          </div>

          <div
            ref={surfaceRef}
            className={`relative select-none ${drawing ? "cursor-crosshair" : ""}`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
          >
            {preview.url ? (
              <img
                // `burn` routes through the apply job's own rasterizer, so
                // what is on screen is what will be in the file. The bytes
                // come through the API client (ARCH41-S1): a bare src carried
                // neither the session nor the API origin.
                src={preview.url}
                alt={`Page ${page}`}
                className={`w-full rounded border border-border ${
                  preview.status === "loading" ? "opacity-60" : ""
                }`}
                onLoad={(event) => {
                  const img = event.currentTarget;
                  setSize({
                    widthPt: (img.naturalWidth / PREVIEW_DPI) * POINTS_PER_INCH,
                    heightPt: (img.naturalHeight / PREVIEW_DPI) * POINTS_PER_INCH,
                  });
                }}
              />
            ) : (
              <div
                className="flex aspect-[1/1.294] w-full items-center justify-center rounded border border-border"
                aria-busy={preview.status === "loading"}
              >
                {preview.status === "error" ? null : (
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
                )}
              </div>
            )}
            {preview.status === "loading" && preview.url ? (
              <span className="pointer-events-none absolute right-2 top-2">
                <Loader2 className="h-4 w-4 animate-spin" aria-label="Loading page" />
              </span>
            ) : null}

            {!showBurned && size
              ? pageRegions
                  .filter((r) => r.enabled)
                  .map((region) => (
                    <button
                      key={region.id}
                      type="button"
                      aria-label={`${DETECTOR_LABELS[region.detector] ?? region.detector} on page ${region.page_number}`}
                      aria-pressed={selectedId === region.id}
                      onClick={() => setSelectedId(region.id)}
                      className={`absolute border-2 ${
                        selectedId === region.id
                          ? "border-primary bg-primary/40"
                          : "border-foreground/70 bg-foreground/25"
                      }`}
                      style={toCss(region, size)}
                    />
                  ))
              : null}

            {draft && size ? (
              <div
                className="pointer-events-none absolute border-2 border-dashed border-primary bg-primary/20"
                style={toCss(draft, size)}
              />
            ) : null}
          </div>

          {preview.status === "error" ? (
            <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" aria-hidden />
              <span>{preview.error}</span>
              <button
                type="button"
                className={BUTTON_GHOST}
                onClick={() => setPreviewAttempt((n) => n + 1)}
              >
                Try again
              </button>
            </p>
          ) : null}

          {editable ? (
            <p className={HINT}>
              Select a region, then use the arrow keys to place a copy one
              point away — hold Alt for ten, Shift to resize instead of move.
              Drawing works without a mouse this way.
            </p>
          ) : null}
        </section>

        {/* ---- region list ---- */}
        <aside className={`${SURFACE} space-y-3 p-3`}>
          <h2 className={SECTION_TITLE}>Regions on this page</h2>
          <p className={HINT}>
            {regions.length} region{regions.length === 1 ? "" : "s"} ·{" "}
            {enabledCount} on
            {blockCount > 0
              ? ` · ${blockCount} widened to a whole line`
              : ""}
          </p>

          <ul className="space-y-1">
            {pageRegions.map((region) => (
              <li key={region.id}>
                <label
                  className={`flex cursor-pointer items-center gap-2 rounded p-1.5 text-sm ${
                    selectedId === region.id ? "bg-muted" : ""
                  }`}
                  onMouseEnter={() => setSelectedId(region.id)}
                >
                  <input
                    type="checkbox"
                    checked={region.enabled}
                    disabled={!editable || toggleMutation.isPending}
                    onChange={(event) =>
                      toggleMutation.mutate({
                        id: region.id,
                        enabled: event.target.checked,
                      })
                    }
                  />
                  <span className="flex-1">
                    {DETECTOR_LABELS[region.detector] ?? region.detector}
                  </span>
                  {region.checksum_validated ? (
                    <span
                      className="rounded bg-emerald-100 px-1 text-[10px] font-medium text-emerald-800"
                      title="The issuer's own checksum agrees"
                    >
                      checksum
                    </span>
                  ) : null}
                  <span
                    className="rounded border border-border px-1 font-mono text-[10px]"
                    title={precisionTitle(region.geometry_precision)}
                  >
                    {precisionBadge(region.geometry_precision)}
                  </span>
                </label>
              </li>
            ))}
            {pageRegions.length === 0 ? (
              <li className={HINT}>Nothing was found on this page.</li>
            ) : null}
          </ul>

          {editable ? (
            <button
              type="button"
              className={`${BUTTON_PRIMARY} w-full`}
              onClick={() => setApplyOpen(true)}
              disabled={enabledCount === 0}
            >
              Apply and download
            </button>
          ) : null}
          {editable && enabledCount === 0 ? (
            <p className={HINT}>
              Every region is switched off, so this would produce a document
              that looks redacted and is not.
            </p>
          ) : null}
        </aside>
      </div>

      {applyOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className={`${SURFACE_DIALOG} max-w-lg space-y-3 p-5`} role="dialog" aria-modal="true">
            <h2 className={SECTION_TITLE}>Apply {enabledCount} redactions?</h2>
            {/* §3.6 requires this to be stated plainly rather than implied. */}
            <p className="text-sm">
              Pages become images, and annotations, form fields, comments and
              metadata are removed. Search works through a new text layer made
              from the redacted pages.
            </p>
            <p className={HINT}>
              The result is checked for any surviving trace of what was
              redacted before it is offered for download. If that check fails,
              nothing is published.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" className={BUTTON_SECONDARY} onClick={() => setApplyOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className={BUTTON_PRIMARY}
                onClick={() => applyMutation.mutate()}
                disabled={applyMutation.isPending}
              >
                {applyMutation.isPending ? "Applying…" : "Apply and download"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

interface Rect {
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
}

interface PageSize {
  readonly widthPt: number;
  readonly heightPt: number;
}

/** ARCH41-S1:studio-back-link. */
const BackLink: React.FC<{ readonly to: string; readonly label: string }> = ({ to, label }) => (
  <Link to={to} className={`${BUTTON_GHOST} inline-flex items-center gap-1`}>
    <ArrowLeft className="h-4 w-4" aria-hidden />
    {label}
  </Link>
);

/**
 * The document the job was started from, or the document list when the job
 * itself could not be read. Falls back to the root only if the route carried
 * no slugs, which the tenant router never produces.
 */
const backTarget = (
  orgSlug: string,
  workspaceSlug: string,
  workItemId: string | null,
): string => {
  if (!orgSlug || !workspaceSlug) {
    return "/";
  }
  return workItemId
    ? workItemDetailsPath(orgSlug, workspaceSlug, workItemId)
    : workItemsPath(orgSlug, workspaceSlug);
};

/** FNV-1a, 32-bit, hex. A cache key, not a security boundary. */
const regionDigest = (text: string): string => {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
};

const Centered: React.FC<{ readonly children: React.ReactNode }> = ({ children }) => (
  <div className="flex h-64 items-center justify-center">{children}</div>
);

export default RedactionStudio;
