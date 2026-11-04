import { Dict, list, record, text } from "./format";

export type EvidenceGroups = {
  byItem: Map<string, Dict[]>;
  unassigned: Dict[];
};

function timestamp(value: unknown): number {
  const parsed = Date.parse(text(value, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function groupEvidenceByWorkItems(evidenceInput: unknown, workItemsInput: unknown): EvidenceGroups {
  const evidence = list(evidenceInput);
  const workItems = list(workItemsInput);
  const byItem = new Map<string, Dict[]>();
  const unassigned: Dict[] = [];

  for (const item of evidence) {
    const at = timestamp(item.created_at);
    const candidates = workItems.filter((item) => {
      const started = timestamp(item.started_at);
      const finished = timestamp(item.finished_at);
      if (!started || at < started) return false;
      return !finished || at <= finished;
    });
    candidates.sort((a, b) => timestamp(a.started_at) - timestamp(b.started_at));
    const owner = candidates[candidates.length - 1];
    const id = text(owner?.id, "");
    if (!id) {
      unassigned.push(item);
      continue;
    }
    const group = byItem.get(id) ?? [];
    group.push(item);
    byItem.set(id, group);
  }
  return { byItem, unassigned };
}

export function groupByTool(evidence: Dict[]): Map<string, Dict[]> {
  const groups = new Map<string, Dict[]>();
  for (const item of evidence) {
    const tool = text(item.tool, "другие вызовы");
    const group = groups.get(tool) ?? [];
    group.push(item);
    groups.set(tool, group);
  }
  return groups;
}

export function metricsForItem(metricsInput: unknown, itemId: string): Dict[] {
  return list(metricsInput).filter((metric) => text(metric.work_item_id) === itemId);
}

export function relatedRecords(recordsInput: unknown, evidence: Dict[]): Dict[] {
  const ids = new Set(evidence.map((item) => text(item.id)));
  return list(recordsInput).filter((record) => {
    const evidenceIds = record.evidence_ids;
    return Array.isArray(evidenceIds) && evidenceIds.some((id) => ids.has(String(id)));
  });
}

export function evidenceMatches(item: Dict, query: string): boolean {
  if (!query) return true;
  const haystack = `${text(item.id)} ${text(item.tool)} ${text(item.iql)} ${text(item.status)}`.toLowerCase();
  return haystack.includes(query.toLowerCase());
}

export function itemMetricsSummary(metrics: Dict[]): string {
  const duration = metrics.reduce((sum, metric) => sum + Number(record(metric).duration_ms || 0), 0);
  const tokens = metrics.reduce((sum, metric) => sum + Number(record(metric).total_tokens || 0), 0);
  return `${Math.round(duration / 1000)} с · ${tokens} токенов`;
}
