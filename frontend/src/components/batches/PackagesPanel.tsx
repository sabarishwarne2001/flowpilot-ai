/**
 * Phase 1 — export packages: request one, follow its build, download it, read its integrity
 * report (every file with its SHA-256). Polls while a package is being built.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Download, FileArchive, Loader2, PackagePlus, ScrollText, TimerOff } from "lucide-react";

import { batchKeys, downloadPackage, getPackageManifest, listPackages, requestPackage } from "@/services/api/batches";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import { formatBytesShort, Modal, Sha } from "@/components/batches/shared";
import type { ExportPackage, PackageStatus } from "@/types/batches";

const STATUS: Readonly<Record<PackageStatus, { label: string; tone: string }>> = {
  QUEUED: { label: "Queued", tone: "border-primary/25 bg-primary/[0.08] text-primary" },
  BUILDING: { label: "Building", tone: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300" },
  READY: { label: "Ready", tone: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300" },
  FAILED: { label: "Failed", tone: "border-destructive/35 bg-destructive/10 text-destructive" },
  EXPIRED: { label: "Expired", tone: "border-border bg-muted/60 text-muted-foreground" },
};

const ManifestDialog: React.FC<{ readonly workspaceId: string; readonly item: ExportPackage; readonly onClose: () => void }> = ({
  workspaceId,
  item,
  onClose,
}) => {
  const manifest = useQuery({
    queryKey: ["batches", workspaceId, "manifest", item.id],
    queryFn: () => getPackageManifest(workspaceId, item.id),
  });
  return (
    <Modal
      title="Integrity report"
      description={`${item.name} — every file in the package with its SHA-256. The same list is in the package as manifest.json and SHA256SUMS.`}
      onClose={onClose}
      wide
    >
      <dl className="mb-4 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Archive SHA-256</dt>
          <dd><Sha value={item.package_sha256} label="archive SHA-256" full /></dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Manifest SHA-256 (recorded)</dt>
          <dd><Sha value={item.manifest_sha256} label="manifest SHA-256" full /></dd>
        </div>
      </dl>
      {manifest.isLoading ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" />
      ) : manifest.isError ? (
        <p className="text-sm text-destructive">{errorMessage(manifest.error, "The report could not be loaded.")}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-left text-xs" data-testid="integrity-report">
            <thead className="sticky top-0 bg-muted/60 text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-2.5 py-2 font-semibold">File</th>
                <th className="px-2.5 py-2 text-right font-semibold">Size</th>
                <th className="px-2.5 py-2 font-semibold">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {(manifest.data?.files ?? []).map((file) => (
                <tr key={file.path} className="border-t border-border/60">
                  <td className="break-all px-2.5 py-1.5 font-mono">{file.path}</td>
                  <td className="fp-num whitespace-nowrap px-2.5 py-1.5 text-right">{formatBytesShort(file.bytes)}</td>
                  <td className="px-2.5 py-1.5"><Sha value={file.sha256} label="file SHA-256" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
};

const RequestDialog: React.FC<{
  readonly workspaceId: string;
  readonly batchId: string;
  readonly defaultName: string;
  readonly onClose: () => void;
}> = ({ workspaceId, batchId, defaultName, onClose }) => {
  const client = useQueryClient();
  const [name, setName] = useState(defaultName);
  const [originals, setOriginals] = useState(true);
  const create = useMutation({
    mutationFn: () => requestPackage(workspaceId, { name, batch_id: batchId, include_originals: originals }),
    onSuccess: async () => {
      toast.success("Export package requested. It is built in the background; you'll be notified when it is ready.");
      await client.invalidateQueries({ queryKey: batchKeys.all(workspaceId) });
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error, "The package could not be requested.")),
  });
  return (
    <Modal
      title="Export package"
      description="A zip of this batch's extracted data, with a SHA-256 for every file, that a compliance team can verify later."
      onClose={onClose}
      busy={create.isPending}
      footer={
        <>
          <button type="button" className="fp-btn fp-btn-secondary" onClick={onClose} disabled={create.isPending}>
            Cancel
          </button>
          <button
            type="button"
            className="fp-btn fp-btn-primary"
            disabled={create.isPending || !name.trim()}
            onClick={() => create.mutate()}
          >
            {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <PackagePlus className="h-4 w-4" aria-hidden />}
            Build package
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">Package name</span>
          <input className="fp-input" value={name} maxLength={160} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="flex items-start gap-3 rounded-lg border border-border p-3">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 accent-primary"
            checked={originals}
            onChange={(e) => setOriginals(e.target.checked)}
          />
          <span>
            <span className="block text-sm font-medium">Include the original files</span>
            <span className="block text-xs text-muted-foreground">
              Each original is checked against the SHA-256 recorded when it was uploaded, so the package proves it is
              the file you received. Leave this off for a data-only package.
            </span>
          </span>
        </label>
        <ul className="list-inside list-disc space-y-1 text-xs text-muted-foreground">
          <li>data/extractions.json and .csv — every document's fields, confidence and dispatch lane</li>
          <li>manifest.json and SHA256SUMS — every file's SHA-256 (check with sha256sum -c)</li>
          <li>README.txt — the manifest's SHA-256 and how to verify</li>
        </ul>
      </div>
    </Modal>
  );
};

export const PackagesPanel: React.FC<{
  readonly workspaceId: string;
  readonly batchId?: string;
  readonly batchName?: string;
  readonly canCreate: boolean;
  readonly canDownload: boolean;
}> = ({ workspaceId, batchId, batchName, canCreate, canDownload }) => {
  const [manifestFor, setManifestFor] = useState<ExportPackage | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const query = useQuery({
    queryKey: batchKeys.packages(workspaceId, batchId),
    queryFn: () => listPackages(workspaceId, batchId),
    refetchInterval: (q) =>
      q.state.data?.items.some((p) => p.status === "QUEUED" || p.status === "BUILDING") ? 2_000 : false,
  });
  const items = query.data?.items ?? [];

  const download = async (item: ExportPackage): Promise<void> => {
    setDownloading(item.id);
    try {
      await downloadPackage(workspaceId, item);
    } catch (error) {
      toast.error(errorMessage(error, "The download failed."));
    } finally {
      setDownloading(null);
    }
  };

  return (
    <section className="fp-card overflow-hidden" aria-labelledby="packages-title" data-testid="packages-panel">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 id="packages-title" className="text-[15px] font-semibold tracking-tight">Export packages</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Zips with a SHA-256 manifest. Ready packages can be downloaded for 7 days.
          </p>
        </div>
        {canCreate && batchId ? (
          <button type="button" className="fp-btn fp-btn-secondary h-8 text-xs" onClick={() => setRequesting(true)}>
            <PackagePlus className="h-3.5 w-3.5" aria-hidden /> New package
          </button>
        ) : null}
      </header>
      {query.isLoading ? (
        <div className="p-6"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" /></div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-6 py-8 text-center">
          <FileArchive className="h-6 w-6 text-muted-foreground" aria-hidden />
          <p className="text-sm font-medium">No export packages yet</p>
          <p className="max-w-sm text-xs text-muted-foreground">
            {batchId
              ? "Build one to hand this batch's data to an auditor or another system, with checksums they can verify."
              : "Open a batch and choose New package."}
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-border/70">
          {items.map((item) => {
            const status = STATUS[item.status];
            const busy = item.status === "QUEUED" || item.status === "BUILDING";
            return (
              <li key={item.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between" data-testid="package-row" data-status={item.status}>
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium" title={item.name}>{item.name}</span>
                    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ${status.tone}`}>
                      {busy ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : null}
                      {item.status === "EXPIRED" ? <TimerOff className="h-3 w-3" aria-hidden /> : null}
                      {status.label}
                    </span>
                  </div>
                  <p className="fp-num text-xs text-muted-foreground">
                    {item.document_count} documents
                    {item.status === "READY" ? ` · ${item.file_count} files · ${formatBytesShort(item.size_bytes)}` : ""}
                    {" · "}
                    {formatTimestamp(item.created_at)}
                    {item.status === "READY" && item.expires_at ? ` · until ${formatTimestamp(item.expires_at)}` : ""}
                  </p>
                  {item.manifest_sha256 ? (
                    <p className="text-xs text-muted-foreground">
                      Manifest <Sha value={item.manifest_sha256} label="manifest SHA-256" />
                    </p>
                  ) : null}
                  {item.status === "FAILED" ? (
                    <p className="flex items-start gap-1 text-xs text-destructive">
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                      {item.error_detail ?? item.error_code}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {item.manifest_sha256 ? (
                    <button type="button" className="fp-btn fp-btn-ghost h-8 text-xs" onClick={() => setManifestFor(item)}>
                      <ScrollText className="h-3.5 w-3.5" aria-hidden /> Integrity report
                    </button>
                  ) : null}
                  {canDownload && item.status === "READY" ? (
                    <button
                      type="button"
                      className="fp-btn fp-btn-primary h-8 text-xs"
                      disabled={downloading === item.id}
                      onClick={() => void download(item)}
                      aria-label={`Download ${item.name}`}
                    >
                      {downloading === item.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Download className="h-3.5 w-3.5" aria-hidden />}
                      Download
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {manifestFor ? <ManifestDialog workspaceId={workspaceId} item={manifestFor} onClose={() => setManifestFor(null)} /> : null}
      {requesting && batchId ? (
        <RequestDialog
          workspaceId={workspaceId}
          batchId={batchId}
          defaultName={`${batchName ?? "Batch"} export`}
          onClose={() => setRequesting(false)}
        />
      ) : null}
    </section>
  );
};

export default PackagesPanel;
