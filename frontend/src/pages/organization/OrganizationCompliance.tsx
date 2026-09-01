import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Download,
  Globe,
  Loader2,
  ShieldAlert,
  Timer,
  UserX,
} from "lucide-react";

import {
  createComplianceExport,
  createErasure,
  getComplianceExportDownloadUrl,
  getComplianceOverview,
  listComplianceExports,
  listErasures,
  updateDataResidency,
  updateRetentionPolicy,
} from "@/services/api/compliance";
import { complianceKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  formatBytes,
  isDownloadable,
  shortHash,
  totalDestroyed,
  REGION_DESCRIPTIONS,
  REGION_LABELS,
  type ComplianceExport,
  type DataResidencyRegion,
  type ErasedSubject,
  type RetentionPolicy,
} from "@/types/compliance";

const CARD =
  "rounded-lg border border-border bg-card p-5 text-card-foreground shadow-sm";
const LABEL = "text-sm font-medium text-foreground";
const HINT = "text-xs text-muted-foreground";
const INPUT =
  "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

const formatDate = (value: string | null): string =>
  value ? new Date(value).toLocaleString() : "—";

const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    return first?.msg ?? "The request was rejected.";
  }
  return "Something went wrong. Please try again.";
};

// ---------------------------------------------------------------------------
// Residency
// ---------------------------------------------------------------------------

