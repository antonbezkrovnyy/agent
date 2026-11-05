"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Dict, formatDateTime, list, record, text } from "./format";
import "./home.css";

type Props = {
  onOpenThread: (threadId: string) => void;
  onOpenReport: (threadId: string) => void;
  onNewThread: () => void;
};

const statusLabels: Record<string, string> = { active: "Расследуется", paused: "На паузе", error: "Ошибка", done: "Завершено" };

export function HomeDashboard({ onOpenThread, onOpenReport, onNewThread }: Props) {
  const [data, setData] = useState<Dict>({});
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const response = await fetch("/api/control/dashboard", { cache: "no-store" });
      if (!response.ok) throw new Error(await response.text());
      setData(await response.json());
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => load(true), 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const matches = useCallback((value: unknown) => !query || String(value ?? "").toLowerCase().includes(query.toLowerCase()), [query]);
  const cases = list(data.cases).filter((item) => (filter === "all" || item.status === filter) && matches(`${item.id} ${item.title} ${item.last_message}`));
  const chats = list(data.chats).filter((item) => matches(`${item.id} ${item.title} ${item.last_message}`)).slice(0, 4);
  const reports = list(data.reports).filter((item) => matches(`${item.title} ${item.summary}`)).slice(0, 4);
  const memory = list(data.memory).filter((item) => matches(`${item.statement} ${item.source}`)).slice(0, 4);
  const health = list(data.health);
  const healthy = health.length > 0 && health.every((item) => item.ok);
  const runs = list(data.runs);

  return (
    <main className="analyst-home">
      <header className="home-topbar">
        <div><h1>Рабочий стол аналитика</h1><p>Дела, результаты и контекст расследований</p></div>
        <label className="home-search"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Дело, IP, хост, учётная запись или событие" aria-label="Поиск по рабочему столу" /></label>
        <button type="button" className="home-button home-primary" onClick={onNewThread}>Новое расследование</button>
      </header>

      <div className="home-content">
        <section className={`health-strip ${healthy ? "" : "degraded"}`}>
          <span className="health-dot" /><strong>{healthy ? "Основные функции доступны" : "Есть ограничения"}</strong>
          <div>{health.map((item) => <span className={item.ok ? "service-ok" : "service-error"} key={text(item.name)}>{text(item.name)}</span>)}</div>
          <button type="button" className="home-link" onClick={() => load()}>Обновить</button>
        </section>
        {error && <div className="home-error">Не удалось загрузить часть данных: {error}</div>}

        <div className="home-main-grid">
          <section className="home-panel">
            <div className="home-panel-head"><h2>Открытые дела</h2><span>{cases.length}</span></div>
            <div className="home-filters">
              <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>Все</button>
              <button className={filter === "active" ? "active" : ""} onClick={() => setFilter("active")}>Активные</button>
              <button className={filter === "paused" ? "active" : ""} onClick={() => setFilter("paused")}>На паузе</button>
              <button className={filter === "error" ? "active" : ""} onClick={() => setFilter("error")}>Ошибки</button>
            </div>
            {loading ? <div className="home-empty">Загружаю дела…</div> : cases.length ? (
              <div className="home-table-wrap"><table className="home-table"><thead><tr><th>Дело</th><th>Статус</th><th>Evidence</th><th>Обновлено</th></tr></thead><tbody>{cases.map((item) => (
                <tr key={text(item.id)} onClick={() => onOpenThread(text(item.id))}>
                  <td><strong>{text(item.title)}</strong><small>{text(item.id)}</small><span>{text(item.stage, "Без текущего этапа")}</span></td>
                  <td><i className={`home-status status-${text(item.status)}`}>{statusLabels[text(item.status)] ?? text(item.status)}</i></td>
                  <td>{text(item.entities, "0")} сущностей · {text(item.evidence, "0")}</td>
                  <td>{formatDateTime(item.updated_at)}</td>
                </tr>
              ))}</tbody></table></div>
            ) : <div className="home-empty">Нет открытых дел по текущему фильтру. Завершённые диалоги доступны ниже.</div>}
          </section>

          <section className="home-panel">
            <div className="home-panel-head"><h2>Сейчас выполняется</h2><span>{runs.length}</span></div>
            {runs.length ? <div className="home-list">{runs.map((item) => (
              <button className="home-list-row" key={text(item.id)} onClick={() => onOpenThread(text(item.id))}>
                <div><strong>{text(item.title)}</strong><small>{text(item.role, "Агент")} · {text(item.stage, "Выполняется")}</small><span>{text(item.id)}</span></div><i className="home-status status-active">Работает</i>
              </button>
            ))}</div> : <div className="home-empty">Сейчас агент ничего не выполняет. Можно продолжить дело из списка или начать новое.</div>}
          </section>
        </div>

        <div className="home-secondary-grid">
          <section className="home-panel">
            <div className="home-panel-head"><h2>Недавние чаты</h2><span>{chats.length}</span></div>
            {chats.length ? <div className="home-list">{chats.map((item) => (
              <button className="home-list-row" key={text(item.id)} onClick={() => onOpenThread(text(item.id))}>
                <div><strong>{text(item.title)}</strong><small>{text(item.last_message, "Сообщений пока нет")}</small><span>{formatDateTime(item.updated_at)} · {statusLabels[text(item.status)] ?? text(item.run_status)}</span></div>
              </button>
            ))}</div> : <div className="home-empty">Чатов не найдено.</div>}
          </section>

          <section className="home-panel">
            <div className="home-panel-head"><h2>Отчёты</h2><span>{reports.length}</span></div>
            {reports.length ? <div className="home-list">{reports.map((item) => (
              <button className="home-list-row" key={text(item.id)} onClick={() => onOpenReport(text(item.thread_id))}>
                <div><strong>{text(item.title)}</strong><small>{text(item.summary, "Отчёт расследования")}</small><span>{formatDateTime(item.updated_at)} · Markdown и артефакты</span></div><i className="home-status status-done">Готов</i>
              </button>
            ))}</div> : <div className="home-empty">Готовых отчётов пока нет.</div>}
          </section>
        </div>

        <section className="home-panel memory-panel">
          <div className="home-panel-head"><h2>Память агента</h2><span>контекст, не свежие Evidence</span></div>
          {memory.length ? <div className="memory-grid">{memory.map((item) => (
            <article className="memory-fact" key={text(item.id)}><div><i className={`memory-kind ${text(item.status)}`}>{text(item.status) === "observed" ? "Наблюдение" : "Подтверждено"}</i><span>{formatDateTime(item.updated_at)}</span></div><p>{text(item.statement)}</p><small>{text(item.source, "Без источника")} · {text(item.evidence, "0")} Evidence{item.valid_to ? ` · актуально до ${formatDateTime(item.valid_to)}` : ""}</small></article>
          ))}</div> : <div className="home-empty">Устойчивых фактов пока нет. Они появятся после завершённых расследований.</div>}
        </section>
      </div>
    </main>
  );
}
