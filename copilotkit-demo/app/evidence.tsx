"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Dict,
  formatDateTime,
  list,
  number,
  record,
  resultRows,
  resultSummary,
  statusTone,
  text,
  windowLabel,
} from "./format";
import { Badge, CopyButton, EmptyState, KeyValue, Section, StatusBadge } from "./ui";

function cellText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (["string", "number", "boolean"].includes(typeof value)) {
    const rendered = String(value);
    return rendered.length > 220 ? `${rendered.slice(0, 217)}…` : rendered;
  }
  if (Array.isArray(value)) return value.map(cellText).join(", ");
  const entries = Object.entries(record(value));
  return entries.slice(0, 4).map(([key, item]) => `${key}: ${cellText(item)}`).join(" · ") || "—";
}

function ResultTable({ result }: { result: unknown }) {
  const rows = resultRows(result);
  if (!rows.length) return null;
  const columns = Array.from(new Set(rows.slice(0, 25).flatMap((row) => Object.keys(row)))).slice(0, 8);
  return (
    <div className="result-table-wrap">
      <div className="result-table-caption">
        <strong>Записи результата</strong>
        <span>{rows.length > 25 ? `показаны 25 из ${rows.length}` : `${rows.length} записей`}</span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>
            {rows.slice(0, 25).map((row, index) => (
              <tr key={text(row.uuid ?? row.id, String(index))}>
                {columns.map((column) => <td key={column}>{cellText(row[column])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ResultFields({ result }: { result: unknown }) {
  const fields = Object.entries(record(result)).filter(([key]) => !["events", "rows", "items", "results", "data", "window"].includes(key));
  if (!fields.length) return null;
  return (
    <div className="result-fields">
      {fields.map(([key, value]) => <KeyValue key={key} label={key}>{cellText(value)}</KeyValue>)}
    </div>
  );
}

function EvidenceDetail({ item }: { item: Dict }) {
  const args = Object.entries(record(item.arguments));
  const iql = text(item.iql, "");
  const validationText = iql
    ? `${iql}\n\nПериод проверки: ${windowLabel(item.window)}`
    : "";
  return (
    <div className="evidence-detail">
      <div className="evidence-detail-header">
        <div>
          <div className="eyebrow">{text(item.id)}</div>
          <h2>{text(item.tool)}</h2>
          <p>{text(item.step_id)} · {formatDateTime(item.created_at)}</p>
        </div>
        <div className="evidence-statuses">
          <StatusBadge status={item.status} />
          <Badge tone={item.complete ? "ok" : "running"}>{item.complete ? "полный результат" : "неполный результат"}</Badge>
        </div>
      </div>

      <div className="evidence-window">
        <span>Временное окно</span>
        <strong>{windowLabel(item.window)}</strong>
      </div>

      <section className="query-panel">
        <div className="query-heading">
          <div>
            <h3>Запрос для проверки в LogiCore</h3>
            <p>Фактический запрос, выполненный агентом. LogiCore использует синтаксис IQL, не SQL; вставьте его в LogiCore с тем же окном.</p>
          </div>
          <div className="button-row">
            <CopyButton value={iql} label="Копировать запрос" />
            <CopyButton value={validationText} label="Запрос + окно" />
          </div>
        </div>
        {iql ? <pre className="query-code"><code>{iql}</code></pre> : (
          <div className="query-missing">LogiCore не вернул текст запроса. Агент не реконструирует его сам — ниже показаны только фактические аргументы вызова.</div>
        )}
      </section>

      <section className="arguments-panel">
        <h3>Аргументы вызова</h3>
        {args.length ? (
          <div className="details-grid">
            {args.map(([key, value]) => <KeyValue key={key} label={key}>{cellText(value)}</KeyValue>)}
          </div>
        ) : <span className="muted">Без аргументов</span>}
      </section>

      <section className="result-panel">
        <div className="result-heading"><h3>Результат инструмента</h3><Badge tone={statusTone(item.status)}>{resultSummary(item.result)}</Badge></div>
        {!!item.error && <div className="error-box">{cellText(item.error)}</div>}
        <ResultFields result={item.result} />
        <ResultTable result={item.result} />
      </section>
    </div>
  );
}

export function EvidenceExplorer({ evidence, focusId = "" }: { evidence: Dict[]; focusId?: string }) {
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "ok" | "issues">("all");

  useEffect(() => {
    if (focusId) {
      setSelectedId(focusId);
      setFilter("all");
      setQuery("");
    }
  }, [focusId]);

  const filtered = useMemo(() => evidence.filter((item) => {
    if (filter === "ok" && item.status !== "ok") return false;
    if (filter === "issues" && item.status === "ok") return false;
    const haystack = `${text(item.id)} ${text(item.tool)} ${text(item.iql)} ${text(item.arguments && JSON.stringify(item.arguments))}`.toLowerCase();
    return haystack.includes(query.toLowerCase());
  }).reverse(), [evidence, filter, query]);

  const selected = filtered.find((item) => item.id === selectedId) ?? filtered[0];
  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(text(selected.id));
  }, [selected, selectedId]);

  if (!evidence.length) {
    return (
      <Section title="Запросы и доказательства" subtitle="Фактические вызовы инструментов и их результаты">
        <EmptyState title="Доказательств пока нет">Запустите проверку. Здесь появятся запросы к LogiCore, временные окна и полученные записи — всё можно проверить повторно.</EmptyState>
      </Section>
    );
  }

  return (
    <div className="evidence-explorer">
      <div className="evidence-toolbar">
        <div className="segmented">
          <button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>Все · {evidence.length}</button>
          <button type="button" className={filter === "ok" ? "active" : ""} onClick={() => setFilter("ok")}>OK</button>
          <button type="button" className={filter === "issues" ? "active" : ""} onClick={() => setFilter("issues")}>Ограничения / ошибки</button>
        </div>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ID, инструменту или IQL" aria-label="Поиск Evidence" />
      </div>
      <div className="evidence-layout">
        <aside className="evidence-list">
          {filtered.length === 0 && <EmptyState title="Ничего не найдено">Измените фильтр или поисковый запрос.</EmptyState>}
          {filtered.map((item, index) => (
            <button
              type="button"
              className={`evidence-list-item ${selected?.id === item.id ? "selected" : ""}`}
              key={text(item.id, String(index))}
              onClick={() => setSelectedId(text(item.id))}
            >
              <div><strong>{text(item.tool)}</strong><StatusBadge status={item.status} /></div>
              <span>{text(item.id)}</span>
              <p>{windowLabel(item.window)}</p>
              <small>{resultSummary(item.result)} · {formatDateTime(item.created_at)}</small>
            </button>
          ))}
        </aside>
        <div className="evidence-content">
          {selected ? <EvidenceDetail item={selected} /> : <EmptyState title="Выберите Evidence">Слева находится список фактических вызовов.</EmptyState>}
        </div>
      </div>
    </div>
  );
}
