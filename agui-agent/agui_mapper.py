"""Детерминированное преобразование Aegra events в AG-UI events."""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from ag_ui.core import (
    BaseEvent,
    EventType,
    RawEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from aegra_client import SSEEvent

PRIVATE_CHANNELS = ("scout_msgs", "inv_msgs", "rep_msgs")


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    return ""


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class AegraAguiMapper:
    def __init__(self, thread_id: str, run_id: str, known_message_ids: set[str]):
        self.thread_id = thread_id
        self.run_id = run_id
        self.known_message_ids = known_message_ids
        self.emitted_text: set[str] = set()
        self.emitted_tool_calls: set[str] = set()
        self.emitted_tool_results: set[str] = set()
        self.aegra_run_id = ""

    def started(self) -> RunStartedEvent:
        return RunStartedEvent(type=EventType.RUN_STARTED, threadId=self.thread_id, runId=self.run_id)

    def finished(self, result: Any = None) -> RunFinishedEvent:
        return RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            threadId=self.thread_id,
            runId=self.run_id,
            result=result,
            metadata={"aegra_run_id": self.aegra_run_id} if self.aegra_run_id else None,
        )

    def error(self, error: Exception) -> RunErrorEvent:
        return RunErrorEvent(type=EventType.RUN_ERROR, message=str(error) or type(error).__name__)

    def _text_events(self, message: dict) -> Iterator[BaseEvent]:
        message_id = str(message.get("id") or "")
        content = message_text(message.get("content"))
        if not message_id or not content or message_id in self.known_message_ids or message_id in self.emitted_text:
            return
        self.emitted_text.add(message_id)
        yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, messageId=message_id, role="assistant")
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, messageId=message_id, delta=content)
        yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, messageId=message_id)

    def _private_events(self, message: dict) -> Iterator[BaseEvent]:
        message_type = message.get("type") or message.get("role")
        message_id = str(message.get("id") or "")
        if message_type in ("ai", "assistant"):
            for call in message.get("tool_calls") or []:
                call_id = str(call.get("id") or "")
                if not call_id or call_id in self.emitted_tool_calls:
                    continue
                self.emitted_tool_calls.add(call_id)
                yield ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START,
                    toolCallId=call_id,
                    toolCallName=str(call.get("name") or "tool"),
                    parentMessageId=message_id or None,
                )
                yield ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    toolCallId=call_id,
                    delta=json_text(call.get("args") or {}),
                )
                yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, toolCallId=call_id)
        if message_type == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id and call_id not in self.emitted_tool_results:
                self.emitted_tool_results.add(call_id)
                yield ToolCallResultEvent(
                    type=EventType.TOOL_CALL_RESULT,
                    messageId=message_id or f"tool-result-{call_id}",
                    toolCallId=call_id,
                    content=message_text(message.get("content")) or json_text(message.get("content")),
                    role="tool",
                )

    def values(self, values: dict, raw: Any) -> Iterator[BaseEvent]:
        yield StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=values, rawEvent=raw)
        for message in values.get("messages") or []:
            if (message.get("type") or message.get("role")) in ("ai", "assistant"):
                yield from self._text_events(message)
        for channel in PRIVATE_CHANNELS:
            for message in values.get(channel) or []:
                yield from self._private_events(message)

    def source_event(self, event: SSEEvent) -> Iterator[BaseEvent]:
        if event.event == "metadata" and isinstance(event.data, dict):
            self.aegra_run_id = str(event.data.get("run_id") or self.aegra_run_id)
        yield RawEvent(type=EventType.RAW, source="aegra", event={"event": event.event, "id": event.event_id, "data": event.data})
        if event.event == "values" and isinstance(event.data, dict):
            yield from self.values(event.data, event.data)
        elif event.event == "updates" and isinstance(event.data, dict):
            for node in event.data:
                yield StepStartedEvent(type=EventType.STEP_STARTED, stepName=node)
                yield StepFinishedEvent(type=EventType.STEP_FINISHED, stepName=node)
        elif event.event == "error":
            message = event.data.get("message") if isinstance(event.data, dict) else str(event.data)
            yield RunErrorEvent(type=EventType.RUN_ERROR, message=message or "Aegra run failed")


async def map_stream(
    thread_id: str,
    run_id: str,
    known_message_ids: set[str],
    source: AsyncIterator[SSEEvent],
) -> AsyncIterator[BaseEvent]:
    mapper = AegraAguiMapper(thread_id, run_id, known_message_ids)
    yield mapper.started()
    try:
        async for event in source:
            for mapped in mapper.source_event(event):
                yield mapped
            if event.event == "end":
                status = event.data.get("status") if isinstance(event.data, dict) else None
                if status in ("error", "failed", "timeout"):
                    yield RunErrorEvent(type=EventType.RUN_ERROR, message=f"Aegra run: {status}")
                else:
                    yield mapper.finished(event.data)
                return
        yield mapper.finished()
    except Exception as exc:
        yield mapper.error(exc)
