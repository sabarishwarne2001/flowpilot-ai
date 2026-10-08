/**
 * Phase 1 — check an export package someone holds: every file against SHA256SUMS and the
 * manifest, and the manifest against what this workspace recorded when it built the package.
 */
import React, { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { FileArchive, Loader2, ShieldAlert, ShieldCheck, ShieldQuestion, ShieldX, UploadCloud } from "lucide-react";

import { verifyPackage } from "@/services/api/batches";
import { errorMessage } from "@/services/api/errors";
import { formatBytesShort, Modal, Sha } from "@/components/batches/shared";
import type { Verdict, VerifyResult } from "@/types/batches";

const VERDICT: Readonly<Record<Verdict, { label: string; tone: string; Icon: React.ElementType }>> = {
  VERIFIED: {
    label: "Verified",
    tone: "border-emerald-500/30 bg-emerald-500/[0.07] text-emerald-800 dark:text-emerald-300",
    Icon: ShieldCheck,
  },
  TAMPERED: { label: "Tampered", tone: "border-destructive/35 bg-destructive/[0.07] text-destructive", Icon: ShieldX },
  UNRECOGNISED: {
    label: "Not issued here",
    tone: "border-amber-500/35 bg-amber-500/[0.07] text-amber-800 dark:text-amber-300",
    Icon: ShieldQuestion,
  },
  INVALID: { label: "Not a package", tone: "border-border bg-muted/50 text-muted-foreground", Icon: ShieldAlert },
};

const Outcome: React.FC<{ readonly result: VerifyResult }> = ({ result }) => {
  const { label, tone, Icon } = VERDICT[result.verdict];
  return (
    <section className="space-y-3" aria-live="polite" data-testid="verify-result" data-verdict={result.verdict}>
      <div className={`flex items-start gap-3 rounded-xl border p-3 ${tone}`}>
        <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-semibold">{label}</p>
          <p className="mt-0.5 text-xs leading-relaxed">{result.message}</p>
        </div>
      </div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
        {result.package_name ? (
          <div>
            <dt className="text-muted-foreground">Package</dt>
            <dd className="font-medium">{result.package_name}</dd>
          </div>
        ) : null}
        <div>
          <dt className="text-muted-foreground">Files checked</dt>
          <dd className="fp-num font-medium">
            {result.files_ok} of {result.files_checked} match
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-muted-foreground">Manifest SHA-256 (this file)</dt>
          <dd><Sha value={result.manifest_sha256} label="manifest SHA-256" full /></dd>
        </div>
        {result.recorded_manifest_sha256 ? (
          <div className="sm:col-span-2">
            <dt className="text-muted-foreground">Manifest SHA-256 (recorded when built)</dt>
            <dd><Sha value={result.recorded_manifest_sha256} label="recorded SHA-256" full /></dd>
          </div>
        ) : null}
        <div className="sm:col-span-2">
          <dt className="text-muted-foreground">Archive SHA-256</dt>
          <dd>
            <Sha value={result.archive_sha256} label="archive SHA-256" full />
            {result.archive_matches_record === true ? (
              <span className="ml-1 text-emerald-700 dark:text-emerald-300">· same archive as issued</span>
            ) : result.archive_matches_record === false ? (
              <span className="ml-1 text-muted-foreground">· re-zipped (the files are what count)</span>
            ) : null}
          </dd>
        </div>
      </dl>
      {result.problems.length > 0 ? (
        <div className="overflow-hidden rounded-lg border border-destructive/30">
          <table className="w-full text-left text-xs">
            <thead className="bg-destructive/[0.06] text-destructive">
              <tr>
                <th className="px-2 py-1.5 font-semibold">File</th>
                <th className="px-2 py-1.5 font-semibold">Problem</th>
              </tr>
            </thead>
            <tbody>
              {result.problems.map((problem) => (
                <tr key={`${problem.path}:${problem.status}`} className="border-t border-border/60">
                  <td className="break-all px-2 py-1.5 font-mono">{problem.path}</td>
                  <td className="px-2 py-1.5">
                    {problem.status === "MODIFIED"
                      ? "Changed since it was issued"
                      : problem.status === "MISSING"
                        ? "Missing from the package"
                        : "Not part of the package"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
};

export const VerifyPackageDialog: React.FC<{ readonly workspaceId: string; readonly onClose: () => void }> = ({
  workspaceId,
  onClose,
}) => {
  const input = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const verify = useMutation({ mutationFn: (f: File) => verifyPackage(workspaceId, f) });

  const choose = (picked: File | undefined | null): void => {
    if (!picked) {
      return;
    }
    setFile(picked);
    verify.reset();
    verify.mutate(picked);
  };

  return (
    <Modal
      title="Verify an export package"
      description="Upload a package exactly as you received it. Every file is checked against its SHA-256, and the manifest against the one this workspace issued. The file is read and discarded, never stored."
      onClose={onClose}
      busy={verify.isPending}
      wide
    >
      <div className="space-y-4">
        <div
          role="button"
          tabIndex={0}
          aria-label="Choose a package to verify"
          onClick={() => input.current?.click()}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              input.current?.click();
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            choose(event.dataTransfer.files[0]);
          }}
          className={`flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-7 text-center transition-colors ${
            dragging ? "border-primary bg-primary/[0.06]" : "border-border-strong/80 hover:border-primary/50 hover:bg-primary/[0.03]"
          }`}
        >
          <input
            ref={input}
            type="file"
            accept=".zip,application/zip"
            hidden
            aria-label="Package file"
            onChange={(event) => {
              choose(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          {verify.isPending ? (
            <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden />
          ) : file ? (
            <FileArchive className="h-6 w-6 text-primary" aria-hidden />
          ) : (
            <UploadCloud className="h-6 w-6 text-muted-foreground" aria-hidden />
          )}
          <p className="text-sm font-medium">
            {file ? file.name : "Drop a package (.zip) here, or click to choose one"}
          </p>
          <p className="text-xs text-muted-foreground">
            {file ? `${formatBytesShort(file.size)}${verify.isPending ? " · checking every file…" : ""}` : "Nothing leaves your workspace."}
          </p>
        </div>
        {verify.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(verify.error, "The package could not be checked.")}
          </p>
        ) : null}
        {verify.data ? <Outcome result={verify.data} /> : null}
      </div>
    </Modal>
  );
};

export default VerifyPackageDialog;