const ResidencyCard: React.FC<{
  organizationId: string;
  region: DataResidencyRegion;
  options: readonly { region: DataResidencyRegion; configured: boolean }[];
  canWrite: boolean;
  onChanged: () => void;
}> = ({ organizationId, region, options, canWrite, onChanged }) => {
  const [selected, setSelected] = useState<DataResidencyRegion>(region);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      updateDataResidency(organizationId, {
        region: selected,
        acknowledge_no_migration: acknowledged,
      }),
    onSuccess: () => {
      setError(null);
      setAcknowledged(false);
      onChanged();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const chosen = options.find((option) => option.region === selected);
  const unavailable = chosen ? !chosen.configured : false;
  const dirty = selected !== region;

  return (
    <section className={CARD}>
      <header className="flex items-start gap-3">
        <Globe className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden />
        <div>
          <h2 className="text-base font-semibold">Data residency</h2>
          <p className={HINT}>
            Where this tenant&rsquo;s documents, avatars and export archives are
            physically stored.
          </p>
        </div>
      </header>

      <div className="mt-4 space-y-3">
        <div>
          <label className={LABEL} htmlFor="residency-region">
            Region
          </label>
          <select
            id="residency-region"
            className={INPUT}
            value={selected}
            disabled={!canWrite || mutation.isPending}
            onChange={(event) =>
              setSelected(event.target.value as DataResidencyRegion)
            }
          >
            {options.map((option) => (
              <option key={option.region} value={option.region}>
                {REGION_LABELS[option.region]}
                {option.configured ? "" : " — not provisioned"}
              </option>
            ))}
          </select>
          <p className={`${HINT} mt-1`}>{REGION_DESCRIPTIONS[selected]}</p>
        </div>

        {unavailable ? (
          <p className="flex items-start gap-2 rounded-md border border-border bg-muted p-3 text-xs">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <span>
              No bucket is provisioned for {REGION_LABELS[selected]} on this
              deployment. Selecting it would pin your data to storage that does
              not exist, so the change is refused rather than accepted quietly.
            </span>
          </p>
        ) : null}

        {dirty && canWrite ? (
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            <span>
              I understand that repinning applies to new objects only. Files
              already written stay in the bucket they were written to; this is a
              forward-looking policy change, not a migration.
            </span>
          </label>
        ) : null}

        {error ? (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        {canWrite ? (
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
            disabled={
              !dirty || !acknowledged || unavailable || mutation.isPending
            }
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : null}
            Save region
          </button>
        ) : (
          <p className={HINT}>Only an organization owner can change this.</p>
        )}
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Retention
// ---------------------------------------------------------------------------

const RetentionCard: React.FC<{
  organizationId: string;
  policy: RetentionPolicy;
  canWrite: boolean;
  onChanged: () => void;
}> = ({ organizationId, policy, canWrite, onChanged }) => {
  const [workItems, setWorkItems] = useState<string>(
    policy.work_item_retention_days?.toString() ?? "",
  );
  const [conversations, setConversations] = useState<string>(
    policy.conversation_retention_days?.toString() ?? "",
  );
  const [audit, setAudit] = useState<string>(
    policy.audit_retention_days?.toString() ?? "",
  );
  const [autoPurge, setAutoPurge] = useState(policy.auto_purge_enabled);
  const [error, setError] = useState<string | null>(null);

  const parse = (value: string): number | null => {
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const mutation = useMutation({
    mutationFn: () =>
      updateRetentionPolicy(organizationId, {
        work_item_retention_days: parse(workItems),
        conversation_retention_days: parse(conversations),
        audit_retention_days: parse(audit),
        auto_purge_enabled: autoPurge,
      }),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <section className={CARD}>
      <header className="flex items-start gap-3">
        <Timer className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden />
        <div>
          <h2 className="text-base font-semibold">Retention</h2>
          <p className={HINT}>
            Leave a field blank to keep that data indefinitely.
          </p>
        </div>
      </header>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <label className={LABEL} htmlFor="retention-work-items">
            Documents (days)
          </label>
          <input
            id="retention-work-items"
            className={INPUT}
            inputMode="numeric"
            placeholder="Forever"
            value={workItems}
            disabled={!canWrite}
            onChange={(event) => setWorkItems(event.target.value)}
          />
          <p className={`${HINT} mt-1`}>Minimum 30.</p>
        </div>
        <div>
          <label className={LABEL} htmlFor="retention-conversations">
            Conversations (days)
          </label>
          <input
            id="retention-conversations"
            className={INPUT}
            inputMode="numeric"
            placeholder="Forever"
            value={conversations}
            disabled={!canWrite}
            onChange={(event) => setConversations(event.target.value)}
          />
          <p className={`${HINT} mt-1`}>Minimum 30.</p>
        </div>
        <div>
          <label className={LABEL} htmlFor="retention-audit">
            Audit log (days)
          </label>
          <input
            id="retention-audit"
            className={INPUT}
            inputMode="numeric"
            placeholder="Forever"
            value={audit}
            disabled={!canWrite}
            onChange={(event) => setAudit(event.target.value)}
          />
          <p className={`${HINT} mt-1`}>
            Minimum {policy.audit_retention_floor_days}. The audit log is
            append-only and the database refuses deletions below that age, so a
            lower value cannot be honoured.
          </p>
        </div>
      </div>

      <label className="mt-4 flex items-start gap-2 text-xs">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={autoPurge}
          disabled={!canWrite}
          onChange={(event) => setAutoPurge(event.target.checked)}
        />
        <span>
          Enable automatic purging. Off by default: documents and conversations
          have no recycle bin, so a purge deletes live records permanently on a
          schedule. The retention sweeper only acts on tenants that opt in.
        </span>
      </label>

      {error ? (
        <p className="mt-3 text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      {canWrite ? (
        <button
          type="button"
          className="mt-4 inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : null}
          Save retention policy
        </button>
      ) : (
        <p className={`${HINT} mt-3`}>
          Only an organization owner can change this.
        </p>
      )}
    </section>
  );
};

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

const ExportsCard: React.FC<{
  organizationId: string;
  records: readonly ComplianceExport[];
  isLoading: boolean;
  onChanged: () => void;
}> = ({ organizationId, records, isLoading, onChanged }) => {
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () => createComplianceExport(organizationId),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const download = async (record: ComplianceExport): Promise<void> => {
    setDownloadingId(record.id);
    setError(null);
    try {
      const result = await getComplianceExportDownloadUrl(
        organizationId,
        record.id,
      );
      window.open(result.download_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <section className={CARD}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Archive
            className="mt-0.5 h-5 w-5 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h2 className="text-base font-semibold">DPA export bundles</h2>
            <p className={HINT}>
              A ZIP archive of this tenant&rsquo;s members, workspaces, document
              register, conversations, audit trail and erasure history. Written
              to your residency region and expiring automatically.
            </p>
          </div>
        </div>
        <button
          type="button"
          className="inline-flex shrink-0 items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
          disabled={generate.isPending}
          onClick={() => generate.mutate()}
        >
          {generate.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : null}
          Generate bundle
        </button>
      </header>

      {error ? (
        <p className="mt-3 text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-muted-foreground">
            <tr>
              <th className="py-2 pr-4 font-medium">Requested</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 pr-4 font-medium">Region</th>
              <th className="py-2 pr-4 font-medium">Size</th>
              <th className="py-2 pr-4 font-medium">Expires</th>
              <th className="py-2 font-medium">
                <span className="sr-only">Download</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} className="py-4 text-muted-foreground">
                  Loading&hellip;
                </td>
              </tr>
            ) : records.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-4 text-muted-foreground">
                  No bundles yet.
                </td>
              </tr>
            ) : (
              records.map((record) => (
                <tr key={record.id} className="border-t border-border">
                  <td className="py-2 pr-4">{formatDate(record.created_at)}</td>
                  <td className="py-2 pr-4">
                    <span className="rounded border border-border px-1.5 py-0.5 text-[11px]">
                      {record.status}
                    </span>
                    {record.error_message ? (
                      <span className="ml-2 text-xs text-destructive">
                        {record.error_message}
                      </span>
                    ) : null}
                  </td>
                  <td className="py-2 pr-4">{record.residency_region}</td>
                  <td className="py-2 pr-4">
                    {formatBytes(record.file_size_bytes)}
                  </td>
                  <td className="py-2 pr-4">{formatDate(record.expires_at)}</td>
                  <td className="py-2">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs disabled:opacity-40"
                      disabled={
                        !isDownloadable(record) || downloadingId === record.id
                      }
                      onClick={() => void download(record)}
                    >
                      {downloadingId === record.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                      ) : (
                        <Download className="h-3 w-3" aria-hidden />
                      )}
                      Download
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Erasure
// ---------------------------------------------------------------------------

const ErasureModal: React.FC<{
  organizationId: string;
  onClose: () => void;
  onErased: () => void;
}> = ({ organizationId, onClose, onErased }) => {
  const [subjectUserId, setSubjectUserId] = useState("");
  const [ticket, setTicket] = useState("");
  const [confirmEmail, setConfirmEmail] = useState("");
  const [typedPhrase, setTypedPhrase] = useState("");
  const [error, setError] = useState<string | null>(null);

  const PHRASE = "ERASE";

  const mutation = useMutation({
    mutationFn: () =>
      createErasure(organizationId, {
        subject_user_id: subjectUserId.trim(),
        erasure_ticket: ticket.trim(),
        confirm_subject_email: confirmEmail.trim(),
      }),
    onSuccess: () => {
      setError(null);
      onErased();
      onClose();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const ready =
    subjectUserId.trim().length > 0 &&
    ticket.trim().length > 0 &&
    confirmEmail.trim().length > 0 &&
    typedPhrase === PHRASE;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="erasure-title"
    >
      <div className="w-full max-w-lg rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="flex items-start gap-3">
          <ShieldAlert
            className="mt-0.5 h-5 w-5 text-destructive"
            aria-hidden
          />
          <div>
            <h3 id="erasure-title" className="text-base font-semibold">
              Erase a data subject
            </h3>
            <p className={HINT}>
              This is irreversible. Documents, retrieval chunks and
              conversations belonging to this person are destroyed, their
              account is anonymised, and their sessions are revoked. Invoices
              and usage records are kept — statutory retention outranks erasure.
            </p>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          <div>
            <label className={LABEL} htmlFor="erasure-subject">
              Subject user ID
            </label>
            <input
              id="erasure-subject"
              className={INPUT}
              value={subjectUserId}
              onChange={(event) => setSubjectUserId(event.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
            />
          </div>
          <div>
            <label className={LABEL} htmlFor="erasure-email">
              Confirm their email address
            </label>
            <input
              id="erasure-email"
              className={INPUT}
              value={confirmEmail}
              onChange={(event) => setConfirmEmail(event.target.value)}
              placeholder="person@example.com"
            />
            <p className={`${HINT} mt-1`}>
              Checked against the account before anything is destroyed. A
              mistyped user ID is otherwise indistinguishable from a correct one.
            </p>
          </div>
          <div>
            <label className={LABEL} htmlFor="erasure-ticket">
              Erasure ticket reference
            </label>
            <input
              id="erasure-ticket"
              className={INPUT}
              value={ticket}
              onChange={(event) => setTicket(event.target.value)}
              placeholder="DSAR-2026-014"
            />
          </div>
          <div>
            <label className={LABEL} htmlFor="erasure-phrase">
              Type {PHRASE} to confirm
            </label>
            <input
              id="erasure-phrase"
              className={INPUT}
              value={typedPhrase}
              onChange={(event) => setTypedPhrase(event.target.value)}
            />
          </div>
        </div>

        {error ? (
          <p className="mt-3 text-xs text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
            onClick={onClose}
            disabled={mutation.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-destructive px-3 py-1.5 text-sm font-medium text-destructive-foreground disabled:opacity-50"
            disabled={!ready || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : null}
            Erase permanently
          </button>
        </div>
      </div>
    </div>
  );
};

const ErasureCard: React.FC<{
  organizationId: string;
  records: readonly ErasedSubject[];
  isLoading: boolean;
  canWrite: boolean;
  onChanged: () => void;
}> = ({ organizationId, records, isLoading, canWrite, onChanged }) => {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <section className={CARD}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <UserX className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden />
          <div>
            <h2 className="text-base font-semibold">Subject erasures</h2>
            <p className={HINT}>
              Every erasure is recorded here permanently. The subject&rsquo;s
              address is stored only as a hash, so a repeat request is
              recognised without holding the data that was destroyed.
            </p>
          </div>
        </div>
        {canWrite ? (
          <button
            type="button"
            className="shrink-0 rounded-md border border-destructive px-3 py-1.5 text-sm font-medium text-destructive"
            onClick={() => setModalOpen(true)}
          >
            Erase a subject
          </button>
        ) : null}
      </header>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-muted-foreground">
            <tr>
              <th className="py-2 pr-4 font-medium">Erased</th>
              <th className="py-2 pr-4 font-medium">Ticket</th>
              <th className="py-2 pr-4 font-medium">Subject hash</th>
              <th className="py-2 font-medium">Records destroyed</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={4} className="py-4 text-muted-foreground">
                  Loading&hellip;
                </td>
              </tr>
            ) : records.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-4 text-muted-foreground">
                  No erasures recorded.
                </td>
              </tr>
            ) : (
              records.map((record) => {
                const counts =
                  (record.details?.counts as Record<string, number> | undefined) ?? {};
                return (
                  <tr key={record.id} className="border-t border-border">
                    <td className="py-2 pr-4">{formatDate(record.erased_at)}</td>
                    <td className="py-2 pr-4">{record.erasure_ticket}</td>
                    <td className="py-2 pr-4 font-mono text-xs">
                      {shortHash(record.subject_email_hash)}&hellip;
                    </td>
                    <td className="py-2">{totalDestroyed(counts)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {modalOpen ? (
        <ErasureModal
          organizationId={organizationId}
          onClose={() => setModalOpen(false)}
          onErased={onChanged}
        />
      ) : null}
    </section>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const OrganizationCompliance: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const queryClient = useQueryClient();

  const isOwner = String(organizationRole).toUpperCase() === "OWNER";

  const overview = useQuery({
    queryKey: complianceKeys.overview(organizationId),
    queryFn: () => getComplianceOverview(organizationId),
    enabled: Boolean(organizationId),
  });

  const erasures = useQuery({
    queryKey: complianceKeys.erasures(organizationId),
    queryFn: () => listErasures(organizationId),
    enabled: Boolean(organizationId),
  });

  const exportsQuery = useQuery({
    queryKey: complianceKeys.exports(organizationId),
    queryFn: () => listComplianceExports(organizationId),
    enabled: Boolean(organizationId),
  });

  const invalidateAll = (): void => {
    void queryClient.invalidateQueries({
      queryKey: complianceKeys.all(organizationId),
    });
  };

  const residencyOptions = useMemo(
    () => overview.data?.residency.available_regions ?? [],
    [overview.data],
  );

  if (overview.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading compliance settings&hellip;
      </div>
    );
  }

  if (overview.isError || !overview.data) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive" role="alert">
          {errorMessage(overview.error)}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-xl font-semibold">
          Data governance &amp; compliance
        </h1>
        <p className={HINT}>
          Residency, retention, subject erasure and DPA exports for this
          organization.
        </p>
      </header>

      <ResidencyCard
        organizationId={organizationId}
        region={overview.data.residency.region}
        options={residencyOptions}
        canWrite={isOwner}
        onChanged={invalidateAll}
      />

      <RetentionCard
        organizationId={organizationId}
        policy={overview.data.retention}
        canWrite={isOwner}
        onChanged={invalidateAll}
      />

      <ExportsCard
        organizationId={organizationId}
        records={exportsQuery.data ?? []}
        isLoading={exportsQuery.isLoading}
        onChanged={invalidateAll}
      />

      <ErasureCard
        organizationId={organizationId}
        records={erasures.data ?? []}
        isLoading={erasures.isLoading}
        canWrite={isOwner}
        onChanged={invalidateAll}
      />
    </div>
  );
};

export default OrganizationCompliance;
