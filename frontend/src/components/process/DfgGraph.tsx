/**
 * ARCH49-S2:dfg-graph — the directly-follows graph, drawn as SVG.
 *
 * Activities are laid out in rows by their breadth-first distance from the activities traces start
 * with, top to bottom (Phase 2: it ran left to right, and a linear process of ten steps was 3,000px
 * wide with its labels cut off; it now grows down the page and fits the panel's width). An edge's
 * width is its share of the busiest edge, its label the number of times one activity directly
 * followed the other and the median wait between them. An edge that runs backwards (rework) is
 * drawn in its own lane on the right; one between activities in a row arcs above them.
 * Everything is text from the server's closed activity vocabulary — never document content.
 */
import React, { useMemo } from "react";

import { activityLabel, duration } from "@/components/process/common";
import { HINT } from "@/components/ui/primitives";
import type { DfgGraph as Graph } from "@/types/process";

const NODE_W = 196;
const NODE_H = 46;
const GAP_X = 22;
const ROW = NODE_H + 58;
const PAD = 28;
const BACK_STEP = 16; // each backward edge runs in its own lane on the right
const MAX_NODES = 28;

interface Placed {
  readonly activity: string;
  readonly events: number;
  readonly x: number;
  readonly y: number;
  readonly rank: number;
}

const layout = (graph: Graph): { nodes: Map<string, Placed>; right: number; bottom: number } => {
  const kept = [...graph.activities].sort((a, b) => b.events - a.events).slice(0, MAX_NODES);
  const names = new Set(kept.map((a) => a.activity));
  const next = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (names.has(edge.source) && names.has(edge.target)) {
      next.set(edge.source, [...(next.get(edge.source) ?? []), edge.target]);
    }
  }
  const rank = new Map<string, number>();
  const queue: string[] = [];
  for (const start of graph.starts) {
    if (names.has(start.activity) && !rank.has(start.activity)) {
      rank.set(start.activity, 0);
      queue.push(start.activity);
    }
  }
  while (queue.length) {
    const current = queue.shift() as string;
    for (const target of next.get(current) ?? []) {
      if (!rank.has(target)) {
        rank.set(target, (rank.get(current) ?? 0) + 1);
        queue.push(target);
      }
    }
  }
  const rows = new Map<number, string[]>();
  for (const activity of kept) {
    const r = rank.get(activity.activity) ?? 0;
    rows.set(r, [...(rows.get(r) ?? []), activity.activity]);
  }
  const widest = Math.max(1, ...[...rows.values()].map((members) => members.length));
  const span = widest * NODE_W + (widest - 1) * GAP_X;
  const nodes = new Map<string, Placed>();
  for (const [r, members] of rows) {
    const rowWidth = members.length * NODE_W + (members.length - 1) * GAP_X;
    const x0 = PAD + (span - rowWidth) / 2;
    members.forEach((activity, i) => {
      const events = kept.find((a) => a.activity === activity)?.events ?? 0;
      nodes.set(activity, { activity, events, rank: r, x: x0 + i * (NODE_W + GAP_X), y: PAD + r * ROW });
    });
  }
  const deepest = Math.max(0, ...rows.keys());
  return { nodes, right: PAD + span, bottom: PAD + deepest * ROW + NODE_H };
};

