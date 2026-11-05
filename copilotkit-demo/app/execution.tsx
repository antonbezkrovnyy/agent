"use client";

import { useCallback, useEffect, useState } from "react";
import { Dict, formatTime, list, record, roleLabel, text } from "./format";
import { EmptyState, Section } from "./ui";
import { GraphView } from "./graph";
import "./execution.css";

const roles = ["scout", "investigator", "reporter", "planner", "intent", "critic_evidence", "critic_report", "chat"];
const channels: Record<string, string> = { scout: "scout_msgs", investigator: "inv_msgs", reporter: "rep_msgs" };

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((part) => typeof part === "string" ? part : text(record(part).text, "")).join("");
  return content == null ? "" : JSON.stringify(content, null, 2);
}

function MessageCard({ message }: { message: Dict }) {
  const type = text(message.type ?? message.role);
  const calls = list(message.tool_calls);
  return (
    <article className={`execution-message message-${type}`}>
      <div className="execution-message-meta"><strong>{type === "ai" ? "Модель" : type === "tool" ? "Инструмент" : type === "human" ? "Пользователь" : type}</strong><span>{text(message.name, "")}</span><small>{text(message.id, "")}</small></div>
      {!!contentText(message.content) && <pre>{contentText(message.content)}</pre>}
      {calls.map((call, index) => (
        <details className="tool-call-detail" key={text(call.id, String(index))}>
          <summary>{text(call.name)} · {text(call.id, "")}</summary>
          <pre>{JSON.stringify(call.args ?? {}, null, 2)}</pre>
        </details>
      ))}
    </article>
  );
}

function PromptCard({ event }: { event: Dict }) {
  const payload = record(event.payload);
  const messages = list(payload.messages);
  return (
    <article className="prompt-card">
      <div className="prompt-card-heading"><strong>{roleLabel(event.role)}</strong><span>{text(event.work_item_id, "основной поток")}</span><time>{formatTime(event.created_at)}</time></div>
      <div className="prompt-card-meta">run {text(event.run_id, "—")} · {messages.length} сообщений · tools: {list(payload.tools).length || (Array.isArray(payload.tools) ? payload.tools.length : 0)}</div>
      <details>
        <summary>Фактический system prompt</summary>
        <pre className="prompt-text">{text(payload.system_prompt, "Системный prompt не зафиксирован")}</pre>
      </details>
      <details>
        <summary>Входные сообщения модели · {messages.length}</summary>
        <div className="prompt-messages">{messages.map((message, index) => <MessageCard key={text(message.id, String(index))} message={message} />)}</div>
      </details>
    </article>
  );
}

export function ExecutionView({ state, threadId, running }: { state: Dict; threadId: string; running: boolean }) {
  const [role, setRole] = useState("scout");
  const [journalRole, setJournalRole] = useState("");
  const [events, setEvents] = useState<Dict[]>([]);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  const load = useCallback(async (nextOffset: number, append: boolean) => {
    const params = new URLSearchParams({ limit: "50", offset: String(nextOffset) });
    if (journalRole) params.set("role", journalRole);
    const response = await fetch(`/api/control/threads/${encodeURIComponent(threadId)}/events?${params}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await response.text());
    const items = await response.json();
    setEvents((current) => append ? [...current, ...items] : items);
    setOffset(nextOffset + items.length);
    setHasMore(items.length === 50);
  }, [threadId, journalRole]);

  useEffect(() => {
    setError("");
    load(0, false).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
  }, [load]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => load(0, false).catch(() => undefined), 4000);
    return () => window.clearInterval(timer);
  }, [running, load]);

  const messages = list(state[channels[role]]);
  return (
    <div className="execution-view">
      <Section title="Топология исполнения" subtitle="Граф и субграфы загружены из Aegra; активный узел отмечается по фактическому progress">
        <GraphView state={state} />
      </Section>
      <div className="execution-grid">
        <Section
          title="Журнал модели"
          subtitle="Фактические prompts и входные сообщения, сохранённые вне checkpoint"
          className="execution-journal"
          action={<select value={journalRole} onChange={(event) => setJournalRole(event.target.value)} aria-label="Фильтр журнала по роли"><option value="">Все роли</option>{roles.map((item) => <option key={item} value={item}>{roleLabel(item)}</option>)}</select>}
        >
          {error && <div className="error-box">{error}</div>}
          {!events.length && !error && <EmptyState title="Вызовы модели появятся здесь">Для новых runs сохраняются system prompt и фактический вход каждой роли.</EmptyState>}
          <div className="prompt-list">{[...events].reverse().map((event) => <PromptCard key={text(event.event_id)} event={event} />)}</div>
          {hasMore && <button type="button" className="button button-secondary" onClick={() => load(offset, true)}>Загрузить ещё</button>}
        </Section>
        <Section
          title="Диалог агента"
          subtitle="Приватные сообщения и фактические tool calls из state"
          className="execution-messages"
          action={<select value={role} onChange={(event) => setRole(event.target.value)} aria-label="Агент"><option value="scout">Scout</option><option value="investigator">Investigator</option><option value="reporter">Reporter</option></select>}
        >
          {!messages.length ? <EmptyState title={`Сообщений ${roleLabel(role)} пока нет`}>Они появятся после запуска этой роли. В общий чат внутренние сообщения не попадают.</EmptyState> : (
            <div className="execution-message-list">{messages.map((message, index) => <MessageCard key={text(message.id, String(index))} message={message} />)}</div>
          )}
        </Section>
      </div>
    </div>
  );
}
