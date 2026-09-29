/**
 * ARCH50-S2:page-sovereign — the operator's Sovereign Edition console (platform superadmins).
 *
 *   Overview   edition, egress mode, the licence (re-verified on every read) and usage against it
 *   Egress     every outbound channel, where it is opened, what the operator declared for it; test a
 *              destination (optionally as an organization); refusals across the deployment
 *   Local model  the operator's OpenAI-compatible endpoint: mode, host, probe
 *   Licence    install a licence (verified offline before it is stored)
 *   Release    the signed release manifest: SBOM + SHA256SUMS, re-hashed
 *   Recovery   measured point-in-time recovery drills (RPO / RTO against the objectives), heartbeat lag
 *
 * Nothing here changes the egress mode or the local model: those are the operator's environment.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, PAGE_TITLE, SCROLL_X, SECTION_TITLE, SELECT, SURFACE,
  TABLE_HEAD, TABLE_ROW, TEXTAREA,
} from "@/components/ui/primitives";
import { errorMessage } from "@/services/api/errors";
import {
  getAllRefusals, getLocalModelHealth, getReleaseStatus, getSovereignStatus, installLicence, operatorEgressTest,
  sovereignKeys,
} from "@/services/api/sovereign";
import {
  CHANNELS, CHANNEL_LABELS, LICENCE_STATUS_LABELS, REASON_LABELS, type Channel, type EgressDecision, type RefusalReason,
} from "@/types/sovereign";
import { formatTimestamp } from "@/utils/displayTime";

type Tab = "overview" | "egress" | "model" | "licence" | "release" | "recovery";
const TABS: readonly (readonly [Tab, string])[] = [
  ["overview", "Overview"], ["egress", "Egress"], ["model", "Local model"], ["licence", "Licence"],
  ["release", "Release"], ["recovery", "Recovery"],
];
const COUNT = new Intl.NumberFormat();

const seconds = (value: string | number | null | undefined): string => {
  if (value === null || value === undefined) {
    return "—";
  }
  const n = Number(value);
  return n < 120 ? `${n.toFixed(1)} s` : `${(n / 60).toFixed(1)} min`;
};

const Stat: React.FC<{ readonly label: string; readonly value: React.ReactNode; readonly hint?: string | undefined }> = ({ label, value, hint }) => (
  <div className={`${SURFACE} p-4`}>
    <p className={HINT}>{label}</p>
    <p className="mt-1 text-lg font-semibold">{value}</p>
    {hint ? <p className={`mt-1 ${HINT}`}>{hint}</p> : null}
  </div>
);

const SovereignConsole: React.FC = () => {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [days, setDays] = useState(7);
  const [channel, setChannel] = useState<Channel>("LLM_PROVIDER");
  const [destination, setDestination] = useState("");
  const [asOrg, setAsOrg] = useState("");
  const [decision, setDecision] = useState<EgressDecision | null>(null);
  const [licenceText, setLicenceText] = useState("");

  const status = useQuery({ queryKey: sovereignKeys.status(), queryFn: getSovereignStatus });
  const refusals = useQuery({ queryKey: sovereignKeys.refusals(days), queryFn: () => getAllRefusals(days), enabled: tab === "egress" });
  const model = useQuery({ queryKey: sovereignKeys.localModel(), queryFn: getLocalModelHealth, enabled: tab === "model" });
  const release = useQuery({ queryKey: sovereignKeys.release(), queryFn: getReleaseStatus, enabled: tab === "release" });
  const test = useMutation({
    mutationFn: () => operatorEgressTest(channel, destination.trim(), asOrg.trim() || undefined),
    onSuccess: (result) => setDecision(result),
    onError: (error) => toast.error(errorMessage(error, "The destination could not be tested.")),
  });
  const install = useMutation({
    mutationFn: () => installLicence(licenceText),
    onSuccess: async (result) => {
      setLicenceText("");
      await queryClient.invalidateQueries({ queryKey: sovereignKeys.all() });
      toast.success(`Licence ${result.licence_id ?? ""} installed: ${LICENCE_STATUS_LABELS[result.status] ?? result.status}.`);
    },
    onError: (error) => toast.error(errorMessage(error, "The licence was refused.")),
  });

  const s = status.data;
  return (
    <div className="space-y-6 p-4" data-testid="sovereign-console">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className={PAGE_TITLE}>Sovereign edition</h1>
          <p className="text-sm text-muted-foreground">Egress, the local model, the licence, the release and recovery evidence for this deployment.</p>
        </div>
        <button type="button" className={BUTTON_SECONDARY} onClick={() => void queryClient.invalidateQueries({ queryKey: sovereignKeys.all() })}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />Refresh
        </button>
      </header>
      <nav className="flex flex-wrap gap-1 border-b border-border/60" aria-label="Sovereign console">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" onClick={() => setTab(key)} aria-current={tab === key ? "page" : undefined}
                  className={`rounded-t-md px-3 py-2 text-sm ${tab === key ? "border-b-2 border-primary font-semibold" : "text-muted-foreground hover:text-foreground"}`}>
            {label}
          </button>
        ))}
      </nav>

      {status.isLoading ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading…</div> : null}
      {status.isError ? <p role="alert" className="text-sm text-destructive">{errorMessage(status.error, "The status could not be loaded.")}</p> : null}

      {s && tab === "overview" ? (
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Edition" value={s.edition === "sovereign" ? "Sovereign" : "Hosted (SaaS)"} hint={`Environment: ${s.environment}`} />
          <Stat label="Egress mode" value={s.egress.mode === "deny" ? "Deny (air-gapped)" : "Open"}
                hint={`${COUNT.format(s.refusals_24h)} refusal${s.refusals_24h === 1 ? "" : "s"} in 24 h`} />
          <Stat label="Licence" value={LICENCE_STATUS_LABELS[s.licence.status] ?? s.licence.status}
                hint={s.licence.expires_at ? `Expires ${formatTimestamp(s.licence.expires_at)}` : s.licence.reason} />
          <Stat label="Usage" value={`${COUNT.format(s.usage.organizations)} orgs · ${COUNT.format(s.usage.seats)} seats`}
                hint={s.licence.max_organizations ? `Licensed: ${COUNT.format(s.licence.max_organizations)} orgs · ${COUNT.format(s.licence.max_seats ?? 0)} seats` : undefined} />
          <Stat label="Local model" value={String(s.local_llm["mode"] ?? "off")} hint={s.local_llm["model"] ? `${String(s.local_llm["model"])} @ ${String(s.local_llm["host"] ?? "")}` : "Not configured"} />
          <Stat label="Last recovery drill" value={s.dr.latest["PITR"]?.outcome ?? "none"}
                hint={s.dr.latest["PITR"] ? `RPO ${seconds(s.dr.latest["PITR"]?.rpo_seconds)} · RTO ${seconds(s.dr.latest["PITR"]?.rto_seconds)}` : "Run dr_pitr.py drill"} />
          <Stat label="Heartbeat lag" value={seconds(s.dr.heartbeat.lag_seconds)} hint={s.dr.heartbeat.latest ? formatTimestamp(s.dr.heartbeat.latest) : "No heartbeat yet"} />
          <Stat label="Recovery objectives" value={s.dr.meets_targets ? "Met" : "Not demonstrated"}
                hint={`RPO ≤ ${seconds(s.dr.targets.rpo_seconds)}, RTO ≤ ${seconds(s.dr.targets.rto_seconds)}`} />
        </section>
      ) : null}

      {s && tab === "egress" ? (
        <div className="space-y-4">
          <section className={`${SURFACE} ${SCROLL_X} p-4`}>
            <h2 className={SECTION_TITLE}>Outbound channels</h2>
            <table className="mt-2 w-full text-sm">
              <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Channel</th><th className="pr-3">Governed by</th><th className="pr-3">Declared destinations</th><th className="pr-3">Opened in</th></tr></thead>
              <tbody>
                {s.egress.channels.map((c) => (
                  <tr key={c.channel} className={TABLE_ROW}>
                    <td className="py-2 pr-3">{c.label}</td>
                    <td className="pr-3">{c.operator_only ? "Operator only" : c.tenant_governed ? "Tenant lockdown + deployment" : "Deployment"}</td>
                    <td className="pr-3 font-mono text-xs">{c.declared.length ? c.declared.join(", ") : "—"}</td>
                    <td className="pr-3 font-mono text-xs text-muted-foreground">{c.opened_in.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className={`${SURFACE} space-y-3 p-4`}>
            <h2 className={SECTION_TITLE}>Test a destination</h2>
            <form className="grid gap-3 sm:grid-cols-[1fr_2fr_1.5fr_auto] sm:items-end" onSubmit={(e) => {
              e.preventDefault();
              test.mutate();
            }}>
              <label className="space-y-1"><span className={FIELD_LABEL}>Channel</span>
                <select className={SELECT} value={channel} onChange={(e) => setChannel(e.target.value as Channel)}>
                  {CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>)}
                </select></label>
              <label className="space-y-1"><span className={FIELD_LABEL}>URL or host</span>
                <input className={INPUT} value={destination} onChange={(e) => setDestination(e.target.value)} placeholder="api.groq.com:443" /></label>
              <label className="space-y-1"><span className={FIELD_LABEL}>As organization (id, optional)</span>
                <input className={INPUT} value={asOrg} onChange={(e) => setAsOrg(e.target.value)} /></label>
              <button type="submit" className={BUTTON_SECONDARY} disabled={test.isPending || destination.trim() === ""}>Test</button>
            </form>
            {decision ? (
              <p role="status" className={`text-sm ${decision.allowed ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}>
                {decision.allowed ? `Allowed${decision.matched ? ` (${decision.matched})` : ""}` :
                  `Refused — ${REASON_LABELS[decision.reason as RefusalReason] ?? decision.reason}: ${decision.explanation}`}
              </p>
            ) : null}
          </section>
          <section className={`${SURFACE} ${SCROLL_X} space-y-2 p-4`}>
            <div className="flex items-center justify-between gap-2">
              <h2 className={SECTION_TITLE}>Refused connections</h2>
              <select className={`${SELECT} w-auto`} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Window">
                {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d === 1 ? "" : "s"}</option>)}
              </select>
            </div>
            <table className="w-full text-sm">
              <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Last</th><th className="pr-3">Organization</th><th className="pr-3">Channel</th><th className="pr-3">Destination</th><th className="pr-3">Why</th><th className="pr-3 text-right">Times</th></tr></thead>
              <tbody>
                {(refusals.data?.refusals ?? []).map((r) => (
                  <tr key={r.id} className={TABLE_ROW}>
                    <td className="py-2 pr-3 text-muted-foreground">{formatTimestamp(r.last_at)}</td>
                    <td className="pr-3 font-mono text-xs">{r.organization_id ?? "deployment"}</td>
                    <td className="pr-3">{CHANNEL_LABELS[r.channel as Channel] ?? r.channel}</td>
                    <td className="pr-3 font-mono">{r.host}{r.port ? `:${r.port}` : ""}</td>
                    <td className="pr-3">{REASON_LABELS[r.reason as RefusalReason] ?? r.reason}</td>
                    <td className="pr-3 text-right tabular-nums">{COUNT.format(r.count)}</td>
                  </tr>
                ))}
                {(refusals.data?.refusals.length ?? 0) === 0 ? <tr><td colSpan={6} className={`py-3 ${HINT}`}>Nothing refused in this window.</td></tr> : null}
              </tbody>
            </table>
          </section>
        </div>
      ) : null}

      {tab === "model" ? (
        <section className={`${SURFACE} space-y-2 p-4`}>
          <h2 className={SECTION_TITLE}>Operator&apos;s local model</h2>
          <p className={HINT}>Configured only through LOCAL_LLM_MODE / LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL in the environment; no tenant can select or point it anywhere.</p>
          {model.isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {model.data ? (
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div><dt className={HINT}>Mode</dt><dd>{model.data.mode}</dd></div>
              <div><dt className={HINT}>Model</dt><dd className="font-mono">{model.data.model ?? "—"}</dd></div>
              <div><dt className={HINT}>Host</dt><dd className="font-mono">{model.data.host ?? "—"}</dd></div>
              <div><dt className={HINT}>Reachable</dt><dd>{model.data.configured ? (model.data.reachable ? `yes (${String(model.data.latency_ms ?? "")} ms)` : "no") : "not configured"}</dd></div>
              <div><dt className={HINT}>Serves the configured model</dt><dd>{model.data.model_served ? "yes" : "no"}</dd></div>
              <div><dt className={HINT}>Models offered</dt><dd className="font-mono text-xs">{model.data.models.join(", ") || "—"}</dd></div>
              {model.data.error ? <div className="sm:col-span-2 text-destructive">{model.data.error}</div> : null}
            </dl>
          ) : null}
        </section>
      ) : null}

      {s && tab === "licence" ? (
        <section className={`${SURFACE} space-y-3 p-4`}>
          <h2 className={SECTION_TITLE}>Licence</h2>
          <p className="text-sm">{LICENCE_STATUS_LABELS[s.licence.status] ?? s.licence.status}{s.licence.reason ? ` — ${s.licence.reason}` : ""}</p>
          {s.licence.licence_id ? (
            <p className={HINT}>{s.licence.licence_id} for {s.licence.licensee} · key {s.licence.key_id} · features {s.licence.features.join(", ") || "—"}</p>
          ) : null}
          <label className="block space-y-1"><span className={FIELD_LABEL}>Install a licence (the JSON document)</span>
            <textarea className={`${TEXTAREA} font-mono text-xs`} rows={8} value={licenceText} onChange={(e) => setLicenceText(e.target.value)} /></label>
          <button type="button" className={BUTTON_PRIMARY} disabled={install.isPending || licenceText.trim().length < 2} onClick={() => install.mutate()}>
            {install.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Verify and install
          </button>
          <p className={HINT}>Verified offline against the Ed25519 keys pinned in this release. A development key is refused in production.</p>
        </section>
      ) : null}

      {tab === "release" ? (
        <section className={`${SURFACE} space-y-2 p-4`}>
          <h2 className={SECTION_TITLE}>Release manifest</h2>
          {release.isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {release.data ? (
            <>
              <p className="text-sm font-semibold" data-testid="release-status">{release.data.status}</p>
              <p className={HINT}>{COUNT.format(release.data.files)} files listed · signed by {release.data.key_id ?? "—"} · {release.data.release_dir}</p>
              {release.data.modified.length ? <p className="text-sm text-destructive">Modified: {release.data.modified.join(", ")}</p> : null}
              {release.data.missing.length ? <p className="text-sm text-destructive">Missing: {release.data.missing.join(", ")}</p> : null}
              <p className={HINT}>Build it with scripts/release_manifest.py build --version X --key release.key (a CycloneDX SBOM and signed SHA256SUMS).</p>
            </>
          ) : null}
        </section>
      ) : null}

      {s && tab === "recovery" ? (
        <section className={`${SURFACE} ${SCROLL_X} space-y-2 p-4`}>
          <h2 className={SECTION_TITLE}>Recovery drills</h2>
          <p className={HINT}>Measured by scripts/dr_pitr.py (pg_basebackup + WAL archive + restore into a scratch cluster). Objectives: RPO ≤ {seconds(s.dr.targets.rpo_seconds)}, RTO ≤ {seconds(s.dr.targets.rto_seconds)}.</p>
          <table className="w-full text-sm">
            <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Finished</th><th className="pr-3">Kind</th><th className="pr-3">Outcome</th><th className="pr-3 text-right">RPO</th><th className="pr-3 text-right">RTO</th><th className="pr-3">Host</th></tr></thead>
            <tbody>
              {s.dr.recent.map((d) => (
                <tr key={d.id} className={TABLE_ROW}>
                  <td className="py-2 pr-3 text-muted-foreground">{formatTimestamp(d.finished_at)}</td>
                  <td className="pr-3">{d.kind ?? "PITR"}</td>
                  <td className="pr-3">{d.outcome}</td>
                  <td className="pr-3 text-right tabular-nums">{seconds(d.rpo_seconds)}</td>
                  <td className="pr-3 text-right tabular-nums">{seconds(d.rto_seconds)}</td>
                  <td className="pr-3 text-muted-foreground">{d.host}</td>
                </tr>
              ))}
              {s.dr.recent.length === 0 ? <tr><td colSpan={6} className={`py-3 ${HINT}`}>No drill recorded yet.</td></tr> : null}
            </tbody>
          </table>
        </section>
      ) : null}
    </div>
  );
};

export default SovereignConsole;
