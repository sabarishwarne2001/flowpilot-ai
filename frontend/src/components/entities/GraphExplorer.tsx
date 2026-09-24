/**
 * ARCH42-S2:graph-explorer — a force-directed view of one record's neighbourhood.
 *
 * Canvas, not SVG, and no graph library: the roadmap forbids a new dependency,
 * and 150 nodes is well inside what an O(n²) repulsion pass per frame handles.
 * The simulation cools and stops on its own; it is cancelled on unmount and
 * restarted when the graph changes. Nodes can be dragged; a click opens the
 * record. A visually hidden list gives keyboard and screen-reader users the
 * same nodes as buttons, because a canvas is not navigable.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { EntityGraph, EntityKind } from "@/types/entities";

interface GraphExplorerProps {
  readonly graph: EntityGraph;
  readonly onOpen: (entityId: string) => void;
  readonly height?: number;
}

interface SimNode {
  id: string;
  label: string;
  kind: EntityKind;
  depth: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

const KIND_COLOR: Readonly<Record<EntityKind, string>> = {
  PERSON: "#6366f1",
  ORGANIZATION: "#0ea5e9",
  ADDRESS: "#f59e0b",
  ACCOUNT: "#10b981",
  ASSET: "#a855f7",
  SHIPMENT: "#ef4444",
};

const MAX_TICKS = 360;
const RADIUS = 9;

export const GraphExplorer: React.FC<GraphExplorerProps> = ({ graph, onOpen, height = 420 }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const nodesRef = useRef<SimNode[]>([]);
  const frameRef = useRef<number | null>(null);
  const tickRef = useRef(0);
  const dragRef = useRef<{ id: string; moved: boolean } | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [width, setWidth] = useState(640);

  const edges = useMemo(() => graph.edges.map((e) => ({ ...e })), [graph]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {return undefined;}
    const observer = new ResizeObserver((entries) => {
      const w = Math.max(280, Math.floor(entries[0]?.contentRect.width ?? 640));
      setWidth(w);
    });
    observer.observe(canvas.parentElement ?? canvas);
    return () => observer.disconnect();
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) {return;}
    const ratio = window.devicePixelRatio || 1;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const byId = new Map(nodesRef.current.map((n) => [n.id, n]));
    const ink = getComputedStyle(canvas).color || "#334155";
    ctx.lineWidth = 1;
    for (const edge of edges) {
      const a = byId.get(edge.source);
      const b = byId.get(edge.target);
      if (!a || !b) {continue;}
      const active = hover === a.id || hover === b.id;
      ctx.strokeStyle = active ? ink : "rgba(148,163,184,0.55)";
      ctx.lineWidth = Math.min(4, 1 + edge.weight * 0.5);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      if (active) {
        ctx.fillStyle = ink;
        ctx.font = "10px ui-sans-serif, system-ui";
        ctx.fillText(edge.relation.toLowerCase().replace(/_/g, " "), (a.x + b.x) / 2 + 4, (a.y + b.y) / 2 - 4);
      }
    }
    for (const node of nodesRef.current) {
      const root = node.id === graph.root_id;
      ctx.beginPath();
      ctx.arc(node.x, node.y, root ? RADIUS + 4 : RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = KIND_COLOR[node.kind] ?? "#64748b";
      ctx.fill();
      if (root || hover === node.id) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = ink;
        ctx.stroke();
      }
      ctx.fillStyle = ink;
      ctx.font = `${root ? "600 12px" : "11px"} ui-sans-serif, system-ui`;
      const label = node.label.length > 28 ? `${node.label.slice(0, 27)}…` : node.label;
      ctx.fillText(label, node.x + RADIUS + 5, node.y + 4);
    }
  }, [edges, graph.root_id, height, hover, width]);

  const step = useCallback(() => {
    const nodes = nodesRef.current;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const cx = width / 2;
    const cy = height / 2;
    const cooling = Math.max(0.02, 1 - tickRef.current / MAX_TICKS);
    for (let i = 0; i < nodes.length; i += 1) {
      const a = nodes[i]!;
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = nodes[j]!;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          d2 = 0.5;
        }
        const force = 2200 / d2;
        const d = Math.sqrt(d2);
        a.vx += (dx / d) * force;
        a.vy += (dy / d) * force;
        b.vx -= (dx / d) * force;
        b.vy -= (dy / d) * force;
      }
    }
    for (const edge of edges) {
      const a = byId.get(edge.source);
      const b = byId.get(edge.target);
      if (!a || !b) {continue;}
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const pull = (d - 110) * 0.02;
      a.vx += (dx / d) * pull;
      a.vy += (dy / d) * pull;
      b.vx -= (dx / d) * pull;
      b.vy -= (dy / d) * pull;
    }
    for (const node of nodes) {
      if (node.pinned) {
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      node.vx += (cx - node.x) * 0.004;
      node.vy += (cy - node.y) * 0.004;
      node.x = Math.min(width - 12, Math.max(12, node.x + node.vx * cooling * 0.1));
      node.y = Math.min(height - 12, Math.max(12, node.y + node.vy * cooling * 0.1));
      node.vx *= 0.6;
      node.vy *= 0.6;
    }
    tickRef.current += 1;
    draw();
    frameRef.current = tickRef.current < MAX_TICKS ? requestAnimationFrame(step) : null;
  }, [draw, edges, height, width]);

  useEffect(() => {
    const previous = new Map(nodesRef.current.map((n) => [n.id, n]));
    const count = Math.max(1, graph.nodes.length);
    nodesRef.current = graph.nodes.map((node, index) => {
      const kept = previous.get(node.id);
      const angle = (index / count) * Math.PI * 2;
      const r = node.depth * 90;
      return {
        id: node.id,
        label: node.label,
        kind: node.kind,
        depth: node.depth,
        x: kept?.x ?? width / 2 + Math.cos(angle) * r,
        y: kept?.y ?? height / 2 + Math.sin(angle) * r,
        vx: 0,
        vy: 0,
        pinned: node.id === graph.root_id,
      };
    });
    const root = nodesRef.current.find((n) => n.id === graph.root_id);
    if (root) {
      root.x = width / 2;
      root.y = height / 2;
    }
    const canvas = canvasRef.current;
    if (canvas) {
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
    }
    tickRef.current = 0;
    if (frameRef.current !== null) {cancelAnimationFrame(frameRef.current);}
    frameRef.current = requestAnimationFrame(step);
    return () => {
      if (frameRef.current !== null) {cancelAnimationFrame(frameRef.current);}
      frameRef.current = null;
    };
  }, [graph, height, step, width]);

  const hit = (event: React.PointerEvent<HTMLCanvasElement>): SimNode | undefined => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    return nodesRef.current.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 <= (RADIUS + 5) ** 2);
  };

  const restart = (): void => {
    if (frameRef.current === null) {
      tickRef.current = Math.floor(MAX_TICKS / 2);
      frameRef.current = requestAnimationFrame(step);
    }
  };

  return (
    <div className="relative w-full text-foreground">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Relationship graph: ${graph.nodes.length} records, ${graph.edges.length} relationships`}
        style={{ width: "100%", height }}
        className="cursor-pointer touch-none rounded-lg border border-border/60 bg-muted/20"
        onPointerDown={(event) => {
          const node = hit(event);
          if (!node) {return;}
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { id: node.id, moved: false };
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (drag) {
            const rect = event.currentTarget.getBoundingClientRect();
            const node = nodesRef.current.find((n) => n.id === drag.id);
            if (node) {
              node.x = event.clientX - rect.left;
              node.y = event.clientY - rect.top;
              node.pinned = true;
              drag.moved = true;
              draw();
            }
            return;
          }
          const node = hit(event);
          setHover(node?.id ?? null);
        }}
        onPointerUp={() => {
          const drag = dragRef.current;
          dragRef.current = null;
          if (drag && !drag.moved) {
            onOpen(drag.id);
          } else if (drag) {
            restart();
          }
        }}
        onPointerLeave={() => setHover(null)}
      />
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {(Object.keys(KIND_COLOR) as EntityKind[]).map((kind) => (
          <span key={kind} className="inline-flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: KIND_COLOR[kind] }} />
            {kind.toLowerCase()}
          </span>
        ))}
        {graph.truncated && <span>Showing the nearest 150 records.</span>}
      </div>
      <ul className="sr-only">
        {graph.nodes.map((node) => (
          <li key={node.id}>
            <button type="button" onClick={() => onOpen(node.id)}>
              {node.label} ({node.kind.toLowerCase()}, {node.documents} documents)
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default GraphExplorer;
