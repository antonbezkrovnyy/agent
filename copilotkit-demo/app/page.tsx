"use client";

import { useState } from "react";
import { CopilotChat, CopilotKitProvider } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import "./globals.css";
import { Inspector } from "./inspector";

function Sandbox() {
  const [chatOpen, setChatOpen] = useState(true);
  return (
    <div className={`sandbox-page ${chatOpen ? "with-chat" : ""}`}>
      <div className="dashboard-area">
        <Inspector />
      </div>
      <button
        type="button"
        className={`chat-toggle ${chatOpen ? "is-open" : ""}`}
        onClick={() => setChatOpen((open) => !open)}
      >
        {chatOpen ? "Скрыть чат" : "Открыть чат"}
      </button>
      {chatOpen && (
        <aside className="chat-panel" aria-label="Чат с агентом">
          <CopilotChat className="sandbox-chat" inspectorTools={false} />
        </aside>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <CopilotKitProvider runtimeUrl="/api/copilotkit" enableInspector={false} showIntelligenceIndicator={false}>
      <Sandbox />
    </CopilotKitProvider>
  );
}
