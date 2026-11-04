"use client";

import { useMemo, useState } from "react";
import { Dict, formatTime, list, record, statusTone, text } from "./format";
import { Badge, EmptyState, StatusBadge } from "./ui";
import { evidenceMatches, groupByTool, groupEvidenceByWorkItems, itemMetricsSummary, metricsForItem, relatedRecords } from "./tree-data";

function EvidenceRow({ item, onOpen }: { item: Dict; onOpen: (id: string) => void }) {
  return (
    <button type="button" className={`tree-evidence-row row-${statusTone(item.status)}`} onClick={() => onOpen(text(item.id))}>
      <span className="tree-evidence-status" />
      <strong>{text(item.tool)}</strong>
      <span className="tree-evidence-id">{text(item.id)}</span>
      <span className="tree-evidence-summary">{text(record(item.result).count !== undefined ? `count=${String(record(item.result).count)}` : item.status, "")}</span>
      <time>{formatTime(item.created_at)}</time>
    </button>
  );
}

function EvidenceList({ items, onOpen }: { items: Dict[]; onOpen: (id: string) => void }) {
  const [visible, setVisible] = useState(6);
  return (
    <div className="tree-evidence-list">
      {items.slice(0, visible).map((item, index) => <EvidenceRow key={text(item.id, String(index))} item={item} onOpen={onOpen} />)}
      {items.length > visible && (
        <button type="button" className="tree-more" onClick={() => setVisible((count) => count + 20)}>
          Ещё {Math.min(20, items.length - visible)} из {items.length - visible}
        </button>
      )}
    </div>
  );
}

function RelatedBlock({ title, items, kind }: { title: string; items: Dict[]; kind: "hypothesis" | "finding" }) {
  if (!items.length) return null;
  return (
    <details className="tree-related">
      <summary>{title} · {items.length}</summary>
      {items.slice(0, 8).map((item, index) => (
        <div className="tree-related-item" key={index}>
          {kind === "hypothesis" && <StatusBadge status={item.status} />}
          <span>{text(kind === "hypothesis" ? item.text : item.claim)}</span>
        </div>
      ))}
    </details>
  );
}

function WorkItemNode({
  item,
  evidence,
  metrics,
  hypotheses,
  findings,
  query,
  issuesOnly,
  expanded,
  onToggle,
  onOpenEvidence,
}: {
  item: Dict;
  evidence: Dict[];
  metrics: Dict[];
  hypotheses: Dict[];
  findings: Dict[];
  query: string;
  issuesOnly: boolean;
  expanded: boolean;
  onToggle: () => void;
  onOpenEvidence: (id: string) => void;
}) {
  const itemMatches = !query || `${text(item.id)} ${text(item.goal)}`.toLowerCase().includes(query.toLowerCase());
  const visibleEvidence = evidence.filter((evidenceItem) => evidenceMatches(evidenceItem, query) && (!issuesOnly || evidenceItem.status !== "ok"));
  if ((!itemMatches && !visibleEvidence.length) || (issuesOnly && item.status !== "failed" && !visibleEvidence.length)) return null;
  return (
    <div className={`tree-node work-${statusTone(item.status)}`}>
      <div className="tree-node-header">
        <button type="button" className="tree-expander" onClick={onToggle} aria-label={expanded ? "Свернуть этап" : "Развернуть этап"}>
          {expanded ? "−" : "+"}
        </button>
        <button type="button" className="tree-node-title" onClick={onToggle}>
          <strong>{text(item.id)}</strong>
          <StatusBadge status={item.status} />
          <span>{evidence.length} вызовов</span>
        </button>
      </div>
      <div className="tree-node-goal">{text(item.goal)}</div>
      {expanded && (
        <div className="tree-children">
          {!!metrics.length && <div className="tree-metrics">{itemMetricsSummary(metrics)}</div>}
          {visibleEvidence.length > 0 && <EvidenceList items={visibleEvidence} onOpen={onOpenEvidence} />}
          {issuesOnly && !visibleEvidence.length && <div className="tree-empty-inline">Проблемных вызовов нет</div>}
          <RelatedBlock title="Гипотезы" items={relatedRecords(hypotheses, visibleEvidence)} kind="hypothesis" />
          <RelatedBlock title="Находки" items={relatedRecords(findings, visibleEvidence)} kind="finding" />
        </div>
      )}
    </div>
  );
}

