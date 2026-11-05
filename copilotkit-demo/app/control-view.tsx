"use client";

import { useCallback, useEffect, useState } from "react";
import type { AbstractAgent } from "@ag-ui/client";
import { Dict, formatDateTime, list, record, text } from "./format";
import { EmptyState, Section } from "./ui";
import { publicMessages } from "./thread-sync";
import { ManualToolPanel } from "./manual-tool";
import "./control-view.css";

type ControlTab = "run" | "plan" | "prompt" | "task" | "tool" | "history";
const promptRoles = ["intent", "scope", "planner", "scout", "investigator", "reporter", "critic_evidence", "critic_report", "chat"];

export function ControlView({ agent, state, threadId }: { agent: AbstractAgent; state: Dict; threadId: string }) {
  const [tab, setTab] = useState<ControlTab>("run");
  const [snapshot, setSnapshot] = useState<Dict>({});
  const [history, setHistory] = useState<Dict[]>([]);
  const [runs, setRuns] = useState<Dict[]>([]);
  const [items, setItems] = useState<Dict[]>(list(state.work_items));
  const [task, setTask] = useState("");
  const [role, setRole] = useState("planner");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const applyState = useCallback((payload: Dict) => {
    const values = record(payload.values);
    setSnapshot(payload);
    agent.setState(values);
    agent.setMessages(publicMessages(values));
    setItems(list(values.work_items));
  }, [agent]);

  const refresh = useCallback(async () => {
    const [stateResponse, historyResponse, runsResponse] = await Promise.all([
      fetch(`/api/control/threads/${encodeURIComponent(threadId)}/state`, { cache: "no-store" }),
      fetch(`/api/control/threads/${encodeURIComponent(threadId)}/history?limit=30`, { cache: "no-store" }),
      fetch(`/api/control/threads/${encodeURIComponent(threadId)}/runs`, { cache: "no-store" }),
    ]);
    if (!stateResponse.ok || !historyResponse.ok || !runsResponse.ok) throw new Error("Aegra не вернула состояние управления");
    applyState(await stateResponse.json());
    setHistory(await historyResponse.json());
    setRuns(await runsResponse.json());
  }, [threadId, applyState]);

  useEffect(() => {
    const messages = list(state.messages);
    const latest = [...messages].reverse().find((message) => ["human", "user"].includes(text(message.type ?? message.role)));
    setTask(text(latest?.content, ""));
    setItems(list(state.work_items));
  }, [state.messages, state.work_items]);

  useEffect(() => { refresh().catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))); }, [refresh]);
  useEffect(() => { if (!agent.isRunning) refresh().catch(() => undefined); }, [agent.isRunning, refresh]);

  const perform = async (action: () => Promise<void>, success: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(success);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const pause = () => perform(async () => {
    const active = runs.find((run) => ["pending", "running"].includes(text(run.status)));
    if (!active) throw new Error("Активный run не найден");
    const response = await fetch(`/api/control/threads/${encodeURIComponent(threadId)}/runs/${active.run_id}/cancel`, { method: "POST" });
    if (!response.ok) throw new Error(await response.text());
    agent.abortRun();
  }, "Запрошена остановка на безопасной границе");

  const resume = (checkpointId = "", step = false) => perform(async () => {
    if (checkpointId) {
      const response = await fetch(`/api/control/threads/${encodeURIComponent(threadId)}/state/${checkpointId}`, { cache: "no-store" });
      if (!response.ok) throw new Error(await response.text());
      applyState(await response.json());
    }
    await agent.runAgent({ forwardedProps: {
      resume: true,
      ...(checkpointId ? { checkpointId } : {}),
      ...(step ? { interruptAfter: ["*"] } : {}),
    } });
  }, step ? "Выполнен следующий доступный узел" : "Run продолжен");

  const save = (operation: string) => perform(async () => {
    const checkpoint = record(snapshot.checkpoint);
    const body: Dict = { operation, checkpoint_id: text(checkpoint.checkpoint_id, "") };
    if (operation === "update_task") body.task = task;
    if (operation === "update_prompt") Object.assign(body, { role, prompt });
    if (operation === "set_step_mode") body.enabled = !stepEnabled;
    if (operation === "update_plan") body.items = items.map((item) => ({
      id: item.id,
      goal: item.goal,
      acceptance: item.acceptance,
      priority: item.priority,
      estimated_cost: item.estimated_cost,
      depends_on: item.depends_on,
      required: item.required,
    }));
    const response = await fetch(`/api/control/threads/${encodeURIComponent(threadId)}/state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    applyState(await response.json());
  }, "Изменение сохранено новым checkpoint");

  const active = runs.find((run) => ["pending", "running"].includes(text(run.status)));
  const latest = runs[0];
  const stepEnabled = record(record(state.control_overrides)._step).tools === true;
  return (
    <div className="control-view">
      <div className="control-summary">
        <div><span>Thread</span><strong>{threadId}</strong></div>
        <div><span>Run</span><strong>{text(active?.status ?? latest?.status)}</strong></div>
        <div><span>Checkpoint</span><strong>{text(record(snapshot.checkpoint).checkpoint_id)}</strong></div>
        <div className="control-actions">
          {agent.isRunning || active ? <button type="button" className="button button-stop" disabled={busy} onClick={pause}>Пауза</button> : (
            <>
              <button type="button" className="button button-primary" disabled={busy} onClick={() => resume()}>Продолжить</button>
              <button type="button" className="button button-secondary" disabled={busy} onClick={() => resume("", true)}>Следующий узел</button>
            </>
          )}
        </div>
      </div>
      {error && <div className="error-box">{error}</div>}
      {notice && <div className="control-notice">{notice}</div>}
      <nav className="control-tabs">
        <button className={tab === "run" ? "active" : ""} onClick={() => setTab("run")}>Run</button>
        <button className={tab === "plan" ? "active" : ""} onClick={() => setTab("plan")}>План · {items.length}</button>
        <button className={tab === "prompt" ? "active" : ""} onClick={() => setTab("prompt")}>Prompt</button>
        <button className={tab === "task" ? "active" : ""} onClick={() => setTab("task")}>Задача</button>
        <button className={tab === "tool" ? "active" : ""} onClick={() => setTab("tool")}>Инструмент</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Checkpoints · {history.length}</button>
      </nav>

      {tab === "run" && <Section title="Управление без остановки автопилота" subtitle="Пауза запрашивается вручную; обязательных approval-интерраптов нет">
        <div className="run-facts">
          <p><strong>Текущий статус:</strong> {text(active?.status ?? latest?.status)}</p>
          <p><strong>Run ID:</strong> {text(active?.run_id ?? latest?.run_id)}</p>
          <p><strong>Следующие узлы:</strong> {Array.isArray(snapshot.next) && snapshot.next.length ? snapshot.next.map((item) => String(item)).join(", ") : "нет"}</p>
          <p>«Следующий узел» использует Aegra <code>interrupt_after</code>. После остановки можно изменить план или prompt и продолжить от того же checkpoint.</p>
          <p><strong>Пауза перед tool calls:</strong> {stepEnabled ? "включена" : "выключена"}. Выполняется только по явному выбору, не в автопилоте.</p>
          <button type="button" className="button button-secondary" disabled={busy} onClick={() => save("set_step_mode")}>{stepEnabled ? "Выключить пошаговость tools" : "Останавливаться перед tool calls"}</button>
        </div>
      </Section>}

      {tab === "plan" && <Section title="Редактирование плана" subtitle="Можно менять только pending items; фактические выполненные вызовы не перезаписываются">
        {!items.length ? <EmptyState title="Плана пока нет">Сначала Planner должен создать work items.</EmptyState> : <div className="edit-items">{items.map((item, index) => (
          <article key={text(item.id, String(index))} className="edit-item">
            <div><strong>{text(item.id)}</strong><span>{text(item.status)}</span></div>
            <label>Цель<input value={text(item.goal, "")} disabled={item.status !== "pending"} onChange={(event) => setItems((current) => current.map((entry, i) => i === index ? { ...entry, goal: event.target.value } : entry))} /></label>
            <label>Acceptance<textarea rows={2} value={text(item.acceptance, "")} disabled={item.status !== "pending"} onChange={(event) => setItems((current) => current.map((entry, i) => i === index ? { ...entry, acceptance: event.target.value } : entry))} /></label>
          </article>
        ))}</div>}
        <button type="button" className="button button-primary" disabled={busy || !items.length} onClick={() => save("update_plan")}>Сохранить план</button>
      </Section>}

      {tab === "prompt" && <Section title="Prompt override" subtitle="Изменение применится к следующему вызову выбранной роли; файлы промптов не изменяются">
        <label>Роль<select value={role} onChange={(event) => setRole(event.target.value)}>{promptRoles.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Новый system prompt<textarea className="prompt-editor" rows={14} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Вставьте отредактированный system prompt целиком" /></label>
        <button type="button" className="button button-primary" disabled={busy || !prompt.trim()} onClick={() => save("update_prompt")}>Сохранить override</button>
      </Section>}

      {tab === "task" && <Section title="Редактирование задачи" subtitle="Изменяется последнее пользовательское сообщение в выбранном checkpoint">
        <textarea className="task-editor" rows={6} value={task} onChange={(event) => setTask(event.target.value)} />
        <button type="button" className="button button-primary" disabled={busy || !task.trim()} onClick={() => save("update_task")}>Сохранить задачу</button>
      </Section>}

      {tab === "tool" && <ManualToolPanel threadId={threadId} onState={applyState} />}

      {tab === "history" && <Section title="История checkpoints" subtitle="Продолжение создаёт новую ветку; старая история не удаляется">
        <div className="checkpoint-list">{history.map((entry, index) => {
          const checkpoint = record(entry.checkpoint);
          const values = record(entry.values);
          const next = Array.isArray(entry.next) ? entry.next.map((item) => String(item)) : [];
          const id = text(checkpoint.checkpoint_id);
          return <article key={id || index} className="checkpoint-card">
            <div><strong>{index + 1}. {next.length ? `→ ${next.join(", ")}` : "Завершённый state"}</strong><time>{formatDateTime(entry.created_at)}</time></div>
            <p>{list(values.messages).length} сообщений · {list(values.evidence).length} Evidence · {list(values.work_items).length} этапов</p>
            <small>{id}</small>
            <div><button type="button" className="button button-secondary button-small" disabled={busy} onClick={() => resume(id)}>Продолжить отсюда</button><button type="button" className="button button-secondary button-small" disabled={busy} onClick={() => resume(id, true)}>Один узел</button></div>
          </article>;
        })}</div>
      </Section>}
    </div>
  );
}
