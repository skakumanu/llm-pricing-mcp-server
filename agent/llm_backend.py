"""LLM backend abstraction: Anthropic and OpenAI implementations."""
import json
import logging
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Normalized response from an LLM backend."""
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"
    # Raw provider content — used by append_assistant_turn to avoid re-serialising
    _raw_content: Any = None


class LLMBackend(ABC):
    """Abstract LLM backend that supports chat completion with optional tool use."""

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
    ) -> LLMResponse:
        """Send messages to the LLM and return a normalized response."""

    async def complete_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream an LLM response, yielding token and response events.

        Yields dicts of two types:
          {"type": "token",    "text": str}          — one per text delta
          {"type": "response", "response": LLMResponse} — exactly once at the end

        Default implementation: no token events, just the final response.
        Backends override this for real token-level streaming.
        """
        response = await self.complete(messages, tools, system)
        yield {"type": "response", "response": response}

    def append_assistant_turn(
        self, messages: List[Dict[str, Any]], response: LLMResponse
    ) -> None:
        """Append the assistant's turn (with tool calls) to the message list."""
        messages.append({"role": "assistant", "content": response.content})

    def append_tool_results(
        self,
        messages: List[Dict[str, Any]],
        tool_calls_with_results: List[Dict[str, Any]],
    ) -> None:
        """Append tool results as a user message (text format fallback)."""
        results_text = "\n".join(
            f"Tool '{r['tool']}' result: {json.dumps(r['result'])}"
            for r in tool_calls_with_results
        )
        messages.append({
            "role": "user",
            "content": (
                f"Tool results:\n{results_text}\n\n"
                "Based on these results, please provide your answer or use additional tools if needed."
            ),
        })


class AnthropicBackend(LLMBackend):
    """Claude backend using the Anthropic Python SDK."""

    def __init__(self, api_key: str, model: str):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
    ) -> LLMResponse:
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(timeout=60.0, **kwargs)

        text_content = ""
        tool_calls: List[Dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "tool": block.name,
                    "args": block.input,
                })

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
            _raw_content=response.content,
        )

    async def complete_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream Claude's response token by token via the Anthropic streaming API."""
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        final_message = None
        async with self._client.messages.stream(timeout=60.0, **kwargs) as stream:
            async for text_chunk in stream.text_stream:
                yield {"type": "token", "text": text_chunk}
            final_message = await stream.get_final_message()

        text_content = ""
        tool_calls: List[Dict[str, Any]] = []
        for block in final_message.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "tool": block.name,
                    "args": block.input,
                })

        yield {
            "type": "response",
            "response": LLMResponse(
                content=text_content,
                tool_calls=tool_calls,
                stop_reason=final_message.stop_reason or "end_turn",
                _raw_content=final_message.content,
            ),
        }

    def append_assistant_turn(
        self, messages: List[Dict[str, Any]], response: LLMResponse
    ) -> None:
        """Use raw Anthropic content blocks for proper tool_use format."""
        messages.append({"role": "assistant", "content": response._raw_content})

    def append_tool_results(
        self,
        messages: List[Dict[str, Any]],
        tool_calls_with_results: List[Dict[str, Any]],
    ) -> None:
        """Append tool results as Anthropic tool_result blocks."""
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": r["id"],
                "content": json.dumps(r["result"]),
            }
            for r in tool_calls_with_results
        ]
        messages.append({"role": "user", "content": tool_result_blocks})


class OpenAIBackend(LLMBackend):
    """GPT backend using the OpenAI Python SDK."""

    def __init__(self, api_key: str, model: str):
        import openai
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
    ) -> LLMResponse:
        all_messages: List[Dict[str, Any]] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": all_messages,
        }
        if tools:
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]
            kwargs["tools"] = openai_tools

        response = await self._client.chat.completions.create(timeout=60.0, **kwargs)
        choice = response.choices[0]
        message = choice.message

        text_content = message.content or ""
        tool_calls: List[Dict[str, Any]] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "OpenAI returned malformed JSON for tool '%s' args: %r",
                        tc.function.name,
                        tc.function.arguments,
                    )
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "tool": tc.function.name,
                    "args": args,
                })

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "stop",
            _raw_content=message,
        )

    async def complete_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream GPT's response token by token via the OpenAI streaming API."""
        all_messages: List[Dict[str, Any]] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": all_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]

        accumulated_text = ""
        accumulated_tool_calls: Dict[int, Dict[str, str]] = {}
        finish_reason = "stop"

        stream = await self._client.chat.completions.create(timeout=60.0, **kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                accumulated_text += delta.content
                yield {"type": "token", "text": delta.content}

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        accumulated_tool_calls[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            accumulated_tool_calls[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            accumulated_tool_calls[idx]["args"] += tc_delta.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        # Build normalised tool_calls list
        tool_calls: List[Dict[str, Any]] = []
        for idx in sorted(accumulated_tool_calls.keys()):
            tc = accumulated_tool_calls[idx]
            try:
                args = json.loads(tc["args"]) if tc["args"] else {}
            except json.JSONDecodeError:
                logger.warning(
                    "OpenAI streaming: malformed tool args for '%s': %r",
                    tc["name"], tc["args"],
                )
                args = {}
            tool_calls.append({"id": tc["id"], "tool": tc["name"], "args": args})

        # Build _raw_content compatible with append_assistant_turn
        raw_tool_calls = [
            types.SimpleNamespace(
                id=accumulated_tool_calls[idx]["id"],
                function=types.SimpleNamespace(
                    name=accumulated_tool_calls[idx]["name"],
                    arguments=accumulated_tool_calls[idx]["args"],
                ),
            )
            for idx in sorted(accumulated_tool_calls.keys())
        ] or None

        yield {
            "type": "response",
            "response": LLMResponse(
                content=accumulated_text,
                tool_calls=tool_calls,
                stop_reason=finish_reason,
                _raw_content=types.SimpleNamespace(
                    content=accumulated_text,
                    tool_calls=raw_tool_calls,
                ),
            ),
        }

    def append_assistant_turn(
        self, messages: List[Dict[str, Any]], response: LLMResponse
    ) -> None:
        """Append raw OpenAI assistant message (preserves tool_calls field)."""
        raw = response._raw_content
        msg: Dict[str, Any] = {"role": "assistant", "content": raw.content or ""}
        if raw.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in raw.tool_calls
            ]
        messages.append(msg)

    def append_tool_results(
        self,
        messages: List[Dict[str, Any]],
        tool_calls_with_results: List[Dict[str, Any]],
    ) -> None:
        """Append each tool result as a separate 'tool' role message."""
        for r in tool_calls_with_results:
            messages.append({
                "role": "tool",
                "tool_call_id": r["id"],
                "content": json.dumps(r["result"]),
            })


def create_llm_backend(provider: str, api_key: str, model: str) -> LLMBackend:
    """Factory: create the appropriate LLM backend for the configured provider."""
    if provider == "anthropic":
        return AnthropicBackend(api_key=api_key, model=model)
    if provider == "openai":
        return OpenAIBackend(api_key=api_key, model=model)
    raise ValueError(
        f"Unknown LLM provider: '{provider}'. Supported values: 'anthropic', 'openai'."
    )
