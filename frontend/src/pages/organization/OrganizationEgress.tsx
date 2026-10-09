/**
 * ARCH50-S2:page-egress — the organization's egress lockdown (Enterprise).
 *
 * When the lockdown is on, every connection the platform makes for this organization — webhooks, warehouse and
 * ERP targets, SSO metadata, its own SMTP server and the AI model providers (platform or its own keys) — must match
 * a rule here; anything else is refused before it connects, recorded (per hour, with a count) and audited. The
 * owner switches it and edits the rules; admins read the policy and the refusals and can test a destination.
 * Locked without the capability; the server enforces the same key (402).
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Lock, Network, ShieldCheck, ShieldOff, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_DESTRUCTIVE, BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, SCROLL_X,
  SECTION_TITLE, SELECT, SURFACE, SURFACE_INSET, TABLE_HEAD, TABLE_ROW,
} from "@/components/ui/primitives";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { errorMessage } from "@/services/api/errors";
import {
  addEgressRule, deleteEgressRule, egressKeys, getEgressPolicy, getEgressRefusals, setEgressLockdown,
  testEgressDestination,
} from "@/services/api/sovereign";
import {
  CHANNEL_LABELS, REASON_LABELS, TENANT_CHANNELS, type Channel, type EgressDecision, type RefusalReason,
  type TenantChannel,
} from "@/types/sovereign";
import { formatTimestamp } from "@/utils/displayTime";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";
import { PageHeader } from "@/components/ui/PageHeader";

const COUNT = new Intl.NumberFormat();

const channelLabel = (channel: string | null): string =>
  channel ? (CHANNEL_LABELS[channel as Channel] ?? channel) : "Any governed channel";

const reasonLabel = (reason: string | null): string =>
  reason ? (REASON_LABELS[reason as RefusalReason] ?? reason) : "";

const EgressLocked: React.FC = () => {
  const { organization, organizationRole } = useResolvedOrganization();
  return (
  <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="egress-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4" aria-hidden />
      <h2 id="egress-lock" className={SECTION_TITLE}>Egress lockdown</h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Confine every connection the platform makes for your organization — webhooks, warehouses, ERP targets, SSO
      metadata, your SMTP server and the AI model providers — to destinations you allow. Anything else is refused
      before it connects, and every refusal is recorded and audited.
    </p>
    <p className={HINT}>It&apos;s included on the Enterprise plan.</p>
    <ViewPlansAction organizationSlug={organization.organization_slug} organizationRole={organizationRole} />
  </section>
  );
};

const OrganizationEgress: React.FC = () => {
  const { organization, organizationId, organizationRole } = useResolvedOrganization();
  const role = String(organizationRole).toUpperCase();
  const isOwner = role === "OWNER";
  const queryClient = useQueryClient();
  const capability = useCapabilityAccess(organizationId, CAPABILITY.egressLockdown);
  const [days, setDays] = useState(7);
  const [channel, setChannel] = useState<TenantChannel | "">("");
  const [pattern, setPattern] = useState("");
  const [port, setPort] = useState("");
  const [note, setNote] = useState("");
  const [testChannel, setTestChannel] = useState<TenantChannel>("WEBHOOK");
  const [destination, setDestination] = useState("");
  const [decision, setDecision] = useState<EgressDecision | null>(null);

  const enabled = capability.granted && Boolean(organizationId);
  const policy = useQuery({ queryKey: egressKeys.policy(organizationId), queryFn: () => getEgressPolicy(organizationId), enabled });
  const refusals = useQuery({
    queryKey: egressKeys.refusals(organizationId, days), queryFn: () => getEgressRefusals(organizationId, days), enabled,
  });
  const refresh = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: egressKeys.all(organizationId) });
  };

  const lockdown = useMutation({
    mutationFn: (next: boolean) => setEgressLockdown(organizationId, next),
    onSuccess: async (result) => {
      await refresh();
      toast.success(result.lockdown_enabled ? "Egress lockdown is on." : "Egress lockdown is off.");
    },
    onError: (error) => toast.error(errorMessage(error, "The lockdown could not be changed.")),
  });
  const addRule = useMutation({
    mutationFn: () => addEgressRule(organizationId, {
      channel: channel === "" ? null : channel, host_pattern: pattern.trim(),
      port: port.trim() === "" ? null : Number.parseInt(port, 10), note: note.trim() === "" ? null : note.trim(),
    }),
    onSuccess: async () => {
      setPattern("");
      setPort("");
      setNote("");
      await refresh();
      toast.success("Rule added.");
    },
    onError: (error) => toast.error(errorMessage(error, "The rule could not be added.")),
  });
  const removeRule = useMutation({
    mutationFn: (ruleId: string) => deleteEgressRule(organizationId, ruleId),
    onSuccess: async () => {
      await refresh();
      toast.success("Rule removed.");
    },
    onError: (error) => toast.error(errorMessage(error, "The rule could not be removed.")),
  });
  const test = useMutation({
    mutationFn: () => testEgressDestination(organizationId, testChannel, destination.trim()),
    onSuccess: (result) => setDecision(result),
    onError: (error) => toast.error(errorMessage(error, "The destination could not be tested.")),
  });

  if (capability.isLoading) {
    return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading…</div>;
  }
  if (!capability.granted) {
    return <EgressLocked />;
  }

  const data = policy.data;
  const on = Boolean(data?.lockdown_enabled);
  const portValid = port.trim() === "" || (/^\d+$/.test(port.trim()) && Number(port) >= 1 && Number(port) <= 65535);

  return (
    <div className="mx-auto max-w-5xl space-y-6" data-testid="egress-page">
      <PageHeader
        icon={Network}
        eyebrow={organization.organization_name}
        title="Egress lockdown"
        description={<>Where the platform may connect on your organization&apos;s behalf. Refusals happen before any connection opens and are never retried.</>}
      />

      {policy.isError ? (
        <p role="alert" className="text-sm text-destructive">{errorMessage(policy.error, "The policy could not be loaded.")}</p>
      ) : null}

      <section className={`${SURFACE} space-y-3 p-5`} aria-labelledby="egress-switch">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {on ? <ShieldCheck className="h-5 w-5 text-emerald-600" aria-hidden /> : <ShieldOff className="h-5 w-5 text-muted-foreground" aria-hidden />}
            <h2 id="egress-switch" className={SECTION_TITLE}>{on ? "Lockdown is on" : "Lockdown is off"}</h2>
          </div>
          {isOwner ? (
            <button type="button" className={on ? BUTTON_DESTRUCTIVE : BUTTON_PRIMARY} disabled={lockdown.isPending || !data}
                    onClick={() => lockdown.mutate(!on)} data-testid="egress-toggle">
              {lockdown.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              {on ? "Switch lockdown off" : "Switch lockdown on"}
            </button>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {on
            ? `Only the ${COUNT.format(data?.rules.length ?? 0)} destination rule${(data?.rules.length ?? 0) === 1 ? "" : "s"} below are reachable on the governed channels.`
            : "Governed channels may reach any destination the platform's other safeguards allow."}
          {on && (data?.rules.length ?? 0) === 0 ? " With no rules, every governed connection is refused — including AI model calls." : ""}
        </p>
        <p className={HINT}>
          This deployment&apos;s own egress mode: <strong>{data?.deployment_mode === "deny" ? "deny (air-gapped)" : "open"}</strong>.
          {data?.updated_at ? ` Last changed ${formatTimestamp(data.updated_at)}.` : ""}
          {isOwner ? "" : " Only the organization owner can change the lockdown and its rules."}
        </p>
        <details className={`${SURFACE_INSET} p-3 text-sm`}>
          <summary className="cursor-pointer font-medium">Governed channels</summary>
          <ul className="mt-2 grid gap-1 sm:grid-cols-2">
            {TENANT_CHANNELS.map((c) => <li key={c}>{CHANNEL_LABELS[c]}</li>)}
          </ul>
        </details>
      </section>

      <section className={`${SURFACE} space-y-3 p-5`} aria-labelledby="egress-rules">
        <h2 id="egress-rules" className={SECTION_TITLE}>Allowed destinations</h2>
        <div className={SCROLL_X}>
          <table className="w-full text-sm">
            <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Host</th><th className="pr-3">Port</th><th className="pr-3">Channel</th><th className="pr-3">Note</th><th className="pr-3">Added</th><th /></tr></thead>
            <tbody>
              {(data?.rules ?? []).map((rule) => (
                <tr key={rule.id} className={TABLE_ROW}>
                  <td className="py-2 pr-3 font-mono">{rule.host_pattern}</td>
                  <td className="pr-3 tabular-nums">{rule.port ?? "any"}</td>
                  <td className="pr-3">{channelLabel(rule.channel)}</td>
                  <td className="pr-3 text-muted-foreground">{rule.note ?? ""}</td>
                  <td className="pr-3 text-muted-foreground">{formatTimestamp(rule.created_at)}</td>
                  <td className="text-right">
                    {isOwner ? (
                      <button type="button" className={BUTTON_SECONDARY} aria-label={`Remove ${rule.host_pattern}`}
                              disabled={removeRule.isPending} onClick={() => removeRule.mutate(rule.id)}>
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
              {(data?.rules.length ?? 0) === 0 ? <tr><td colSpan={6} className={`py-3 ${HINT}`}>No rules yet.</td></tr> : null}
            </tbody>
          </table>
        </div>
        {isOwner ? (
          <form className="grid gap-3 sm:grid-cols-[2fr_1fr_2fr_2fr_auto] sm:items-end" onSubmit={(event) => {
            event.preventDefault();
            addRule.mutate();
          }}>
            <label className="space-y-1"><span className={FIELD_LABEL}>Host, *.domain, IP or CIDR</span>
              <input className={INPUT} value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="*.acme.com" required maxLength={253} /></label>
            <label className="space-y-1"><span className={FIELD_LABEL}>Port</span>
              <input className={INPUT} value={port} onChange={(e) => setPort(e.target.value)} placeholder="any" inputMode="numeric" /></label>
            <label className="space-y-1"><span className={FIELD_LABEL}>Channel</span>
              <select className={SELECT} value={channel} onChange={(e) => setChannel(e.target.value as TenantChannel | "")}>
                <option value="">Any governed channel</option>
                {TENANT_CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>)}
              </select></label>
            <label className="space-y-1"><span className={FIELD_LABEL}>Note</span>
              <input className={INPUT} value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} /></label>
            <button type="submit" className={BUTTON_PRIMARY} disabled={addRule.isPending || pattern.trim() === "" || !portValid}>
              {addRule.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Add rule
            </button>
          </form>
        ) : null}
        <p className={HINT}>A rule of <span className="font-mono">*.acme.com</span> allows its subdomains, not acme.com itself. At most {COUNT.format(data?.max_rules ?? 200)} rules.</p>
      </section>

      <section className={`${SURFACE} space-y-3 p-5`} aria-labelledby="egress-test">
        <h2 id="egress-test" className={SECTION_TITLE}>Test a destination</h2>
        <form className="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end" onSubmit={(event) => {
          event.preventDefault();
          test.mutate();
        }}>
          <label className="space-y-1"><span className={FIELD_LABEL}>Channel</span>
            <select className={SELECT} value={testChannel} onChange={(e) => setTestChannel(e.target.value as TenantChannel)}>
              {TENANT_CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>)}
            </select></label>
          <label className="space-y-1"><span className={FIELD_LABEL}>URL or host</span>
            <input className={INPUT} value={destination} onChange={(e) => setDestination(e.target.value)} placeholder="https://hooks.acme.com/flowpilot" /></label>
          <button type="submit" className={BUTTON_SECONDARY} disabled={test.isPending || destination.trim() === ""}>Test</button>
        </form>
        {decision ? (
          <p role="status" data-testid="egress-decision" className={`text-sm ${decision.allowed ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}>
            {decision.allowed
              ? `Allowed${decision.matched ? ` by ${decision.matched}` : ""}: ${decision.host}${decision.port ? `:${decision.port}` : ""}`
              : `Refused — ${reasonLabel(decision.reason)}: ${decision.explanation}`}
          </p>
        ) : null}
      </section>

      <section className={`${SURFACE} space-y-3 p-5`} aria-labelledby="egress-refusals">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="egress-refusals" className={SECTION_TITLE}>Refused connections</h2>
          <select className={`${SELECT} w-auto`} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Window">
            {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d === 1 ? "" : "s"}</option>)}
          </select>
        </div>
        <div className={SCROLL_X}>
          <table className="w-full text-sm">
            <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Last</th><th className="pr-3">Channel</th><th className="pr-3">Destination</th><th className="pr-3">Why</th><th className="pr-3 text-right">Times</th></tr></thead>
            <tbody>
              {(refusals.data?.refusals ?? []).map((r) => (
                <tr key={r.id} className={TABLE_ROW}>
                  <td className="py-2 pr-3 text-muted-foreground">{formatTimestamp(r.last_at)}</td>
                  <td className="pr-3">{channelLabel(r.channel)}</td>
                  <td className="pr-3 font-mono">{r.host}{r.port ? `:${r.port}` : ""}</td>
                  <td className="pr-3">{reasonLabel(r.reason)}</td>
                  <td className="pr-3 text-right tabular-nums">{COUNT.format(r.count)}</td>
                </tr>
              ))}
              {(refusals.data?.refusals.length ?? 0) === 0 ? <tr><td colSpan={5} className={`py-3 ${HINT}`}>Nothing refused in this window.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default OrganizationEgress;
