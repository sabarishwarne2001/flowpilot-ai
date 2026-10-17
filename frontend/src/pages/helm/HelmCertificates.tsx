import React from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, CircleCheck, CircleMinus, CircleX, Download, Lock, ShieldAlert, ShieldCheck, UserCheck } from "lucide-react";

import { LoadFailed } from "@/components/common/LoadFailed";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { CertificateSeal } from "@/components/helm/ui/CertificateSeal";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
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

/** One icon per check result, beside its word (never the icon alone). */
const CHECK_ICONS = { pass: CircleCheck, fail: CircleX, needs_approval: UserCheck, not_applicable: CircleMinus } as const;
const CHECK_TONES = { pass: "holding", fail: "breached", needs_approval: "drifting", not_applicable: "unknown" } as const;

export const HelmCertificates: React.FC = () => {
  const { organizationId, orgSlug, withBook } = useHelm();
  const book = useHelmBook();
  const list = useQuery({
    queryKey: helmKeys.certificates(organizationId, book.id),
    queryFn: () => listHelmCertificates(organizationId, book.id),
  });
  return (
    <div className="mx-auto max-w-6xl space-y-5" data-tour="certificates">
      <HelmPageHeader
        eyebrow={book.name}
        title="Certificates"
        description="Every action Helm proposes or runs gets a certificate: what it was, the facts it relied on, the mandate versions in force and each check the Guard made. Its SHA-256 fingerprint shows it was never changed."
      />
      {list.isError ? (
        <LoadFailed what="the certificates" onRetry={() => void list.refetch()} />
      ) : list.isLoading ? (
        <HelmSkeleton label="Loading…" />
      ) : (list.data ?? []).length === 0 ? (
        <p className="helm-panel p-4 text-sm">None yet. The first plan issues the first certificates.</p>
      ) : (
        <div className="helm-panel helm-enter max-h-[75vh] overflow-auto p-2">
          <table className="helm-table w-full min-w-[640px] text-sm">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Stage</th>
                <th scope="col">Result</th>
                <th scope="col">Issued</th>
                <th scope="col">Fingerprint</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((row) => (
                <tr key={row.id} data-testid="helm-certificate-row">
                  <td>
                    <Link to={withBook(helmCertificatePath(orgSlug, row.id))} className="font-medium hover:underline">
                      {row.readable ? row.title : "An action in a workspace you cannot read"}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap text-xs">{STAGE_WORDS[row.stage]}</td>
                  <td>
                    <span className={`helm-chip px-2 text-xs ${row.passed ? "helm-state-holding" : "helm-state-breached"}`}>
                      {row.passed ? <ShieldCheck className="h-3 w-3" aria-hidden="true" /> : <ShieldAlert className="h-3 w-3" aria-hidden="true" />}
                      {row.passed ? "Passed" : "Refused"}
                    </span>
                  </td>
                  <td className="whitespace-nowrap text-xs">{formatTimestamp(row.created_at)}</td>
                  <td className="helm-id text-xs">{row.sha256.slice(0, 12)}…</td>
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
    return <HelmSkeleton label="Loading…" />;
  }
  const cert = certificate.data;
  const body = cert.body;
  const action = (body.action ?? {}) as Record<string, unknown>;
  const checks = body.checks ?? [];
  const mandates = body.mandates ?? [];

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <Link to={withBook(helmPath(orgSlug, "certificates"))} className="helm-accent inline-flex items-center gap-1 text-xs hover:underline">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        All certificates
      </Link>
      <div className="helm-panel helm-panel-strong helm-enter helm-certificate relative overflow-hidden p-6 sm:p-8">
        <div className="pointer-events-none absolute right-6 top-6 hidden sm:block">
          <CertificateSeal fingerprint={cert.sha256} passed={cert.passed} verified={cert.verified} size={140} />
        </div>
        <div className="sm:hidden">
          <CertificateSeal fingerprint={cert.sha256} passed={cert.passed} verified={cert.verified} size={96} />
        </div>
        <p className="helm-label mt-3 sm:mt-0">Certificate · {STAGE_WORDS[cert.stage]}</p>
        <h1 className="mt-2 text-2xl sm:pr-40 font-semibold leading-snug tracking-tight">
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
          <dd className="helm-id break-all text-xs">
            {cert.sha256}{" "}
            <span className={cert.verified ? "helm-state-holding" : "helm-state-breached"}>
              {cert.verified ? "(matches: unchanged since it was issued)" : "(does not match: altered)"}
            </span>
          </dd>
          {body.engine_version ? (
            <>
              <dt className="helm-muted">Engine</dt>
              <dd className="helm-id text-xs">{body.engine_version}</dd>
            </>
          ) : null}
          {body.snapshot ? (
            <>
              <dt className="helm-muted">Facts as of</dt>
              <dd className="text-xs">
                {body.snapshot.as_of} · inputs <span className="helm-id">{body.snapshot.digest.slice(0, 16)}…</span>
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

      <section aria-labelledby="cert-checks" className="helm-panel helm-enter p-5" style={{ ["--i" as string]: 2 }}>
        <h2 id="cert-checks" className="helm-label">
          What the Guard checked
        </h2>
        <ul className="mt-3 helm-divide text-sm">
          {checks.map((check) => {
            const Icon = CHECK_ICONS[check.result] ?? CircleMinus;
            const tone = CHECK_TONES[check.result] ?? "unknown";
            return (
            <li key={check.id} className="flex flex-col gap-1 py-2.5 sm:flex-row sm:gap-3" data-result={check.result}>
              <span className={`inline-flex w-40 shrink-0 items-center gap-1.5 font-semibold helm-state-${tone}`}>
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                {CHECK_WORDS[check.result] ?? check.result}
              </span>
              <span>
                <span className="font-medium">{check.title}.</span> {check.detail}
              </span>
            </li>
            );
          })}
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
                <span className="helm-muted helm-id text-xs">{mandate.spec_hash.slice(0, 12)}…</span>
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
