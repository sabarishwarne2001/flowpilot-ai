/**
 * Phase 2 — the TruthMesh graph: documents laid out by authority, linked by what ties them.
 *
 * A layered layout, not a force simulation: agreements on top, orders and statements of work below
 * them, receipts and waybills, then invoices and claims at the bottom, so "what authorises what"
 * reads top to bottom at a glance and the picture is the same every time it opens. Within a layer,
 * documents are ordered by the mean position of their neighbours (a few barycentric sweeps), which
 * keeps most links short and uncrossed. A layer wider than the panel wraps onto further rows, so
 * the graph opens at a readable size instead of shrinking to fit one long line.
 *
 * A link that only restates a longer chain (invoice → agreement when invoice → order → agreement is
 * already drawn) is hidden until "Show implied links" is on: it is still in the mesh, and in the
 * twin, it just adds lines without adding meaning to the picture.
 *
 * Pan by dragging the background, zoom with the wheel or the buttons, hover a document to see its
 * neighbourhood (everything else dims), click or press Enter to open its twin. Colour is never alone:
 * every node says its kind in words, its risk as a number and its conflicts as a count.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";

import type { MeshLink, MeshNode } from "@/types/truthmesh";
import { bandOf, kindStyle, money, RISK_COLOR } from "@/components/truthmesh/shared";

interface MeshGraphProps {
  readonly nodes: readonly MeshNode[];
  readonly links: readonly MeshLink[];
  readonly selected?: string | null;
  readonly onSelect: (workItemId: string) => void;
  readonly height?: number;
  /** Ids to emphasise (a conflict's documents); the rest dim. */
  readonly emphasis?: ReadonlySet<string> | null;
}

const NODE_W = 176;
const NODE_H = 48;
const GAP_X = 26;
const ROW_H = 64;
const LAYER_GAP = 46;
const MAX_PER_ROW = 8;
const MIN_PER_ROW = 2;
const MARGIN = 36;

/** How many documents fit in one row of a panel this wide at full size. */
export const perRowFor = (panelWidth: number): number =>
  Math.min(MAX_PER_ROW, Math.max(MIN_PER_ROW, Math.floor((panelWidth - 2 * MARGIN + GAP_X) / (NODE_W + GAP_X))));

/**
 * The directed links another chain of directed links already implies (a transitive reduction): a → c
 * is implied when c can be reached from a through at least one other document. Undirected links are
 * never implied.
 */
export function impliedLinkIds(links: readonly MeshLink[]): Set<string> {
  const directed = links.filter((link) => link.directed && link.status !== "REJECTED");
  const out = new Map<string, MeshLink[]>();
  for (const link of directed) {
    out.set(link.source, [...(out.get(link.source) ?? []), link]);
  }
  const implied = new Set<string>();
  for (const link of directed) {
    const seen = new Set<string>([link.source]);
    const queue = (out.get(link.source) ?? []).filter((other) => other.id !== link.id).map((other) => other.target);
    let found = false;
    while (queue.length > 0 && !found) {
      const at = queue.shift()!;
      if (seen.has(at)) {
        continue;
      }
      seen.add(at);
      for (const next of out.get(at) ?? []) {
        if (next.target === link.target) {
          found = true;
          break;
        }
        queue.push(next.target);
      }
    }
    if (found) {
      implied.add(link.id);
    }
  }
  return implied;
}

interface Placed {
  readonly node: MeshNode;
  readonly x: number;
  readonly y: number;
}

