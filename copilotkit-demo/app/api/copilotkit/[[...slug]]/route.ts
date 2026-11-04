import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

process.env.COPILOTKIT_TELEMETRY_DISABLED = "true";

const runtime = new CopilotRuntime({
  agents: {
    default: new HttpAgent({
      url: process.env.AGUI_ENDPOINT_URL ?? "http://localhost:2027/agent",
    }),
  },
  identifyUser: () => ({ id: "local-sandbox", name: "Local Sandbox" }),
});

const handler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
});

export { handler as GET, handler as POST, handler as PATCH, handler as DELETE };
