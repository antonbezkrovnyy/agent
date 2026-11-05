"use client";

import { useEffect, useMemo, useState } from "react";
import { Dict, list, record, text } from "./format";

type NodeLayout = { id: string; x: number; y: number; active: boolean };

export function GraphView({ state }: { state: Dict }) {
  const [payload, setPayload] = useState<Dict>({});
  const [error, setError] = useState("");
  useEffect(() => {
    fetch("/api/control/graph", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      })
      .then(setPayload)
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
  }, []);

  const graph = record(payload.graph);
  const nodes = list(graph.nodes);
  const edges = list(graph.edges);
  const progress = record(state.progress);
  const activeName = text(progress.stage ?? progress.agent, "");
  const { layout, width, height } = useMemo(() => {
    const depths = new Map<string, number>([["__start__", 0]]);
    const queue = ["__start__"];
    while (queue.length) {
      const current = queue.shift()!;
      const depth = depths.get(current) ?? 0;
      for (const edge of edges.filter((item) => item.source === current)) {
        const target = text(edge.target, "");
        if (target && !depths.has(target)) {
          depths.set(target, depth + 1);
          queue.push(target);
        }
      }
    }
    const groups = new Map<number, Dict[]>();
    for (const node of nodes) {
      const depth = depths.get(text(node.id)) ?? 0;
      groups.set(depth, [...(groups.get(depth) ?? []), node]);
    }
    const result: NodeLayout[] = [];
    for (const [depth, group] of groups) {
      group.forEach((node, index) => result.push({
        id: text(node.id),
        x: 20 + depth * 180,
        y: 24 + index * 68,
        active: text(node.id) === activeName,
      }));
    }
    return {
      layout: result,
      width: Math.max(760, (Math.max(0, ...groups.keys()) + 1) * 180 + 20),
      height: Math.max(150, Math.max(0, ...Array.from(groups.values()).map((group) => group.length)) * 68 + 30),
    };
  }, [nodes, edges, activeName]);

  if (error) return <div className="error-box">Топология недоступна: {error}</div>;
  if (!nodes.length) return <div className="empty-state"><strong>Загружаю топологию графа…</strong></div>;
  const positions = new Map(layout.map((node) => [node.id, node]));
  return (
    <div className="graph-view">
      <div className="graph-legend"><span><i className="graph-dot active" />Активный узел</span><span><i className="graph-dot" />Узел графа</span><span>Пунктир — условный переход</span></div>
      <div className="graph-canvas">
        <svg width={width} height={height} role="img" aria-label="Топология графа агента">
          <defs><marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" /></marker></defs>
          {edges.map((edge, index) => {
            const source = positions.get(text(edge.source));
            const target = positions.get(text(edge.target));
            if (!source || !target) return null;
            const x1 = source.x + 142;
            const y1 = source.y + 20;
            const x2 = target.x;
            const y2 = target.y + 20;
            return <path key={index} className={`graph-edge ${edge.conditional ? "conditional" : ""}`} d={`M${x1},${y1} C${x1 + 35},${y1} ${x2 - 35},${y2} ${x2},${y2}`} markerEnd="url(#graph-arrow)" />;
          })}
          {layout.map((node) => (
            <g key={node.id} className={`graph-node ${node.active ? "active" : ""}`} transform={`translate(${node.x},${node.y})`}>
              <rect width="142" height="42" rx="8" />
              <text x="71" y="25" textAnchor="middle">{node.id}</text>
            </g>
          ))}
        </svg>
      </div>
      <div className="subgraph-list">
        {Object.keys(record(payload.subgraphs)).map((name) => <span key={name} className={activeName === name ? "active" : ""}>{name}</span>)}
      </div>
    </div>
  );
}