/** A point on the cubic Bézier p0 → p3 at t. */
const at = (t: number, p0: number, p1: number, p2: number, p3: number): number =>
  (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3;

const clip = (text: string, max: number): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

export const DfgGraph: React.FC<{ readonly graph: Graph }> = ({ graph }) => {
  const { nodes, right, bottom } = useMemo(() => layout(graph), [graph]);
  const busiest = Math.max(1, ...graph.edges.map((e) => e.count));
  if (graph.activities.length === 0) {
    return <p className={HINT}>No events for this object type in the window yet. The log fills on every sweep.</p>;
  }
  const edges = graph.edges.filter((e) => nodes.has(e.source) && nodes.has(e.target));
  const starts = new Set(graph.starts.map((s) => s.activity));
  const ends = new Set(graph.ends.map((s) => s.activity));
  let lanes = 0;
  const drawn = edges.map((edge) => {
    const a = nodes.get(edge.source) as Placed;
    const b = nodes.get(edge.target) as Placed;
    let d: string;
    let lx: number;
    let ly: number;
    let kind: "forward" | "back" | "self";
    if (edge.source === edge.target) {
      kind = "self";
      const x = a.x + NODE_W;
      d = `M ${x} ${a.y + 10} C ${x + 34} ${a.y - 14}, ${x + 34} ${a.y + NODE_H + 14}, ${x} ${a.y + NODE_H - 10}`;
      lx = x + 30;
      ly = a.y + NODE_H / 2 + 3;
    } else if (b.rank > a.rank) {
      kind = "forward";
      const x1 = a.x + NODE_W / 2;
      const y1 = a.y + NODE_H;
      const x2 = b.x + NODE_W / 2;
      const y2 = b.y - 2;
      const mid = (y1 + y2) / 2;
      d = `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
      lx = at(0.45, x1, x1, x2, x2) + 4;
      ly = at(0.45, y1, mid, mid, y2) + 3;
    } else if (b.rank === a.rank) {
      // between activities in one row: an arc above them
      kind = "back";
      const x1 = a.x + NODE_W / 2 + (b.x > a.x ? 8 : -8);
      const x2 = b.x + NODE_W / 2 + (b.x > a.x ? -8 : 8);
      const lift = Math.min(40, 18 + Math.abs(x2 - x1) / 10);
      d = `M ${x1} ${a.y} C ${x1} ${a.y - lift}, ${x2} ${b.y - lift}, ${x2} ${b.y - 2}`;
      lx = (x1 + x2) / 2;
      ly = a.y - lift * 0.75;
    } else {
      // rework: back to an earlier row, in its own lane on the right
      kind = "back";
      lanes += 1;
      const lane = right + 20 + lanes * BACK_STEP;
      const y1 = a.y + NODE_H / 2 + 6;
      const y2 = b.y + NODE_H / 2 - 6;
      d = `M ${a.x + NODE_W} ${y1} C ${lane} ${y1}, ${lane} ${y2}, ${b.x + NODE_W + 2} ${y2}`;
      lx = at(0.5, a.x + NODE_W, lane, lane, b.x + NODE_W) + 4;
      ly = (y1 + y2) / 2;
    }
    return { edge, d, lx, ly, kind };
  });
  const width = right + PAD + 40 + lanes * BACK_STEP;
  const height = bottom + PAD;
  return (
    <div className="max-h-[75vh] overflow-auto overscroll-contain rounded-lg border border-border bg-background">
      <svg
        role="img"
        aria-label={`Directly-follows graph: ${graph.activities.length} activities, ${graph.edges.length} transitions`}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="mx-auto text-foreground"
      >
        <defs>
          {/* userSpaceOnUse: the head keeps its size whatever the edge's width */}
          <marker id="dfg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
        </defs>
        {drawn.map(({ edge, d, kind }) => (
          <g key={`${edge.source}->${edge.target}`}>
            <title>{`${activityLabel(edge.source)} → ${activityLabel(edge.target)}: ${edge.count} time(s), median wait ${duration(edge.seconds.median)}`}</title>
            <path d={d} fill="none" strokeWidth={1 + (5 * edge.count) / busiest} markerEnd="url(#dfg-arrow)"
              className={kind === "forward" ? "stroke-primary/50" : "stroke-amber-500/60"} />
          </g>
        ))}
        {[...nodes.values()].map((node) => {
          const [name, outcome] = activityLabel(node.activity).split(" · ");
          return (
            <g key={node.activity}>
              <title>{`${activityLabel(node.activity)}: ${node.events} event(s)`}</title>
              <rect
                x={node.x}
                y={node.y}
                width={NODE_W}
                height={NODE_H}
                rx={9}
                strokeWidth={starts.has(node.activity) || ends.has(node.activity) ? 2 : 1}
                className={`fill-card ${starts.has(node.activity) ? "stroke-emerald-500" : ends.has(node.activity) ? "stroke-sky-500" : "stroke-border"}`}
              />
              <text x={node.x + 11} y={node.y + 19} className="fill-foreground text-[11.5px] font-semibold">
                {clip(name ?? "", 30)}
              </text>
              <text x={node.x + 11} y={node.y + 35} className="fill-muted-foreground text-[10.5px]">
                {outcome ? `${outcome} · ` : ""}
                {node.events} event{node.events === 1 ? "" : "s"}
              </text>
            </g>
          );
        })}
        {/* labels last, haloed, so neither an edge nor a node hides them */}
        {drawn.map(({ edge, lx, ly }) => (
          <text key={`${edge.source}->${edge.target}:label`} x={lx} y={ly} strokeWidth={3}
            style={{ paintOrder: "stroke" }} className="fill-muted-foreground stroke-background text-[10px]">
            {edge.count} · {duration(edge.seconds.median)}
          </text>
        ))}
      </svg>
      <p className={`${HINT} px-3 pb-2`}>
        Read top to bottom. Green: where traces start · blue: where they end · amber: going back (rework), on
        the right, or between activities in one row, above them. Widths are shares of the busiest transition;
        labels are counts and the median wait.
        {graph.activities.length > MAX_NODES ? ` The ${MAX_NODES} busiest of ${graph.activities.length} activities are shown.` : ""}
      </p>
    </div>
  );
};

export default DfgGraph;
