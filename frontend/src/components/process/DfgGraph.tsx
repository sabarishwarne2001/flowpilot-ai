/**
 * ARCH49-S2:dfg-graph — the directly-follows graph, drawn as SVG.
 *
 * Activities are laid out in columns by their breadth-first distance from the
 * activities traces start with; an edge's width is its share of the busiest
 * edge, its label the number of times one activity directly followed the other
 * and the median wait between them. An edge that runs backwards (rework) is
 * drawn as an arc in its own lane below (or, within one column, on the left).
 * Everything is text from the server's closed activity
 * vocabulary — never document content.
 */
import React, { useMemo } from "react";

import { activityLabel, duration } from "@/components/process/common";
import { HINT } from "@/components/ui/primitives";
import type { DfgGraph as Graph } from "@/types/process";

const COLUMN = 300;
const ROW = 72;
const NODE_W = 180;
const NODE_H = 40;
const PAD = 24;
const LEFT = 84; // room for the arcs between activities in one column
const BACK_STEP = 18; // each backward arc runs in its own lane below the graph
const MAX_NODES = 28;

interface Placed {
  readonly activity: string;
  readonly events: number;
  readonly x: number;
  readonly y: number;
  readonly rank: number;
}

const layout = (graph: Graph): { nodes: Map<string, Placed>; width: number; bottom: number } => {
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
  const columns = new Map<number, string[]>();
  for (const activity of kept) {
    const r = rank.get(activity.activity) ?? 0;
    columns.set(r, [...(columns.get(r) ?? []), activity.activity]);
  }
  const nodes = new Map<string, Placed>();
  let tallest = 1;
  for (const [r, members] of columns) {
    tallest = Math.max(tallest, members.length);
    members.forEach((activity, i) => {
      const events = kept.find((a) => a.activity === activity)?.events ?? 0;
      nodes.set(activity, { activity, events, rank: r, x: LEFT + r * COLUMN, y: PAD + i * ROW });
    });
  }
  const width = LEFT + PAD + Math.max(...columns.keys(), 0) * COLUMN + NODE_W + 60;
  return { nodes, width, bottom: PAD + (tallest - 1) * ROW + NODE_H };
};

/** A point on the cubic Bézier p0 → p3 at t (labels sit near the source, so edges that cross keep apart). */
const at = (t: number, p0: number, p1: number, p2: number, p3: number): number =>
  (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3;

export const DfgGraph: React.FC<{ readonly graph: Graph }> = ({ graph }) => {
  const { nodes, width, bottom } = useMemo(() => layout(graph), [graph]);
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
      d = `M ${a.x + NODE_W - 24} ${a.y} C ${a.x + NODE_W + 34} ${a.y - 34}, ${a.x + NODE_W + 34} ${a.y + NODE_H + 34}, ${a.x + NODE_W - 24} ${a.y + NODE_H}`;
      lx = a.x + NODE_W + 34;
      ly = a.y + NODE_H / 2 + 3;
    } else if (b.rank > a.rank) {
      kind = "forward";
      const x1 = a.x + NODE_W;
      const y1 = a.y + NODE_H / 2;
      const x2 = b.x - 2;
      const y2 = b.y + NODE_H / 2;
      const mid = (x1 + x2) / 2;
      d = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
      lx = at(0.3, x1, mid, mid, x2);
      ly = at(0.3, y1, y1, y2, y2) - 4;
    } else if (b.rank === a.rank) {
      // between activities in one column: an arc on the left
      kind = "back";
      const x = a.x - 2;
      const y1 = a.y + NODE_H / 2 + (b.y > a.y ? 6 : -6);
      const y2 = b.y + NODE_H / 2 + (b.y > a.y ? -6 : 6);
      const bulge = Math.min(64, 26 + Math.abs(y2 - y1) / 5);
      d = `M ${x} ${y1} C ${x - bulge} ${y1}, ${x - bulge} ${y2}, ${x} ${y2}`;
      lx = x - bulge * 0.75;
      ly = (y1 + y2) / 2 + (b.y > a.y ? -4 : 12);
    } else {
      // rework: back to an earlier column, in its own lane below the graph
      kind = "back";
      lanes += 1;
      const x1 = a.x + NODE_W / 2 + 12;
      const x2 = b.x + NODE_W / 2 - 12;
      const dip = bottom + 20 + lanes * BACK_STEP;
      d = `M ${x1} ${a.y + NODE_H} C ${x1} ${dip}, ${x2} ${dip}, ${x2} ${b.y + NODE_H + 2}`;
      lx = (x1 + x2) / 2;
      ly = at(0.5, a.y + NODE_H, dip, dip, b.y + NODE_H) + 4;
    }
    return { edge, d, lx, ly, kind };
  });
  const height = bottom + PAD + 40 + lanes * BACK_STEP;
  return (
    <div className="overflow-auto rounded-lg border border-border bg-background">
      <svg
        role="img"
        aria-label={`Directly-follows graph: ${graph.activities.length} activities, ${graph.edges.length} transitions`}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="text-foreground"
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
        {[...nodes.values()].map((node) => (
          <g key={node.activity}>
            <title>{`${activityLabel(node.activity)}: ${node.events} event(s)`}</title>
            <rect
              x={node.x}
              y={node.y}
              width={NODE_W}
              height={NODE_H}
              rx={8}
              strokeWidth={starts.has(node.activity) || ends.has(node.activity) ? 2 : 1}
              className={`fill-card ${starts.has(node.activity) ? "stroke-emerald-500" : ends.has(node.activity) ? "stroke-sky-500" : "stroke-border"}`}
            />
            <text x={node.x + 10} y={node.y + 17} className="fill-foreground text-[11px] font-semibold">
              {activityLabel(node.activity).slice(0, 30)}
            </text>
            <text x={node.x + 10} y={node.y + 32} className="fill-muted-foreground text-[10px]">
              {node.events} event(s)
            </text>
          </g>
        ))}
        {/* labels last, haloed, so neither an edge nor a node hides them */}
        {drawn.map(({ edge, lx, ly }) => (
          <text key={`${edge.source}->${edge.target}:label`} x={lx} y={ly} textAnchor="middle" strokeWidth={3}
            style={{ paintOrder: "stroke" }} className="fill-muted-foreground stroke-background text-[10px]">
            {edge.count} · {duration(edge.seconds.median)}
          </text>
        ))}
      </svg>
      <p className={`${HINT} px-3 pb-2`}>
        Green: where traces start · blue: where they end · amber: going back (rework), below the graph or on the left
        within a column. Widths are shares of the busiest transition; labels are counts and the median wait.
        {graph.activities.length > MAX_NODES ? ` The ${MAX_NODES} busiest of ${graph.activities.length} activities are shown.` : ""}
      </p>
    </div>
  );
};

export default DfgGraph;
