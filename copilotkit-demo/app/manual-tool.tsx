"use client";

import { useEffect, useState } from "react";
import { Dict, record, resultSummary, text } from "./format";
import { Section, StatusBadge } from "./ui";

export function ManualToolPanel({ threadId, onState }: { threadId: string; onState: (state: Dict) => void }) {
  const [tools, setTools] = useState<Dict[]>([]);
  const [selected, setSelected] = useState("");
  const [args, setArgs] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState<Dict>({});

  useEffect(() => {
    fetch("/api/control/tools", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      })
      .then((items) => {
        setTools(items);
        if (items[0]) setSelected(items[0].name);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
  }, []);

  const tool = tools.find((item) => item.name === selected);
  const execute = async () => {
    setBusy(true);
    setError("");
    setEvidence({});
    try {
      const parsed = JSON.parse(args);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Аргументы должны быть JSON-объектом");
      const response = await fetch(`/api/control/threads/${encodeURIComponent(threadId)}/tools`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: selected, arguments: parsed }),
      });
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      onState(payload);
      setEvidence(record(payload.manual_evidence));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Ручной read-only вызов" subtitle="Фактический MCP-запрос выполняется на паузе и сохраняется как обычная Evidence">
      <div className="manual-tool-form">
        <label>Инструмент<select value={selected} onChange={(event) => setSelected(event.target.value)}>{tools.map((item) => <option key={text(item.name)} value={text(item.name)}>{text(item.name)}</option>)}</select></label>
        {tool && <details className="tool-schema"><summary>Схема аргументов и описание</summary><p>{text(tool.description, "")}</p><pre>{JSON.stringify(tool.schema ?? {}, null, 2)}</pre></details>}
        <label>Аргументы JSON<textarea rows={8} value={args} onChange={(event) => setArgs(event.target.value)} spellCheck={false} /></label>
        <button type="button" className="button button-primary" disabled={busy || !selected} onClick={execute}>{busy ? "Выполняю…" : "Выполнить и сохранить Evidence"}</button>
        {error && <div className="error-box">{error}</div>}
        {!!evidence.id && <div className="manual-tool-result"><div><strong>{text(evidence.id)}</strong><StatusBadge status={evidence.status} /></div><p>{resultSummary(evidence.result)}</p><small>Результат уже доступен на вкладке «Запросы и доказательства».</small></div>}
      </div>
    </Section>
  );
}
