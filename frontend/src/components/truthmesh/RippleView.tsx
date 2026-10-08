/**
 * Phase 2 — the ripple of a what-if: the document the condition starts on at the centre, each ring
 * one more link away. A document's size is how strongly the change reaches it (impact), its ring
 * colour its most serious effect, and each sits near the document the change came through, so a
 * branch of the ripple reads as one wedge. Click a document to read its effects.
 */
import React, { useMemo } from "react";

import type { RippleNode, Severity } from "@/types/truthmesh";
import { kindStyle, SEVERITY_STYLE } from "@/components/truthmesh/shared";

interface RippleViewProps {
  readonly nodes: readonly RippleNode[];
  readonly selected: string | null;
  readonly onSelect: (workItemId: string) => void;
  readonly size?: number;
}

const SEVERITY_STROKE: Readonly<Record<Severity, string>> = {
  CRITICAL: "#dc2626",
  HIGH: "#ea580c",
  MEDIUM: "#d97706",
  LOW: "#0284c7",
};
const ORDER: readonly Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

export const worstSeverity = (node: RippleNode): Severity | null =>
  ORDER.find((s) => node.effects.some((e) => e.severity === s)) ?? null;

export const RippleView: React.FC<RippleViewProps> = ({ nodes, selected, onSelect, size = 460 }) => {
  const placed = useMemo(() => {
    const center = size / 2;
    const maxDepth = Math.max(1, ...nodes.map((n) => n.depth));
    const ring = (center - 46) / maxDepth;
    const angles = new Map<string, number>();
    const out: { node: RippleNode; x: number; y: number; r: number; parent: string | null }[] = [];
    for (let depth = 0; depth <= maxDepth; depth += 1) {
      const layer = nodes.filter((n) => n.depth === depth);
      const keyed = layer
        .map((n) => ({ n, parentAngle: angles.get(n.path[n.path.length - 2] ?? "") ?? 0 }))
        .sort((a, b) => a.parentAngle - b.parentAngle || b.n.impact - a.n.impact);
      keyed.forEach(({ n }, i) => {
        const angle = depth === 0 ? 0 : -Math.PI / 2 + (2 * Math.PI * (i + 0.5)) / keyed.length;
        angles.set(n.work_item_id, angle);
        const radius = depth * ring;
        out.push({
          node: n,
          x: center + radius * Math.cos(angle),
          y: center + radius * Math.sin(angle),
          r: depth === 0 ? 24 : 9 + 13 * n.impact,
          parent: n.path.length > 1 ? n.path[n.path.length - 2]! : null,
        });
      });
    }
    return { out, ring, maxDepth, center };
  }, [nodes, size]);

  const byId = new Map(placed.out.map((p) => [p.node.work_item_id, p]));

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-auto w-full max-w-[560px]" role="group" aria-label="Ripple of the simulated change">
      {Array.from({ length: placed.maxDepth }, (_, i) => (
        <g key={i}>
          <circle cx={placed.center} cy={placed.center} r={(i + 1) * placed.ring} fill="none" className="stroke-border" strokeDasharray="3 5" />
          <text x={placed.center + 4} y={placed.center - (i + 1) * placed.ring + 12} className="fill-muted-foreground text-[10px]">
            {i + 1} link{i === 0 ? "" : "s"} away
          </text>
        </g>
      ))}
      {placed.out.map((p) => {
        const parent = p.parent ? byId.get(p.parent) : null;
        if (!parent) {
          return null;
        }
        return (
          <line
            key={`e-${p.node.work_item_id}`}
            x1={parent.x}
            y1={parent.y}
            x2={p.x}
            y2={p.y}
            className="stroke-primary"
            strokeOpacity={0.25 + 0.6 * p.node.impact}
            strokeWidth={1 + 3 * p.node.impact}
          />
        );
      })}
      {placed.out.map((p) => {
        const severity = worstSeverity(p.node);
        const { color } = kindStyle(p.node.kind);
        const active = selected === p.node.work_item_id;
        return (
          <g
            key={p.node.work_item_id}
            role="button"
            tabIndex={0}
            aria-label={`${p.node.kind_label} ${p.node.title}, ${p.node.depth} links away, impact ${Math.round(p.node.impact * 100)}%, ${p.node.effects.length} effects`}
            className="cursor-pointer outline-none"
            onClick={() => onSelect(p.node.work_item_id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(p.node.work_item_id);
              }
            }}
          >
            <circle cx={p.x} cy={p.y} r={p.r} fill={color} fillOpacity={p.node.depth === 0 ? 0.9 : 0.75} />
            <circle
              cx={p.x}
              cy={p.y}
              r={p.r + 3.5}
              fill="none"
              stroke={severity ? SEVERITY_STROKE[severity] : "transparent"}
              strokeWidth={active ? 4 : 2.5}
            />
            {active ? <circle cx={p.x} cy={p.y} r={p.r + 8} fill="none" className="stroke-primary" strokeWidth={1.5} /> : null}
            <text
              x={p.x}
              y={p.y + p.r + 15}
              textAnchor="middle"
              className="fill-foreground text-[10.5px] font-semibold"
              paintOrder="stroke"
              stroke="hsl(var(--card))"
              strokeWidth={3.5}
            >
              {p.node.title.length > 22 ? `${p.node.title.slice(0, 21)}…` : p.node.title}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

export const SeverityLegend: React.FC = () => (
  <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
    {ORDER.map((s) => (
      <span key={s} className="inline-flex items-center gap-1">
        <span className={`h-2 w-2 rounded-full ${SEVERITY_STYLE[s].dot}`} aria-hidden />
        {s.toLowerCase()} effect
      </span>
    ))}
  </div>
);

export default RippleView;
