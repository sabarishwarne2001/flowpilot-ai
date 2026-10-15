import React from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Download, Lock, ShieldAlert, ShieldCheck } from "lucide-react";

import { LoadFailed } from "@/components/common/LoadFailed";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { helmCertificatePath, helmPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { downloadHelmCertificate, getHelmCertificate, helmKeys, listHelmCertificates } from "@/services/api/helm";
import { formatTimestamp } from "@/utils/displayTime";

const STAGE_WORDS = { PLAN: "When planned", EXECUTION: "Just before it ran" } as const;

const CHECK_WORDS: Record<string, string> = {
  pass: "Passed",
  fail: "Failed",
  needs_approval: "Needs approval",
  not_applicable: "Not applicable",
};

export const HelmCertificates: React.FC = () => {
  const { organizationId, orgSlug, withBook } = useHelm();
  const book = useHelmBook();
  const list = useQuery({
    queryKey: helmKeys.certificates(organizationId, book.id),
    queryFn: () => listHelmCertificates(organizationId, book.id),
  });
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Certificates</h1>
        <p className="helm-muted mt-1 text-sm">
          Every action Helm proposes or runs gets a certificate: what it was, the facts it relied on, the mandate
          versions in force and each check the Guard made. Its SHA-256 fingerprint shows it was never changed.
        </p>
      </div>
      {list.isError ? (
        <LoadFailed what="the certificates" onRetry={() => void list.refetch()} />
      ) : list.isLoading ? (
        <p className="helm-muted text-sm" role="status">
          Loading…
        </p>
      ) : (list.data ?? []).length === 0 ? (
        <p className="helm-panel p-4 text-sm">None yet. The first plan issues the first certificates.</p>
      ) : (
        <div className="helm-panel overflow-x-auto p-4">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="helm-label text-left">
                <th scope="col" className="py-1 pr-3">Action</th>
                <th scope="col" className="py-1 pr-3">Stage</th>
                <th scope="col" className="py-1 pr-3">Result</th>
                <th scope="col" className="py-1 pr-3">Issued</th>
                <th scope="col" className="py-1">Fingerprint</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((row) => (
                <tr key={row.id} className="helm-hairline border-t" data-testid="helm-certificate-row">
                  <td className="py-2 pr-3">
                    <Link to={withBook(helmCertificatePath(orgSlug, row.id))} className="helm-accent font-medium hover:underline">
                      {row.readable ? row.title : "An action in a workspace you cannot read"}
                    </Link>
                  </td>
                  <td className="py-2 pr-3 text-xs">{STAGE_WORDS[row.stage]}</td>
                  <td className="py-2 pr-3 text-xs">{row.passed ? "Passed" : "Refused"}</td>
                  <td className="py-2 pr-3 text-xs">{formatTimestamp(row.created_at)}</td>
                  <td className="helm-figure py-2 text-xs">{row.sha256.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs">
        <Link to={withBook(helmPath(orgSlug))} className="helm-accent hover:underline">
          Back to the cockpit
        </Link>
      </p>
    </div>
  );
};

export const HelmCertificate: React.FC = () => {
  const { certificateId = "" } = useParams<{ certificateId: string }>();
  const { organizationId, orgSlug, withBook } = useHelm();
  const certificate = useQuery({
    queryKey: helmKeys.certificate(organizationId, certificateId),
    queryFn: () => getHelmCertificate(organizationId, certificateId),
    enabled: Boolean(certificateId),
  });
  const download = useMutation({
    mutationFn: () => downloadHelmCertificate(organizationId, certificateId),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `helm-certificate-${certificateId}.json`;
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not download the certificate.")),
  });

  if (certificate.isError) {
    return <LoadFailed what="the certificate" onRetry={() => void certificate.refetch()} />;
  }
  if (!certificate.data) {
    return (
      <p className="helm-muted text-sm" role="status">
        Loading…
      </p>
    );
  }
  const cert = certificate.data;
  const body = cert.body;
  const action = (body.action ?? {}) as Record<string, unknown>;
  const checks = body.checks ?? [];
  const mandates = body.mandates ?? [];

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <Link to={withBook(helmPath(orgSlug, "certificates"))} className="helm-accent inline-flex items-center gap-1 text-xs hover:underline">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        All certificates
      </Link>
      <div className="helm-panel p-5">
        <p className="helm-label">Certificate · {STAGE_WORDS[cert.stage]}</p>
        <h1 className="mt-1 text-lg font-semibold">
          {cert.readable ? String(action.title ?? "An action") : "An action in a workspace you cannot read"}
        </h1>
        <p className={`mt-2 inline-flex items-center gap-1.5 text-sm font-semibold ${cert.passed ? "helm-state-holding" : "helm-state-breached"}`}>
          {cert.passed ? <ShieldCheck className="h-4 w-4" aria-hidden="true" /> : <ShieldAlert className="h-4 w-4" aria-hidden="true" />}
          {cert.passed ? "The Guard passed it" : "The Guard refused it"}
        </p>
        <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="helm-muted">Issued</dt>
          <dd>{formatTimestamp(cert.created_at)}</dd>
          <dt className="helm-muted">Fingerprint</dt>
          <dd className="helm-figure break-all text-xs">
            {cert.sha256}{" "}
            <span className={cert.verified ? "helm-state-holding" : "helm-state-breached"}>
              {cert.verified ? "(matches: unchanged since it was issued)" : "(does not match: altered)"}
            </span>
          </dd>
          {body.engine_version ? (
            <>
              <dt className="helm-muted">Engine</dt>
              <dd className="helm-figure text-xs">{body.engine_version}</dd>
            </>
          ) : null}
          {body.snapshot ? (
            <>
              <dt className="helm-muted">Facts as of</dt>
              <dd className="text-xs">
                {body.snapshot.as_of} · inputs <span className="helm-figure">{body.snapshot.digest.slice(0, 16)}…</span>
              </dd>
            </>
          ) : null}
          {cert.workspace_name ? (
            <>
              <dt className="helm-muted">Workspace</dt>
              <dd>{cert.workspace_name}</dd>
            </>
          ) : null}
        </dl>
        {!cert.readable ? (
          <p className="helm-muted mt-4 inline-flex items-center gap-1 text-xs">
            <Lock className="h-3 w-3" aria-hidden="true" />
            The facts are hidden because you cannot read {cert.workspace_name ?? "that workspace"}. Ask a workspace admin for access.
          </p>
        ) : null}
      </div>

      <section aria-labelledby="cert-checks" className="helm-panel p-5">
        <h2 id="cert-checks" className="helm-label">
          What the Guard checked
        </h2>
        <ul className="mt-2 space-y-2 text-sm">
          {checks.map((check) => (
            <li key={check.id} className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
              <span className={`w-32 shrink-0 font-semibold ${check.result === "fail" ? "helm-state-breached" : ""}`}>
                {CHECK_WORDS[check.result] ?? check.result}
              </span>
              <span>
                <span className="font-medium">{check.title}.</span> {check.detail}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {mandates.length > 0 ? (
        <section aria-labelledby="cert-mandates" className="helm-panel p-5">
          <h2 id="cert-mandates" className="helm-label">
            Mandates in force
          </h2>
          <ul className="mt-2 space-y-1 text-sm">
            {mandates.map((mandate) => (
              <li key={`${mandate.mandate_id}-${mandate.version}`}>
                {mandate.template.replace(/_/g, " ")}, version {mandate.version}{" "}
                <span className="helm-muted helm-figure text-xs">{mandate.spec_hash.slice(0, 12)}…</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {cert.readable ? (
        <button type="button" className="helm-btn" onClick={() => download.mutate()} disabled={download.isPending}>
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
          Download the JSON
        </button>
      ) : null}
    </div>
  );
};

export default HelmCertificates;
