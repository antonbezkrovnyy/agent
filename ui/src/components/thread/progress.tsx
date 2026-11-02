import { useState } from "react";
import { v4 as uuidv4 } from "uuid";
import { useStreamContext } from "@/providers/Stream";
import type { ProgressState, Suggestion } from "@/providers/Stream";

const agentNames: Record<string, string> = {
  scout: "Скаут",
  investigator: "Инвестигейтор",
  reporter: "Репортёр",
};

export function ProgressView({ progress }: { progress?: ProgressState }) {
  if (!progress) return null;

  return (
    <div className="mr-auto flex max-w-full flex-col gap-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
      <span>{progress.text}</span>
      {progress.agent && progress.tool_limit !== undefined && (
        <span className="text-xs text-gray-500">
          {agentNames[progress.agent] ?? progress.agent}: {progress.tool_calls ?? 0}/{progress.tool_limit} вызовов в этом запуске
        </span>
      )}
    </div>
  );
}

export function SuggestionButtons({ suggestions }: { suggestions?: Suggestion[] }) {
  const thread = useStreamContext();
  const [submitted, setSubmitted] = useState(false);
  if (!suggestions?.length) return null;

  const send = (value: string) => {
    setSubmitted(true);
    thread.submit(
      {
        messages: [
          {
            id: uuidv4(),
            type: "human",
            content: [{ type: "text", text: value }],
          },
        ],
      },
      {
        streamMode: ["values"],
        streamSubgraphs: true,
        streamResumable: true,
      },
    );
  };

  return (
    <div className="mr-auto flex flex-wrap gap-2">
      {suggestions.map((suggestion) => (
        <button
          key={suggestion.value}
          type="button"
          disabled={submitted || thread.isLoading}
          onClick={() => send(suggestion.value)}
          className="cursor-pointer rounded-md border border-teal-700 px-3 py-1.5 text-sm text-teal-700 transition-colors hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {suggestion.label}
        </button>
      ))}
    </div>
  );
}
