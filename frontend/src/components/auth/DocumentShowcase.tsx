/**
 * The right-hand pane of the sign-in screens: what FlowPilot does, shown
 * rather than told. A stylised invoice with the boxes the extractor draws, the
 * fields it read with their confidence, and the match that clears it for
 * posting. Hovering a field lights its box.
 *
 * Illustration only: the document, numbers and names are invented, and the
 * claims under it are ones the product makes good on (SSO, tenant isolation,
 * an audit trail). No figure here is a performance promise.
 *
 * Always dark (the `dark` class scopes the tokens), whatever the user's theme.
 */
import React, { useEffect, useState } from "react";
import { CheckCircle2, FileText, Fingerprint, Lock, ScrollText, ShieldCheck, Sparkles } from "lucide-react";

type FieldKey = "vendor" | "number" | "date" | "total";

interface Field {
  readonly key: FieldKey;
  readonly label: string;
  readonly value: string;
  readonly confidence: number;
  readonly tone: string;
  readonly box: string;
  /** Put the tag beside the box rather than above it (tight neighbours). */
  readonly labelLeft?: boolean;
}

const FIELDS: readonly Field[] = [
  {
    key: "vendor",
    label: "Vendor",
    value: "Northwind Traders",
    confidence: 99.4,
    tone: "text-sky-300",
    box: "border-sky-400/80 bg-sky-400/10 shadow-[0_0_24px_-4px_rgba(56,189,248,0.55)]",
  },
  {
    key: "number",
    label: "Invoice no.",
    value: "NW-48213",
    confidence: 98.9,
    tone: "text-violet-300",
    box: "border-violet-400/80 bg-violet-400/10 shadow-[0_0_24px_-4px_rgba(167,139,250,0.55)]",
  },
  {
    key: "date",
    label: "Issue date",
    value: "2026-09-12",
    confidence: 97.6,
    tone: "text-amber-300",
    box: "border-amber-400/80 bg-amber-400/10 shadow-[0_0_24px_-4px_rgba(251,191,36,0.5)]",
    labelLeft: true,
  },
  {
    key: "total",
    label: "Total due",
    value: "$18,240.00",
    confidence: 99.1,
    tone: "text-emerald-300",
    box: "border-emerald-400/80 bg-emerald-400/10 shadow-[0_0_24px_-4px_rgba(52,211,153,0.55)]",
  },
];

const HIGHLIGHTS: ReadonlyArray<{ title: string; body: string }> = [
  {
    title: "Three-way matching",
    body: "Invoices are matched to purchase orders and goods receipts before anyone approves a payment.",
  },
  {
    title: "Human review where it counts",
    body: "Low-confidence fields go to a review queue. Everything else flows straight through.",
  },
  {
    title: "Workflow automation",
    body: "Rules run the moment a document lands: tag it, route it, post it to your ERP.",
  },
  {
    title: "Ask your documents",
    body: "An assistant that answers with citations to the exact page it read.",
  },
];

const TRUST: ReadonlyArray<{ label: string; icon: React.ElementType }> = [
  { label: "SAML & OIDC SSO", icon: Fingerprint },
  { label: "Tenant-isolated data", icon: Lock },
  { label: "Full audit trail", icon: ScrollText },
];

/** Draws the extractor's box around a field on the page, with its tag. */
function Boxed({
  field: key,
  active,
  children,
}: {
  readonly field: FieldKey;
  readonly active: FieldKey | null;
  readonly children: React.ReactNode;
}) {
  const field = FIELDS.find((item) => item.key === key);
  if (!field) {
    return <>{children}</>;
  }
  const dim = active !== null && active !== key;
  return (
    <div className="relative">
      {children}
      <div
        className={`absolute -inset-[5px] rounded-[4px] border transition-all duration-200 ${field.box} ${
          dim ? "opacity-25" : "opacity-100"
        } ${active === key ? "scale-[1.04]" : ""}`}
      >
        <span
          className={`absolute whitespace-nowrap rounded-sm bg-zinc-950/90 px-1 text-[8.5px] font-medium leading-[13px] ${field.tone} ${
            field.labelLeft ? "-left-1 top-1/2 -translate-x-full -translate-y-1/2" : "-top-[15px] left-0"
          }`}
        >
          {field.label}
        </span>
      </div>
    </div>
  );
}

