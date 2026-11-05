import { type Message } from "@langchain/langgraph-sdk";
import {
  uiMessageReducer,
  isRemoveUIMessage,
  isUIMessage,
  type RemoveUIMessage,
  type UIMessage,
} from "@langchain/langgraph-sdk/react-ui";

export type ProgressState = {
  stage: string;
  state: "running" | "waiting" | "limited" | "done";
  text: string;
  agent?: string;
  tool_calls?: number;
  tool_limit?: number;
};

export type Suggestion = { label: string; value: string };

export type PublicStreamValues = {
  messages: Message[];
  ui?: UIMessage[];
  progress?: ProgressState;
  suggestions?: Suggestion[];
  context?: Record<string, unknown>;
};

type StreamUpdate = Record<string, Record<string, unknown> | null | undefined>;

const PUBLIC_AI_NAMES = new Set([
  "chat",
  "scout",
  "investigator",
  "reporter",
  "scope",
  "planner",
  "finding",
  "window",
  "hypothesis",
  "archive",
]);

function messageText(message: Message): string {
  return JSON.stringify(message.content);
}

function fallbackId(message: Message, nodeName: string, index: number): string {
  const input = `${nodeName}:${message.name ?? message.type}:${messageText(message)}:${index}`;
  let hash = 0;
  for (let index = 0; index < input.length; index += 1) {
    hash = (hash * 31 + input.charCodeAt(index)) >>> 0;
  }
  return `public-${hash.toString(16)}`;
}

function isPublicMessage(message: Message, root: boolean): boolean {
  if (message.type === "human") return root;
  if (message.type !== "ai") return false;
  if (!message.name || !PUBLIC_AI_NAMES.has(message.name)) return false;
  return !("tool_calls" in message && message.tool_calls?.length);
}

function mergeMessages(
  current: Message[],
  incoming: Message[],
  root: boolean,
  nodeName: string,
): Message[] {
  const merged = [...current];
  const positions = new Map(
    merged.map((message, index) => [message.id, index] as const),
  );

  incoming.forEach((message, index) => {
    if (message.type === "remove") {
      if (message.id && positions.has(message.id)) {
        merged.splice(positions.get(message.id)!, 1);
      }
      return;
    }
    if (!isPublicMessage(message, root)) return;

    const normalized = {
      ...message,
      id: message.id ?? fallbackId(message, nodeName, index),
    };
    if (positions.has(normalized.id)) {
      merged[positions.get(normalized.id)!] = normalized;
    } else {
      positions.set(normalized.id, merged.length);
      merged.push(normalized);
    }
  });

  return merged;
}

function applyUpdate(
  current: PublicStreamValues,
  update: StreamUpdate,
  root: boolean,
): PublicStreamValues {
  const next = { ...current };

  for (const [nodeName, nodeUpdate] of Object.entries(update)) {
    if (!nodeUpdate || typeof nodeUpdate !== "object") continue;
    if (Array.isArray(nodeUpdate.messages)) {
      next.messages = mergeMessages(
        next.messages,
        nodeUpdate.messages as Message[],
        root,
        nodeName,
      );
    }
    if ("progress" in nodeUpdate) {
      next.progress = nodeUpdate.progress as ProgressState | undefined;
    }
    if ("suggestions" in nodeUpdate) {
      next.suggestions = nodeUpdate.suggestions as Suggestion[] | undefined;
    }
    if ("context" in nodeUpdate) {
      next.context = nodeUpdate.context as Record<string, unknown> | undefined;
    }
    if ("ui" in nodeUpdate) {
      next.ui = nodeUpdate.ui as UIMessage[] | undefined;
    }
  }

  return next;
}

export function applyPublicUpdate(
  current: PublicStreamValues,
  update: StreamUpdate,
  namespace?: string[],
): PublicStreamValues {
  return applyUpdate(current, update, !namespace?.length);
}

export function applyPublicInput(
  current: PublicStreamValues,
  messages: Message[],
): PublicStreamValues {
  return applyUpdate(current, { input: { messages } }, true);
}

export function publicHistoryValues(
  values: Partial<PublicStreamValues> | null | undefined,
): PublicStreamValues {
  return applyUpdate(
    { messages: [] },
    {
      history: {
        ...values,
        messages: (values?.messages ?? []).filter((message) =>
          isPublicMessage(message, true),
        ),
      },
    },
    true,
  );
}

export function applyPublicUIEvent(
  current: PublicStreamValues,
  event: UIMessage | RemoveUIMessage,
): PublicStreamValues {
  if (!isUIMessage(event) && !isRemoveUIMessage(event)) return current;
  return { ...current, ui: uiMessageReducer(current.ui ?? [], event) };
}