export function InvestigationTree({ state, onOpenEvidence }: { state: Dict; onOpenEvidence: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const workItems = list(state.work_items);
  const evidence = list(state.evidence);
  const hypotheses = list(state.hypotheses);
  const findings = list(state.findings);
  const metrics = list(state.run_metrics);
  const groups = useMemo(() => groupEvidenceByWorkItems(evidence, workItems), [evidence, workItems]);
  const toolGroups = useMemo(() => groupByTool(groups.unassigned), [groups.unassigned]);
  const hasActive = workItems.some((item) => ["running", "critic_review"].includes(text(item.status)));

  const visibleUnassigned = groups.unassigned.filter((item) => evidenceMatches(item, query) && (!issuesOnly || item.status !== "ok"));
  const allNodeIds = [...workItems.map((item) => text(item.id)), ...toolGroups.keys()];

  return (
    <div className="investigation-tree">
      <div className="tree-toolbar">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Этап, инструмент, ID или запрос" aria-label="Поиск по дереву расследования" />
        <label className="tree-issues-filter"><input type="checkbox" checked={issuesOnly} onChange={(event) => setIssuesOnly(event.target.checked)} />Только проблемы</label>
        <button type="button" className="button button-secondary button-small" onClick={() => setExpanded(new Set(allNodeIds))}>Развернуть</button>
        <button type="button" className="button button-secondary button-small" onClick={() => setExpanded(new Set(allNodeIds.map((id) => `close:${id}`)))}>Свернуть</button>
      </div>
      <div className="tree-summary">
        <span>{workItems.length} этапов</span>
        <span>{evidence.length} вызовов</span>
        <span>{findings.length} находок</span>
        {hasActive && <Badge tone="running">идёт работа</Badge>}
      </div>

      {workItems.map((item, index) => {
        const id = text(item.id, String(index));
        const itemEvidence = groups.byItem.get(id) ?? [];
        const defaultOpen = ["running", "critic_review"].includes(text(item.status)) || (workItems.length === 1 && !query);
        return (
          <WorkItemNode
            key={id}
            item={item}
            evidence={itemEvidence}
            metrics={metricsForItem(metrics, id)}
            hypotheses={hypotheses}
            findings={findings}
            query={query}
            issuesOnly={issuesOnly}
            expanded={expanded.has(id) || (defaultOpen && !expanded.has(`close:${id}`))}
            onToggle={() => setExpanded((current) => {
              const next = new Set(current);
              const isOpen = next.has(id) || (defaultOpen && !next.has(`close:${id}`));
              if (isOpen) {
                next.delete(id);
                next.add(`close:${id}`);
              } else {
                next.delete(`close:${id}`);
                next.add(id);
              }
              return next;
            })}
            onOpenEvidence={onOpenEvidence}
          />
        );
      })}

      {workItems.length > 0 && visibleUnassigned.length > 0 && (
        <div className="tree-node tree-unassigned">
          <div className="tree-node-header">
            <button type="button" className="tree-expander" onClick={() => setExpanded((current) => {
              const next = new Set(current);
              if (next.has("unassigned")) next.delete("unassigned"); else next.add("unassigned");
              return next;
            })}>{expanded.has("unassigned") ? "−" : "+"}</button>
            <div className="tree-node-title"><strong>Общие вызовы</strong><span>{visibleUnassigned.length}</span></div>
          </div>
          <div className="tree-node-goal">Запросы вне этапов плана или без однозначной привязки по времени</div>
          {expanded.has("unassigned") && <div className="tree-children"><EvidenceList items={visibleUnassigned} onOpen={onOpenEvidence} /></div>}
        </div>
      )}

      {!workItems.length && Array.from(toolGroups.entries()).map(([tool, items]) => {
        const visible = items.filter((item) => evidenceMatches(item, query) && (!issuesOnly || item.status !== "ok"));
        if (!visible.length) return null;
        return (
          <div className="tree-node" key={tool}>
            <div className="tree-node-header">
              <button type="button" className="tree-expander" onClick={() => setExpanded((current) => {
                const next = new Set(current);
                if (next.has(tool)) next.delete(tool); else next.add(tool);
                return next;
              })}>{expanded.has(tool) ? "−" : "+"}</button>
              <div className="tree-node-title"><strong>{tool}</strong><span>{visible.length} вызовов</span></div>
            </div>
            {expanded.has(tool) && <div className="tree-children"><EvidenceList items={visible} onOpen={onOpenEvidence} /></div>}
          </div>
        );
      })}

      {!evidence.length && <EmptyState title="Дерево появится после первых вызовов">Этапы плана будут раскрываться в вызовы инструментов, гипотезы и находки.</EmptyState>}
    </div>
  );
}