function DocumentPreview() {
  const [active, setActive] = useState<FieldKey | null>(null);

  return (
    <div className="relative w-full max-w-[640px] animate-slide-up">
      {/* glow under the card */}
      <div className="pointer-events-none absolute -inset-6 rounded-[28px] bg-gradient-to-tr from-primary/25 via-violet-500/10 to-transparent opacity-70 blur-2xl" />

      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-zinc-900/70 shadow-[0_32px_80px_-24px_rgba(0,0,0,0.8)] backdrop-blur-xl">
        {/* window bar */}
        <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2.5">
          <div className="flex items-center gap-2 text-[12px] text-zinc-400">
            <FileText className="h-3.5 w-3.5 text-zinc-500" aria-hidden="true" />
            <span className="font-mono text-zinc-300">northwind-invoice-48213.pdf</span>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/25 bg-emerald-400/10 px-2 py-0.5 text-[10.5px] font-medium text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Extracted
          </span>
        </div>

        <div className="grid grid-cols-[1.08fr_1fr] gap-0">
          {/* the page */}
          <div className="relative border-r border-white/[0.06] p-4">
            <div className="relative aspect-[3/3.6] overflow-hidden rounded-lg border border-white/[0.07] bg-gradient-to-b from-zinc-800/70 to-zinc-900/80 p-[7%]">
              <div className="flex items-start justify-between">
                <Boxed field="vendor" active={active}>
                  <div className="space-y-1.5">
                    <div className="h-2.5 w-28 rounded-sm bg-zinc-300/70" />
                    <div className="h-1.5 w-20 rounded-sm bg-zinc-500/50" />
                  </div>
                </Boxed>
                <div className="flex flex-col items-end gap-3.5">
                  <Boxed field="number" active={active}>
                    <div className="flex flex-col items-end space-y-1.5">
                      <div className="h-2 w-14 rounded-sm bg-zinc-400/60" />
                      <div className="h-1.5 w-12 rounded-sm bg-zinc-500/50" />
                    </div>
                  </Boxed>
                  <Boxed field="date" active={active}>
                    <div className="h-1.5 w-12 rounded-sm bg-zinc-400/50" />
                  </Boxed>
                </div>
              </div>
              <div className="mt-[14%] space-y-1">
                <div className="h-1.5 w-24 rounded-sm bg-zinc-500/40" />
                <div className="h-1.5 w-32 rounded-sm bg-zinc-500/40" />
              </div>
              <div className="mt-[10%] space-y-2">
                {[0, 1, 2, 3].map((row) => (
                  <div key={row} className="flex items-center gap-2">
                    <div className={`h-1.5 rounded-sm bg-zinc-500/40 ${row % 2 ? "w-[46%]" : "w-[52%]"}`} />
                    <div className="h-1.5 flex-1" />
                    <div className="h-1.5 w-[14%] rounded-sm bg-zinc-500/40" />
                    <div className="h-1.5 w-[16%] rounded-sm bg-zinc-400/50" />
                  </div>
                ))}
              </div>
              <div className="absolute bottom-[9%] right-[7%] w-[37%]">
                <Boxed field="total" active={active}>
                  <div className="space-y-1.5">
                    <div className="flex justify-between">
                      <div className="h-1.5 w-10 rounded-sm bg-zinc-500/40" />
                      <div className="h-1.5 w-8 rounded-sm bg-zinc-500/40" />
                    </div>
                    <div className="flex justify-between">
                      <div className="h-2 w-12 rounded-sm bg-zinc-300/70" />
                      <div className="h-2 w-12 rounded-sm bg-zinc-300/70" />
                    </div>
                  </div>
                </Boxed>
              </div>

              {/* scan line */}
              <div className="pointer-events-none absolute inset-x-0 top-0 h-[4.5%] animate-scan bg-gradient-to-b from-transparent via-primary/30 to-transparent" />
            </div>
          </div>

          {/* what was read */}
          <div className="flex flex-col p-4">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-zinc-500">Fields</p>
              <span className="inline-flex items-center gap-1 text-[10.5px] text-zinc-400">
                <Sparkles className="h-3 w-3 text-violet-300" aria-hidden="true" />
                14 found
              </span>
            </div>
            <ul className="space-y-1.5">
              {FIELDS.map((field) => (
                <li
                  key={field.key}
                  onMouseEnter={() => setActive(field.key)}
                  onMouseLeave={() => setActive(null)}
                  className={`group cursor-default rounded-lg border px-2.5 py-2 transition-colors duration-150 ${
                    active === field.key
                      ? "border-white/15 bg-white/[0.06]"
                      : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-[10.5px] font-medium ${field.tone}`}>{field.label}</span>
                    <span className="rounded border border-white/10 bg-zinc-950/60 px-1 font-mono text-[10px] tabular-nums text-zinc-300">
                      {field.confidence.toFixed(1)}%
                    </span>
                  </div>
                  <p className="mt-0.5 truncate font-mono text-[12.5px] tabular-nums text-zinc-100">{field.value}</p>
                  <div className="mt-1.5 h-[3px] overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-primary to-violet-400"
                      style={{ width: `${field.confidence}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>

            <div className="mt-auto pt-3">
              <div className="flex items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-400/[0.07] px-2.5 py-2 text-[11.5px] text-emerald-200">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden="true" />
                <span className="min-w-0 truncate">
                  Matched to <span className="font-mono">PO-2209</span> &amp; <span className="font-mono">GR-1180</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* floating chip */}
      <div className="absolute -left-5 bottom-10 hidden animate-float rounded-xl border border-white/10 bg-zinc-900/90 px-3 py-2 shadow-elevation-3 backdrop-blur-xl xl:block">
        <p className="text-[10px] uppercase tracking-[0.08em] text-zinc-500">Ready for ERP</p>
        <p className="mt-0.5 font-mono text-[13px] tabular-nums text-zinc-100">$18,240.00</p>
      </div>
    </div>
  );
}

export function DocumentShowcase({ footer }: { footer: React.ReactNode }) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => setIndex((value) => (value + 1) % HIGHLIGHTS.length), 6000);
    return () => window.clearInterval(id);
  }, []);

  const highlight = HIGHLIGHTS[index] ?? HIGHLIGHTS[0];

  return (
    <div className="relative z-10 flex h-full flex-col p-10 xl:p-14">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[12px] font-medium text-zinc-300 backdrop-blur">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
          </span>
          Document intelligence platform
        </span>
      </div>

      <div className="mt-8 max-w-xl">
        <h2 className="text-[34px] font-semibold leading-[1.1] tracking-[-0.03em] text-white xl:text-[40px]">
          Turn every document into{" "}
          <span className="bg-gradient-to-r from-sky-300 via-primary to-violet-300 bg-clip-text text-transparent">
            structured, auditable data.
          </span>
        </h2>
        <p className="mt-4 max-w-lg text-[15px] leading-relaxed text-zinc-400">
          FlowPilot reads invoices, contracts and purchase orders, extracts every field with a
          confidence score, and routes the exceptions to your team.
        </p>
      </div>

      <div className="mt-10 flex flex-1 items-start justify-center xl:justify-start">
        <DocumentPreview />
      </div>

      <div className="mt-10 grid grid-cols-1 gap-6 xl:grid-cols-[1fr_auto] xl:items-end">
        <div className="min-h-[72px]" aria-live="off">
          <div key={index} className="animate-fade-in">
            <p className="flex items-center gap-2 text-sm font-semibold text-white">
              <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
              {highlight?.title}
            </p>
            <p className="mt-1 max-w-md text-[13px] leading-relaxed text-zinc-400">{highlight?.body}</p>
          </div>
          <div className="mt-3 flex gap-1.5" aria-hidden="true">
            {HIGHLIGHTS.map((item, dot) => (
              <span
                key={item.title}
                className={`h-1 rounded-full transition-all duration-300 ${dot === index ? "w-6 bg-primary" : "w-1.5 bg-white/20"}`}
              />
            ))}
          </div>
        </div>

        <ul className="flex flex-wrap gap-2" aria-label="Platform safeguards">
          {TRUST.map(({ label, icon: Icon }) => (
            <li
              key={label}
              className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[11.5px] font-medium text-zinc-300"
            >
              <Icon className="h-3.5 w-3.5 text-zinc-400" aria-hidden="true" />
              {label}
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-8 border-t border-white/[0.06] pt-5">{footer}</div>
    </div>
  );
}

export default DocumentShowcase;
