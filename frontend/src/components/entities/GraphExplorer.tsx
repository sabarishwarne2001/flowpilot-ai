/**
 * ARCH42-S2:graph-explorer — a force-directed view of one record's neighbourhood.
 *
 * Canvas, not SVG, and no graph library: the roadmap forbids a new dependency,
 * and 150 nodes is well inside what an O(n²) repulsion pass per frame handles.
 * The simulation cools and stops on its own; it is cancelled on unmount and
 * restarted when the graph changes. Nodes can be dragged; a click opens the
 * record. A visually hidden list gives keyboard and screen-reader users the
 * same nodes as buttons, because a canvas is not navigable.
 *
 * Phase 1: labels carry a halo in the surface colour so edges never run through
 * them; "Find a record" dims every other node; hover and search only redraw
 * (they used to restart the layout, so the graph shifted under the pointer).
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
  const hoverRef = useRef<string | null>(null);
  const matchRef = useRef<ReadonlySet<string> | null>(null);
  const [query, setQuery] = useState("");
  const [width, setWidth] = useState(640);

  const matches = useMemo(() => {
    const text = query.trim().toLowerCase();
    if (!text) {return null;}
    return new Set(graph.nodes.filter((n) => n.label.toLowerCase().includes(text)).map((n) => n.id));
  }, [graph.nodes, query]);
  const kindsShown = useMemo(() => new Set(graph.nodes.map((n) => n.kind)), [graph.nodes]);

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
    const surfaceVar = getComputedStyle(document.documentElement).getPropertyValue("--card").trim();
    const surface = surfaceVar ? `hsl(${surfaceVar})` : "#ffffff";
    const hover = hoverRef.current;
    const found = matchRef.current;
    const label = (text: string, x: number, y: number): void => {
      ctx.lineJoin = "round";
      ctx.lineWidth = 3;
      ctx.strokeStyle = surface;
      ctx.strokeText(text, x, y);
      ctx.fillStyle = ink;
      ctx.fillText(text, x, y);
    };
    ctx.lineWidth = 1;
    for (const edge of edges) {
      const a = byId.get(edge.source);
      const b = byId.get(edge.target);
      if (!a || !b) {continue;}
      const active = hover === a.id || hover === b.id;
      ctx.globalAlpha = found && !(found.has(a.id) && found.has(b.id)) && !active ? 0.25 : 1;
      ctx.strokeStyle = active ? ink : "rgba(148,163,184,0.55)";
      ctx.lineWidth = Math.min(4, 1 + edge.weight * 0.5);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      if (active) {
        ctx.font = "10px ui-sans-serif, system-ui";
        label(edge.relation.toLowerCase().replace(/_/g, " "), (a.x + b.x) / 2 + 4, (a.y + b.y) / 2 - 4);
      }
    }
    for (const node of nodesRef.current) {
      const root = node.id === graph.root_id;
      const isMatch = found?.has(node.id) ?? false;
      ctx.globalAlpha = found && !isMatch && hover !== node.id ? 0.25 : 1;
      ctx.beginPath();
      ctx.arc(node.x, node.y, root ? RADIUS + 4 : RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = KIND_COLOR[node.kind] ?? "#64748b";
      ctx.fill();
      // A ring in the surface colour keeps touching nodes apart; the root, the hovered node and
      // the search results get an ink ring.
      ctx.lineWidth = 2;
      ctx.strokeStyle = root || hover === node.id || isMatch ? ink : surface;
      ctx.stroke();
      ctx.font = `${root || isMatch ? "600 12px" : "11px"} ui-sans-serif, system-ui`;
      label(node.label.length > 28 ? `${node.label.slice(0, 27)}…` : node.label, node.x + RADIUS + 5, node.y + 4);
    }
    ctx.globalAlpha = 1;
  }, [edges, graph.root_id, height, width]);

  useEffect(() => {
    matchRef.current = matches;
    draw();
  }, [draw, matches]);

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
          const id = hit(event)?.id ?? null;
          if (id !== hoverRef.current) {
            hoverRef.current = id;
            draw();
          }
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
        onPointerLeave={() => {
          if (hoverRef.current !== null) {
            hoverRef.current = null;
            draw();
          }
        }}
      />
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <input
          type="search"
          className="fp-input h-7 w-48 py-0 text-xs"
          placeholder="Find a record"
          aria-label="Find a record in the graph"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {matches ? (
          <span role="status" data-testid="graph-matches">
            {matches.size === 0 ? "No record matches" : `${matches.size} of ${graph.nodes.length} records match`}
          </span>
        ) : null}
        {(Object.keys(KIND_COLOR) as EntityKind[]).filter((kind) => kindsShown.has(kind)).map((kind) => (
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