function layout(
  nodes: readonly MeshNode[],
  links: readonly MeshLink[],
  perRow: number,
): { placed: Map<string, Placed>; width: number; height: number } {
  const ranks = [...new Set(nodes.map((n) => n.rank))].sort((a, b) => b - a);
  const layers: MeshNode[][] = ranks.map((rank) =>
    nodes.filter((n) => n.rank === rank).sort((a, b) => b.risk_score - a.risk_score || a.title.localeCompare(b.title)),
  );
  const neighbours = new Map<string, string[]>();
  for (const link of links) {
    neighbours.set(link.source, [...(neighbours.get(link.source) ?? []), link.target]);
    neighbours.set(link.target, [...(neighbours.get(link.target) ?? []), link.source]);
  }
  const order = new Map<string, number>();
  const index = () => layers.forEach((layer) => layer.forEach((n, i) => order.set(n.work_item_id, i / Math.max(1, layer.length - 1))));
  index();
  for (let sweep = 0; sweep < 4; sweep += 1) {
    const sequence = sweep % 2 === 0 ? layers.map((_, i) => i) : layers.map((_, i) => layers.length - 1 - i);
    for (const i of sequence) {
      const layer = layers[i]!;
      const score = (n: MeshNode) => {
        const near = (neighbours.get(n.work_item_id) ?? []).map((id) => order.get(id)).filter((v): v is number => v !== undefined);
        return near.length ? near.reduce((a, b) => a + b, 0) / near.length : order.get(n.work_item_id) ?? 0.5;
      };
      layer.sort((a, b) => score(a) - score(b));
      index();
    }
  }
  const widest = Math.min(perRow, Math.max(1, ...layers.map((l) => l.length)));
  const width = MARGIN * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const placed = new Map<string, Placed>();
  let y = MARGIN;
  for (const layer of layers) {
    for (let start = 0; start < layer.length; start += perRow) {
      const row = layer.slice(start, start + perRow);
      const rowWidth = row.length * NODE_W + (row.length - 1) * GAP_X;
      let x = (width - rowWidth) / 2;
      for (const node of row) {
        placed.set(node.work_item_id, { node, x, y });
        x += NODE_W + GAP_X;
      }
      y += ROW_H;
    }
    y += LAYER_GAP;
  }
  return { placed, width, height: y - LAYER_GAP + MARGIN - (ROW_H - NODE_H) };
}

function edgePath(a: Placed, b: Placed): string {
  const ax = a.x + NODE_W / 2;
  const bx = b.x + NODE_W / 2;
  if (Math.abs(a.y - b.y) < 1) {
    const top = a.y - 18;
    return `M ${ax} ${a.y} C ${ax} ${top}, ${bx} ${top}, ${bx} ${b.y}`;
  }
  const [upper, lower, ux, lx] = a.y < b.y ? [a, b, ax, bx] : [b, a, bx, ax];
  const y1 = upper.y + NODE_H;
  const y2 = lower.y;
  const mid = (y1 + y2) / 2;
  return `M ${ux} ${y1} C ${ux} ${mid}, ${lx} ${mid}, ${lx} ${y2}`;
}

const EDGE_STYLE = (link: MeshLink): { dash?: string; opacity: number } => {
  if (link.status === "REJECTED") {
    return { dash: "2 4", opacity: 0.25 };
  }
  if (link.relation === "VERSION_OF") {
    return { dash: "6 4", opacity: 0.9 };
  }
  if (link.relation === "SHARES_PARTY" || link.relation === "RELATES_TO" || link.relation === "RECONCILES_WITH") {
    return { dash: "2 3", opacity: 0.7 };
  }
  return { opacity: 0.85 };
};

