"use client";

import type { AbstractAgent } from "@ag-ui/client";
import type { Dict } from "./format";

export const THREAD_STORAGE_KEY = "logicore.agui.threadId";

export function publicMessages(values: Dict) {
  return (Array.isArray(values.messages) ? values.messages : []).flatMap((item: Dict) => {
    const type = item.type ?? item.role;
    if (!item.id || !["human", "user", "ai", "assistant"].includes(String(type))) return [];
    return [{
      id: String(item.id),
      role: ["human", "user"].includes(String(type)) ? "user" as const : "assistant" as const,
      content: typeof item.content === "string" ? item.content : "",
    }];
  });
}

export async function restoreThread(agent: AbstractAgent, threadId: string) {
  agent.threadId = threadId;
  const [stateResponse, runsResponse] = await Promise.all([
    fetch(`/api/control/threads/${encodeURIComponent(threadId)}/state`, { cache: "no-store" }),
    fetch(`/api/control/threads/${encodeURIComponent(threadId)}/runs`, { cache: "no-store" }),
  ]);
  if (!stateResponse.ok || !runsResponse.ok) throw new Error("Не удалось восстановить thread из Aegra");
  const state = await stateResponse.json();
  const runs = await runsResponse.json();
  const values = state.values ?? {};
  agent.setState(values);
  agent.setMessages(publicMessages(values));
  localStorage.setItem(THREAD_STORAGE_KEY, threadId);
  const active = runs.find((run: Dict) => ["pending", "running"].includes(String(run.status)));
  if (active && !agent.isRunning) {
    void agent.runAgent({ forwardedProps: { aegraRunId: active.run_id } });
  }
  return { state, runs };
}
