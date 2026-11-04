"use client";

import { useEffect, useRef, useState } from "react";
import type { AbstractAgent } from "@ag-ui/client";
import { Dict, record } from "./format";

export function SteeringPanel({ agent, state }: { agent: AbstractAgent; state: Dict }) {
  const running = agent.isRunning || record(state.progress).state === "running";
  const [note, setNote] = useState("");
  const [queue, setQueue] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wasRunning = useRef(running);

  const runNote = async (value: string) => {
    setBusy(true);
    setError("");
    try {
      agent.addMessage({ id: crypto.randomUUID(), role: "user", content: value });
      await agent.runAgent();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (wasRunning.current && !running && queue.length && !busy) {
      const [next, ...rest] = queue;
      setQueue(rest);
      void runNote(next);
    }
    wasRunning.current = running;
  }, [running, queue, busy]);

  const submit = () => {
    const value = note.trim();
    if (!value || busy) return;
    setNote("");
    if (running) setQueue((current) => [...current, value]);
    else void runNote(value);
  };

  return (
    <section className="steering-panel">
      <div className="steering-heading">
        <div>
          <h2>Скорректировать ход</h2>
          <p>Уточнение отправится после текущего run. Агент продолжает работу и никого не ждёт.</p>
        </div>
        {running && <button type="button" className="button button-stop" onClick={() => agent.abortRun()}>Остановить run</button>}
      </div>
      <textarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={2}
        placeholder="Например: не проверяй внешние IP, сфокусируйся на аномалиях аутентификации"
        aria-label="Уточнение для агента"
      />
      <div className="steering-footer">
        <button type="button" className="button button-primary" disabled={!note.trim() || busy} onClick={submit}>
          {running ? "В очередь после run" : busy ? "Отправка…" : "Отправить уточнение"}
        </button>
        <span>{running ? "Можно писать прямо во время работы" : "Уточнение продолжит текущее дело"}</span>
      </div>
      {queue.length > 0 && (
        <div className="steering-queue">
          <span>В очереди: {queue.length}</span>
          {queue.map((item, index) => (
            <div className="steering-queue-item" key={`${item}-${index}`}>
              <p>{item}</p>
              <button type="button" aria-label="Удалить уточнение из очереди" onClick={() => setQueue((current) => current.filter((_, i) => i !== index))}>×</button>
            </div>
          ))}
        </div>
      )}
      {error && <div className="error-box">Не удалось отправить уточнение: {error}</div>}
    </section>
  );
}