export const MeshGraph: React.FC<MeshGraphProps> = ({ nodes, links: allLinks, selected = null, onSelect, height = 520, emphasis = null }) => {
  const [showImplied, setShowImplied] = useState(false);
  const implied = useMemo(() => impliedLinkIds(allLinks), [allLinks]);
  const links = useMemo(
    () => (showImplied ? allLinks : allLinks.filter((link) => !implied.has(link.id))),
    [allLinks, implied, showImplied],
  );
  const [perRow, setPerRow] = useState(MAX_PER_ROW);
  const { placed, width, height: contentHeight } = useMemo(() => layout(nodes, links, perRow), [nodes, links, perRow]);
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver(([entry]) => {
      const panel = entry?.contentRect.width ?? 0;
      if (panel > 0) {
        setPerRow(perRowFor(panel));
      }
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);

  const fit = useCallback(() => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box || box.width === 0) {
      return;
    }
    // Fit the width; fit the height too unless that would make the text too small to read, in
    // which case open at the top and let the reader pan down.
    const byWidth = Math.min(1.1, box.width / width);
    const k = Math.min(byWidth, Math.max(0.6, box.height / contentHeight));
    const tall = contentHeight * k > box.height;
    setView({ k, x: (box.width - width * k) / 2, y: tall ? 8 : (box.height - contentHeight * k) / 2 });
  }, [width, contentHeight]);

  useEffect(() => {
    fit();
  }, [fit]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) {
      return undefined;
    }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = svg.getBoundingClientRect();
      const px = event.clientX - box.left;
      const py = event.clientY - box.top;
      setView((current) => {
        const k = Math.min(2.5, Math.max(0.25, current.k * (event.deltaY < 0 ? 1.12 : 1 / 1.12)));
        return { k, x: px - ((px - current.x) * k) / current.k, y: py - ((py - current.y) * k) / current.k };
      });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const focus = hover ?? selected;
  const near = useMemo(() => {
    if (emphasis && emphasis.size > 0) {
      return emphasis;
    }
    if (!focus) {
      return null;
    }
    const set = new Set<string>([focus]);
    for (const link of links) {
      if (link.source === focus) {
        set.add(link.target);
      }
      if (link.target === focus) {
        set.add(link.source);
      }
    }
    return set;
  }, [focus, links, emphasis]);

  const zoom = (factor: number) =>
    setView((current) => {
      const box = svgRef.current?.getBoundingClientRect();
      const cx = (box?.width ?? 600) / 2;
      const cy = (box?.height ?? height) / 2;
      const k = Math.min(2.5, Math.max(0.25, current.k * factor));
      return { k, x: cx - ((cx - current.x) * k) / current.k, y: cy - ((cy - current.y) * k) / current.k };
    });

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted-foreground" style={{ height }}>
        No documents in the mesh yet.
      </div>
    );
  }

  const kinds = [...new Map(nodes.map((n) => [n.kind, n.kind_label])).entries()];

  return (
    <div className="relative overflow-hidden rounded-xl border border-border/70 bg-[radial-gradient(circle_at_1px_1px,hsl(var(--border))_1px,transparent_0)] [background-size:18px_18px]" style={{ height }}>
      <svg
        ref={svgRef}
        width="100%"
        height="100%"
        role="group"
        aria-label={`Document graph: ${nodes.length} documents, ${links.length} links shown`}
        className="cursor-grab touch-none select-none active:cursor-grabbing"
        onPointerDown={(event) => {
          if ((event.target as Element).closest("[data-node]")) {
            return;
          }
          drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
          (event.currentTarget as Element).setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (drag.current) {
            const d = drag.current;
            setView((current) => ({ ...current, x: d.vx + event.clientX - d.x, y: d.vy + event.clientY - d.y }));
          }
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
      >
        <defs>
          <marker id="tm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
          <marker id="tm-arrow-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-primary" />
          </marker>
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {links.map((link) => {
            const a = placed.get(link.source);
            const b = placed.get(link.target);
            if (!a || !b) {
              return null;
            }
            const hot = Boolean(near && near.has(link.source) && near.has(link.target) && (focus ? link.source === focus || link.target === focus : true));
            const style = EDGE_STYLE(link);
            const dim = near !== null && !hot;
            const path = edgePath(a, b);
            return (
              <g key={link.id} opacity={dim ? 0.12 : style.opacity}>
                <path
                  d={path}
                  fill="none"
                  className={hot ? "stroke-primary" : "stroke-muted-foreground/70"}
                  strokeWidth={1 + 2.2 * link.strength}
                  strokeDasharray={style.dash}
                  markerEnd={link.directed ? (hot ? "url(#tm-arrow-hot)" : "url(#tm-arrow)") : undefined}
                />
                {hot && focus ? (
                  <text
                    x={(a.x + b.x) / 2 + NODE_W / 2}
                    y={(a.y + b.y) / 2 + NODE_H / 2}
                    textAnchor="middle"
                    className="fill-primary text-[10.5px] font-semibold"
                    paintOrder="stroke"
                    stroke="hsl(var(--background))"
                    strokeWidth={4}
                  >
                    {link.relation_label}
                  </text>
                ) : null}
              </g>
            );
          })}
          {[...placed.values()].map(({ node, x, y }) => {
            const { color, Icon } = kindStyle(node.kind);
            const band = bandOf(node.risk_score);
            const dim = near !== null && !near.has(node.work_item_id);
            const isSelected = selected === node.work_item_id;
            const conflicts = node.open_conflicts ?? 0;
            const sub = node.amount_micros !== null ? `${node.kind_label} · ${money(node.amount_micros, node.currency)}` : node.kind_label;
            return (
              <g
                key={node.work_item_id}
                data-node
                role="button"
                tabIndex={0}
                aria-label={`${node.kind_label} ${node.title}, risk ${Math.round(node.risk_score)}, ${conflicts} open conflict${conflicts === 1 ? "" : "s"}`}
                transform={`translate(${x} ${y})`}
                opacity={dim ? 0.25 : 1}
                className="cursor-pointer outline-none [&:focus-visible>rect:first-child]:stroke-primary"
                onMouseEnter={() => setHover(node.work_item_id)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(node.work_item_id)}
                onBlur={() => setHover(null)}
                onClick={() => onSelect(node.work_item_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(node.work_item_id);
                  }
                }}
              >
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={10}
                  className={isSelected ? "fill-card stroke-primary" : "fill-card stroke-border"}
                  strokeWidth={isSelected ? 2.2 : 1.2}
                  filter="drop-shadow(0 1px 2px rgb(0 0 0 / 0.08))"
                />
                <rect width={5} height={NODE_H - 12} x={6} y={6} rx={2.5} fill={color} />
                <foreignObject x={16} y={7} width={NODE_W - 50} height={NODE_H - 10}>
                  <div className="flex h-full min-w-0 flex-col justify-center leading-tight">
                    <span className="flex min-w-0 items-center gap-1 text-[12px] font-semibold text-foreground">
                      <Icon className="h-3 w-3 shrink-0" style={{ color }} aria-hidden />
                      <span className="truncate">{node.title}</span>
                    </span>
                    <span className="truncate text-[10.5px] text-muted-foreground">{sub}</span>
                  </div>
                </foreignObject>
                <circle cx={NODE_W - 20} cy={NODE_H / 2} r={11} fill="none" stroke={RISK_COLOR[band].stroke} strokeWidth={2.5} opacity={node.risk_score > 0 ? 1 : 0.35} />
                <text x={NODE_W - 20} y={NODE_H / 2} textAnchor="middle" dominantBaseline="central" className="fill-foreground text-[9.5px] font-bold">
                  {Math.round(node.risk_score)}
                </text>
                {conflicts > 0 ? (
                  <g transform={`translate(${NODE_W - 6} -4)`}>
                    <circle r={8} className="fill-red-500" />
                    <text textAnchor="middle" dominantBaseline="central" className="fill-white text-[9px] font-bold">
                      {conflicts}
                    </text>
                  </g>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>

      {implied.size > 0 ? (
        <label className="absolute left-3 top-3 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border bg-card/95 px-2 py-1 text-[11px] text-muted-foreground shadow-sm backdrop-blur hover:text-foreground">
          <input
            type="checkbox"
            className="h-3 w-3 accent-[hsl(var(--primary))]"
            checked={showImplied}
            onChange={(event) => setShowImplied(event.target.checked)}
            data-testid="mesh-show-implied"
          />
          Show implied links ({implied.size})
        </label>
      ) : null}

      <div className="absolute right-3 top-3 flex flex-col overflow-hidden rounded-lg border border-border bg-card/95 shadow-sm backdrop-blur">
        <button type="button" onClick={() => zoom(1.2)} aria-label="Zoom in" className="p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button type="button" onClick={() => zoom(1 / 1.2)} aria-label="Zoom out" className="border-t border-border p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
          <Minus className="h-3.5 w-3.5" />
        </button>
        <button type="button" onClick={fit} aria-label="Fit to view" className="border-t border-border p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 right-14 flex flex-wrap gap-x-3 gap-y-1 rounded-lg bg-card/90 px-2.5 py-1.5 text-[10.5px] text-muted-foreground shadow-sm backdrop-blur">
        {kinds.map(([kind, label]) => (
          <span key={kind} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: kindStyle(kind).color }} aria-hidden />
            {label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1">
          <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" className="stroke-muted-foreground" strokeWidth="2" /></svg>
          depends on
        </span>
        <span className="inline-flex items-center gap-1">
          <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" className="stroke-muted-foreground" strokeWidth="2" strokeDasharray="5 3" /></svg>
          version
        </span>
        <span className="inline-flex items-center gap-1">
          <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" className="stroke-muted-foreground" strokeWidth="2" strokeDasharray="2 3" /></svg>
          related
        </span>
      </div>
    </div>
  );
};

export default MeshGraph;
