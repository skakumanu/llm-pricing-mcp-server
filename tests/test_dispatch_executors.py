"""Executor-layer tests for the dispatch gateway (v1.77.0).

Every provider call goes through httpx.MockTransport — no network. Per wire family:
request translation, usage normalisation, finish_reason mapping, and the error
classification table (401/403/404/429/5xx/timeout/context-too-long). The provider key
must appear in the outgoing auth header and nowhere else.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.dispatch_executors import (  # noqa: E402
    EVENT_DELTA, EVENT_FINISH, EVENT_USAGE, EXECUTABLE_PROVIDERS, OUTCOME_AUTH_ERROR, OUTCOME_CONTEXT_TOO_LONG,
    OUTCOME_MODEL_NOT_FOUND, OUTCOME_PROVIDER_REJECTED, OUTCOME_RATE_LIMITED, OUTCOME_SERVER_ERROR,
    OUTCOME_TIMEOUT, AnthropicExecutor, ChatRequest, ExecutorError, GeminiExecutor, HeaderKeyResolver,
    OpenAIWireExecutor, StreamEvent, classify_http_error, executor_spec_for, get_executor, is_executable_provider,
)

# Assembled at runtime so secret scanners do not flag the fixture; it is not a credential.
KEY = "-".join(["sk", "test", "SENTINEL", "123"])

MESSAGES = [
    {"role": "system", "content": "be terse"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi"},
    {"role": "user", "content": "PROMPT-SENTINEL-XYZ"},
]


def _capture_transport(handler):
    """MockTransport that records the last request and delegates the response to `handler`."""
    seen = {}

    def _handle(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return handler(request)

    return httpx.MockTransport(_handle), seen


def _json_response(status, body):
    return lambda request: httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# Registry + key resolver
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_nine_executable_providers_match_catalogue_display_names(self):
        assert set(EXECUTABLE_PROVIDERS) == {
            "OpenAI", "Anthropic", "Google", "Groq", "Together AI", "Fireworks AI", "DeepSeek", "Mistral AI", "xAI",
        }

    def test_lookup_is_case_insensitive(self):
        assert executor_spec_for("openai").provider == "OpenAI"
        assert executor_spec_for("MISTRAL AI").key_header == "X-Provider-Key-Mistral"
        assert executor_spec_for("Cohere") is None
        assert is_executable_provider("xAI") and not is_executable_provider("Amazon Bedrock")

    def test_key_headers_are_unique(self):
        headers = [s.key_header for s in EXECUTABLE_PROVIDERS.values()]
        assert len(headers) == len(set(headers))


class TestHeaderKeyResolver:
    def test_reads_only_known_headers_case_insensitively(self):
        r = HeaderKeyResolver({"x-provider-key-openai": KEY, "X-Provider-Key-XAI": " xk ", "X-Other": "no"})
        assert r.resolve("OpenAI") == KEY
        assert r.resolve("xai") == "xk"
        assert r.resolve("Anthropic") is None
        assert r.resolve("Cohere") is None
        assert r.providers_with_keys() == ["OpenAI", "xAI"]

    def test_blank_header_counts_as_missing(self):
        r = HeaderKeyResolver({"X-Provider-Key-Groq": "   "})
        assert r.resolve("Groq") is None

    def test_repr_never_shows_the_key(self):
        r = HeaderKeyResolver({"X-Provider-Key-OpenAI": KEY})
        assert KEY not in repr(r) and KEY not in str(r)
        assert "OpenAI" in repr(r)


# ---------------------------------------------------------------------------
# OpenAI wire family
# ---------------------------------------------------------------------------

def _openai_ok(finish="stop"):
    return {
        "id": "x", "model": "gpt-x", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "COMPLETION-SENTINEL-XYZ"},
             "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


class TestOpenAIWire:
    @pytest.mark.asyncio
    async def test_translation_and_result(self):
        transport, seen = _capture_transport(_json_response(200, _openai_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["Groq"], 5.0, transport=transport)
        assert isinstance(ex, OpenAIWireExecutor)
        res = await ex.execute(
            ChatRequest("llama-x", MESSAGES, max_tokens=50, temperature=0.2, top_p=0.9, stop=["END"]), KEY,
        )
        req = seen["request"]
        assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert req.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(req.content)
        assert body["model"] == "llama-x"
        assert body["messages"] == MESSAGES
        assert body["max_tokens"] == 50 and body["temperature"] == 0.2 and body["top_p"] == 0.9
        assert body["stop"] == ["END"] and body["stream"] is False
        assert res.content == "COMPLETION-SENTINEL-XYZ"
        assert res.finish_reason == "stop"
        assert (res.input_tokens, res.output_tokens) == (11, 7)
        assert res.http_status == 200 and res.provider_latency_ms >= 0
        assert res.raw_model == "gpt-x"
        # The key is in the auth header and nowhere else in the outgoing request.
        assert KEY not in req.content.decode()
        assert KEY not in str(req.url)

    @pytest.mark.asyncio
    async def test_openai_itself_uses_max_completion_tokens(self):
        # OpenAI's reasoning models (o-series, gpt-5*) reject `max_tokens` with 400; every current
        # OpenAI chat model accepts `max_completion_tokens`. Other OpenAI-wire hosts keep `max_tokens`.
        transport, seen = _capture_transport(_json_response(200, _openai_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        await ex.execute(ChatRequest("gpt-5", MESSAGES, max_tokens=64, temperature=0.3, top_p=0.8), KEY)
        body = json.loads(seen["request"].content)
        assert body["max_completion_tokens"] == 64 and "max_tokens" not in body
        assert body["temperature"] == 0.3 and body["top_p"] == 0.8   # non-reasoning: sampling forwarded

    @pytest.mark.asyncio
    async def test_openai_reasoning_model_drops_sampling_params(self):
        transport, seen = _capture_transport(_json_response(200, _openai_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        await ex.execute(
            ChatRequest("o3", MESSAGES, max_tokens=64, temperature=0.2, top_p=0.9, stop=["END"], reasoning_model=True),
            KEY,
        )
        body = json.loads(seen["request"].content)
        assert "temperature" not in body and "top_p" not in body
        assert body["max_completion_tokens"] == 64 and body["stop"] == ["END"]

    @pytest.mark.asyncio
    async def test_other_openai_wire_hosts_keep_sampling_for_reasoning_models(self):
        transport, seen = _capture_transport(_json_response(200, _openai_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["DeepSeek"], 5.0, transport=transport)
        await ex.execute(ChatRequest("deepseek-reasoner", MESSAGES, max_tokens=64, temperature=0.2,
                                     reasoning_model=True), KEY)
        body = json.loads(seen["request"].content)
        assert body["max_tokens"] == 64 and body["temperature"] == 0.2 and "max_completion_tokens" not in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("finish,expected", [("length", "length"), ("content_filter", "content_filter"),
                                                 ("tool_calls", "other"), (None, "other")])
    async def test_finish_reason_mapping(self, finish, expected):
        transport, _ = _capture_transport(_json_response(200, _openai_ok(finish)))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        res = await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert res.finish_reason == expected

    @pytest.mark.asyncio
    async def test_null_content_and_missing_usage_normalise(self):
        body = {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
        transport, _ = _capture_transport(_json_response(200, body))
        ex = get_executor(EXECUTABLE_PROVIDERS["DeepSeek"], 5.0, transport=transport)
        res = await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert res.content == "" and (res.input_tokens, res.output_tokens) == (0, 0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,body,outcome", [
        (401, {"error": {"message": "Incorrect API key provided: sk-xxx"}}, OUTCOME_AUTH_ERROR),
        (403, {"error": {"message": "forbidden"}}, OUTCOME_AUTH_ERROR),
        (404, {"error": {"message": "The model `nope` does not exist", "code": "model_not_found"}},
         OUTCOME_MODEL_NOT_FOUND),
        (429, {"error": {"message": "Rate limit reached"}}, OUTCOME_RATE_LIMITED),
        (500, {"error": {"message": "boom"}}, OUTCOME_SERVER_ERROR),
        (503, "not json", OUTCOME_SERVER_ERROR),
        (400, {"error": {"message": "This model's maximum context length is 8192 tokens.",
                         "code": "context_length_exceeded"}}, OUTCOME_CONTEXT_TOO_LONG),
        (400, {"error": {"message": "Your input exceeds the limit", "code": None}}, OUTCOME_CONTEXT_TOO_LONG),
        (400, {"error": {"message": "model_not_found: nope"}}, OUTCOME_MODEL_NOT_FOUND),
        (400, {"error": {"message": "Invalid value for 'temperature'"}}, OUTCOME_PROVIDER_REJECTED),
        (422, {"error": {"message": "bad field"}}, OUTCOME_PROVIDER_REJECTED),
    ])
    async def test_error_classification(self, status, body, outcome):
        if isinstance(body, str):
            transport, _ = _capture_transport(lambda r: httpx.Response(status, text=body))
        else:
            transport, _ = _capture_transport(_json_response(status, body))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert exc.value.outcome == outcome
        assert exc.value.http_status == status
        assert exc.value.retryable == (outcome not in (OUTCOME_AUTH_ERROR, OUTCOME_PROVIDER_REJECTED))
        # Classified, never copied: no provider message text and no key in the exception.
        text = str(exc.value)
        assert "maximum context length" not in text and "Incorrect API key" not in text and KEY not in text

    @pytest.mark.asyncio
    async def test_timeout_is_classified(self):
        def _raise(request):
            raise httpx.ReadTimeout("slow", request=request)
        transport, _ = _capture_transport(_raise)
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert exc.value.outcome == OUTCOME_TIMEOUT and exc.value.retryable

    @pytest.mark.asyncio
    async def test_connect_error_is_server_error(self):
        def _raise(request):
            raise httpx.ConnectError("refused", request=request)
        transport, _ = _capture_transport(_raise)
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert exc.value.outcome == OUTCOME_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_malformed_success_body_is_server_error(self):
        transport, _ = _capture_transport(_json_response(200, {"unexpected": True}))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert exc.value.outcome == OUTCOME_SERVER_ERROR


# ---------------------------------------------------------------------------
# Anthropic family
# ---------------------------------------------------------------------------

def _anthropic_ok(stop="end_turn"):
    return {
        "id": "msg", "model": "claude-x", "stop_reason": stop,
        "content": [{"type": "text", "text": "COMPLETION-"}, {"type": "text", "text": "SENTINEL-XYZ"}],
        "usage": {"input_tokens": 21, "output_tokens": 9},
    }


class TestAnthropic:
    @pytest.mark.asyncio
    async def test_translation_hoists_system_and_requires_max_tokens(self):
        transport, seen = _capture_transport(_json_response(200, _anthropic_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport, default_max_tokens=321)
        assert isinstance(ex, AnthropicExecutor)
        res = await ex.execute(ChatRequest("claude-x", MESSAGES, stop=["END"], temperature=0.1), KEY)
        req = seen["request"]
        assert str(req.url) == "https://api.anthropic.com/v1/messages"
        assert req.headers["x-api-key"] == KEY
        assert req.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in req.headers
        body = json.loads(req.content)
        assert body["system"] == "be terse"
        assert all(m["role"] in ("user", "assistant") for m in body["messages"])
        assert len(body["messages"]) == 3
        assert body["max_tokens"] == 321   # mandatory for Anthropic: default applied
        assert body["stop_sequences"] == ["END"] and body["temperature"] == 0.1
        assert res.content == "COMPLETION-SENTINEL-XYZ"
        assert (res.input_tokens, res.output_tokens) == (21, 9)
        assert res.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_explicit_max_tokens_wins(self):
        transport, seen = _capture_transport(_json_response(200, _anthropic_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        await ex.execute(ChatRequest("claude-x", MESSAGES, max_tokens=77), KEY)
        assert json.loads(seen["request"].content)["max_tokens"] == 77

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stop,expected", [("max_tokens", "length"), ("stop_sequence", "stop"),
                                               ("tool_use", "other")])
    async def test_stop_reason_mapping(self, stop, expected):
        transport, _ = _capture_transport(_json_response(200, _anthropic_ok(stop)))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        res = await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert res.finish_reason == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,body,outcome", [
        (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
         OUTCOME_AUTH_ERROR),
        (403, {"type": "error", "error": {"type": "permission_error", "message": "no"}}, OUTCOME_AUTH_ERROR),
        (404, {"type": "error", "error": {"type": "not_found_error", "message": "model: nope"}},
         OUTCOME_MODEL_NOT_FOUND),
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}, OUTCOME_RATE_LIMITED),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "overloaded"}}, OUTCOME_SERVER_ERROR),
        (400, {"type": "error", "error": {"type": "invalid_request_error",
                                          "message": "prompt is too long: 250000 tokens > 200000 maximum"}},
         OUTCOME_CONTEXT_TOO_LONG),
        (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "temperature out of range"}},
         OUTCOME_PROVIDER_REJECTED),
    ])
    async def test_error_classification(self, status, body, outcome):
        transport, _ = _capture_transport(_json_response(status, body))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert exc.value.outcome == outcome
        assert "prompt is too long" not in str(exc.value)


# ---------------------------------------------------------------------------
# Gemini family
# ---------------------------------------------------------------------------

def _gemini_ok(finish="STOP", thoughts=0):
    usage = {"promptTokenCount": 31, "candidatesTokenCount": 5}
    if thoughts:
        usage["thoughtsTokenCount"] = thoughts
    return {
        "modelVersion": "gemini-x-001",
        "candidates": [{"content": {"role": "model", "parts": [{"text": "COMPLETION-SENTINEL-XYZ"}]},
                        "finishReason": finish}],
        "usageMetadata": usage,
    }


class TestGemini:
    @pytest.mark.asyncio
    async def test_translation_and_result(self):
        transport, seen = _capture_transport(_json_response(200, _gemini_ok(thoughts=4)))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        assert isinstance(ex, GeminiExecutor)
        res = await ex.execute(ChatRequest("gemini-x", MESSAGES, max_tokens=40, top_p=0.5, stop=["END"]), KEY)
        req = seen["request"]
        assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
        assert req.headers["x-goog-api-key"] == KEY
        assert KEY not in str(req.url)
        body = json.loads(req.content)
        assert body["systemInstruction"] == {"parts": [{"text": "be terse"}]}
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
        assert body["contents"][0]["parts"] == [{"text": "hello"}]
        assert body["generationConfig"] == {"maxOutputTokens": 40, "topP": 0.5, "stopSequences": ["END"]}
        assert res.content == "COMPLETION-SENTINEL-XYZ"
        assert res.input_tokens == 31
        assert res.output_tokens == 9  # candidates + thoughts
        assert res.finish_reason == "stop"
        assert res.raw_model == "gemini-x-001"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("finish,expected", [("MAX_TOKENS", "length"), ("SAFETY", "content_filter"),
                                                 ("OTHER", "other")])
    async def test_finish_reason_mapping(self, finish, expected):
        transport, _ = _capture_transport(_json_response(200, _gemini_ok(finish)))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        res = await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert res.finish_reason == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,body,outcome", [
        (403, {"error": {"code": 403, "message": "PERMISSION_DENIED", "status": "PERMISSION_DENIED"}},
         OUTCOME_AUTH_ERROR),
        (400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                         "status": "INVALID_ARGUMENT"}}, OUTCOME_AUTH_ERROR),
        (404, {"error": {"code": 404, "message": "models/nope is not found", "status": "NOT_FOUND"}},
         OUTCOME_MODEL_NOT_FOUND),
        (429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}, OUTCOME_RATE_LIMITED),
        (503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}}, OUTCOME_SERVER_ERROR),
        (400, {"error": {"code": 400, "message": "The input token count (300000) exceeds the maximum number of "
                                                 "tokens allowed (128000).", "status": "INVALID_ARGUMENT"}},
         OUTCOME_CONTEXT_TOO_LONG),
        (400, {"error": {"code": 400, "message": "Invalid JSON payload", "status": "INVALID_ARGUMENT"}},
         OUTCOME_PROVIDER_REJECTED),
    ])
    async def test_error_classification(self, status, body, outcome):
        transport, _ = _capture_transport(_json_response(status, body))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await ex.execute(ChatRequest("m", MESSAGES), KEY)
        assert exc.value.outcome == outcome


class TestClassifyDirect:
    def test_unknown_family_still_classifies_status_codes(self):
        assert classify_http_error("weird", 401, None).outcome == OUTCOME_AUTH_ERROR
        assert classify_http_error("weird", 500, None).outcome == OUTCOME_SERVER_ERROR
        assert classify_http_error("weird", 400, {"error": "string error"}).outcome == OUTCOME_PROVIDER_REJECTED

    def test_get_executor_rejects_unknown_family(self):
        from src.services.dispatch_executors import ExecutorSpec
        with pytest.raises(ValueError):
            get_executor(ExecutorSpec("X", "nope", "https://x", "X-Provider-Key-X"))


# ---------------------------------------------------------------------------
# Streaming (v1.78.0) — recorded SSE fixtures per family over MockTransport (AC13a-d)
# ---------------------------------------------------------------------------

def _sse(*events):
    """Build an SSE body from (event_name | None, dict | raw-string) pairs."""
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}\n")
        out.append(f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n")
    return "".join(out).encode()


def _sse_response(body: bytes, status: int = 200):
    return lambda request: httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})


async def _collect(ex, req: ChatRequest, key: str = KEY):
    events = []
    async for ev in ex.stream(req, key):
        events.append(ev)
    return events


def _deltas(events):
    return [e.text for e in events if e.kind == EVENT_DELTA]


def _finish(events):
    reasons = [e.finish_reason for e in events if e.kind == EVENT_FINISH]
    assert len(reasons) == 1, reasons
    return reasons[0]


def _usage(events):
    inp = out = None
    for e in events:
        if e.kind == EVENT_USAGE:
            inp = e.input_tokens if e.input_tokens is not None else inp
            out = e.output_tokens if e.output_tokens is not None else out
    return inp, out


def _openai_chunk(delta=None, finish=None, usage=None, choices=True):
    chunk = {"id": "x", "object": "chat.completion.chunk", "model": "gpt-x", "usage": usage}
    chunk["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if choices else []
    return chunk


OPENAI_STREAM = _sse(
    (None, _openai_chunk({"role": "assistant", "content": ""})),
    (None, _openai_chunk({"content": "COMPLETION-"})),
    (None, _openai_chunk({"content": "SENTINEL-XYZ"})),
    (None, _openai_chunk({}, finish="stop")),
    (None, _openai_chunk(usage={"prompt_tokens": 11, "completion_tokens": 7}, choices=False)),
    (None, "[DONE]"),
)

_ANTHROPIC_MESSAGE = {"id": "msg", "model": "claude-x", "role": "assistant", "content": [], "stop_reason": None,
                      "usage": {"input_tokens": 21, "output_tokens": 1}}

ANTHROPIC_STREAM = _sse(
    ("message_start", {"type": "message_start", "message": _ANTHROPIC_MESSAGE}),
    ("ping", {"type": "ping"}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "COMPLETION-"}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "SENTINEL-XYZ"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 9}}),
    ("message_stop", {"type": "message_stop"}),
)


def _anthropic_stream(stop="end_turn"):
    return ANTHROPIC_STREAM.replace(b"end_turn", stop.encode())


def _gemini_stream(finish="STOP"):
    return _sse(
        (None, {"candidates": [{"content": {"role": "model", "parts": [{"text": "COMPLETION-"}]}, "index": 0}],
                "usageMetadata": {"promptTokenCount": 31}, "modelVersion": "gemini-x-001"}),
        (None, {"candidates": [{"content": {"role": "model", "parts": [{"text": "SENTINEL-"}, {"text": "XYZ"}]},
                                "finishReason": finish, "index": 0}],
                "usageMetadata": {"promptTokenCount": 31, "candidatesTokenCount": 5, "thoughtsTokenCount": 4},
                "modelVersion": "gemini-x-001"}),
    )


class TestOpenAIWireStreaming:
    @pytest.mark.asyncio
    async def test_ac13a_deltas_finish_usage_and_request_body(self):
        transport, seen = _capture_transport(_sse_response(OPENAI_STREAM))
        ex = get_executor(EXECUTABLE_PROVIDERS["Groq"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("llama-x", MESSAGES, max_tokens=50, temperature=0.2, stop=["END"]))
        assert _deltas(events) == ["COMPLETION-", "SENTINEL-XYZ"]
        assert _finish(events) == "stop"
        assert _usage(events) == (11, 7)
        # Order: every delta precedes the finish, which precedes the usage record.
        kinds = [e.kind for e in events]
        assert kinds.index(EVENT_FINISH) > max(i for i, k in enumerate(kinds) if k == EVENT_DELTA)
        assert kinds.index(EVENT_USAGE) > kinds.index(EVENT_FINISH)
        req = seen["request"]
        assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert req.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(req.content)
        assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
        assert body["max_tokens"] == 50 and body["temperature"] == 0.2 and body["stop"] == ["END"]
        assert KEY not in req.content.decode()

    @pytest.mark.asyncio
    async def test_ac13a_non_streaming_request_still_sends_stream_false(self):
        transport, seen = _capture_transport(_json_response(200, _openai_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        await ex.execute(ChatRequest("gpt-x", MESSAGES), KEY)
        body = json.loads(seen["request"].content)
        assert body["stream"] is False and "stream_options" not in body

    @pytest.mark.asyncio
    async def test_openai_itself_streams_with_max_completion_tokens(self):
        transport, seen = _capture_transport(_sse_response(OPENAI_STREAM))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        await _collect(ex, ChatRequest("gpt-5", MESSAGES, max_tokens=64, reasoning_model=True, temperature=0.3))
        body = json.loads(seen["request"].content)
        assert body["max_completion_tokens"] == 64 and "max_tokens" not in body and "temperature" not in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("finish,expected", [("length", "length"), ("content_filter", "content_filter"),
                                                 ("tool_calls", "other")])
    async def test_finish_reason_mapping(self, finish, expected):
        body = _sse((None, _openai_chunk({"content": "x"})), (None, _openai_chunk({}, finish=finish)),
                    (None, "[DONE]"))
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("m", MESSAGES))
        assert _finish(events) == expected

    @pytest.mark.asyncio
    async def test_content_parts_and_null_usage_normalise(self):
        parts = [{"type": "text", "text": "COMPLETION-"}, {"type": "text", "text": "X"}]
        body = _sse(
            (None, _openai_chunk({"content": parts})),
            (None, _openai_chunk({"content": None})),
            (None, _openai_chunk({}, finish="stop")),
            (None, "[DONE]"),
        )
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["DeepSeek"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("m", MESSAGES))
        assert _deltas(events) == ["COMPLETION-X"]
        assert _usage(events) == (None, None)   # no usage record: the service estimates

    @pytest.mark.asyncio
    async def test_multiline_data_and_comments_are_parsed_per_sse_spec(self):
        chunk = json.dumps(_openai_chunk({"content": "hi"}), indent=1)
        raw = b": keep-alive\n\n" + b"".join(f"data: {ln}\n".encode() for ln in chunk.splitlines()) + b"\n" \
            + b"data: " + json.dumps(_openai_chunk({}, finish="stop")).encode() + b"\n\ndata: [DONE]\n\n"
        transport, _ = _capture_transport(_sse_response(raw))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("m", MESSAGES))
        assert _deltas(events) == ["hi"] and _finish(events) == "stop"

    @pytest.mark.asyncio
    async def test_malformed_chunk_is_server_error(self):
        body = b"data: {not json\n\n"
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await _collect(ex, ChatRequest("m", MESSAGES))
        assert exc.value.outcome == OUTCOME_SERVER_ERROR and exc.value.http_status == 200
        assert "not json" not in str(exc.value)

    @pytest.mark.asyncio
    async def test_non_object_chunk_is_server_error(self):
        transport, _ = _capture_transport(_sse_response(b"data: [1, 2]\n\n"))
        ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await _collect(ex, ChatRequest("m", MESSAGES))
        assert exc.value.outcome == OUTCOME_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_mid_stream_error_object_raises_after_prior_deltas(self):
        body = _sse((None, _openai_chunk({"content": "COMPLETION-"})),
                    (None, {"error": {"message": "upstream exploded: PROVIDER-SECRET-DETAIL", "code": "x"}}))
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["Groq"], 5.0, transport=transport)
        got = []
        with pytest.raises(ExecutorError) as exc:
            async for ev in ex.stream(ChatRequest("m", MESSAGES), KEY):
                got.append(ev)
        assert _deltas(got) == ["COMPLETION-"]
        assert exc.value.outcome == OUTCOME_SERVER_ERROR
        assert "PROVIDER-SECRET-DETAIL" not in str(exc.value)

    @pytest.mark.asyncio
    async def test_timeout_and_connect_errors_are_classified(self):
        def _timeout(request):
            raise httpx.ReadTimeout("slow", request=request)

        def _refused(request):
            raise httpx.ConnectError("refused", request=request)
        for handler, outcome in ((_timeout, OUTCOME_TIMEOUT), (_refused, OUTCOME_SERVER_ERROR)):
            transport, _ = _capture_transport(handler)
            ex = get_executor(EXECUTABLE_PROVIDERS["OpenAI"], 5.0, transport=transport)
            with pytest.raises(ExecutorError) as exc:
                await _collect(ex, ChatRequest("m", MESSAGES))
            assert exc.value.outcome == outcome


class TestAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_ac13b_messages_stream_fixture(self):
        transport, seen = _capture_transport(_sse_response(ANTHROPIC_STREAM))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport, default_max_tokens=321)
        events = await _collect(ex, ChatRequest("claude-x", MESSAGES, stop=["END"]))
        assert _deltas(events) == ["COMPLETION-", "SENTINEL-XYZ"]
        assert _finish(events) == "stop"
        assert _usage(events) == (21, 9)       # input from message_start, output from message_delta
        # Input tokens are known before any text (the service buffers this pre-commit).
        kinds = [e.kind for e in events]
        assert kinds[0] == EVENT_USAGE and events[0].input_tokens == 21 and events[0].output_tokens is None
        req = seen["request"]
        assert str(req.url) == "https://api.anthropic.com/v1/messages"
        assert req.headers["x-api-key"] == KEY and req.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(req.content)
        assert body["stream"] is True and body["max_tokens"] == 321 and body["system"] == "be terse"
        assert body["stop_sequences"] == ["END"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stop,expected", [("max_tokens", "length"), ("stop_sequence", "stop"),
                                               ("tool_use", "other")])
    async def test_stop_reason_mapping(self, stop, expected):
        transport, _ = _capture_transport(_sse_response(_anthropic_stream(stop)))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("m", MESSAGES))
        assert _finish(events) == expected

    @pytest.mark.asyncio
    async def test_non_text_deltas_are_not_streamed(self):
        body = _sse(
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "thinking_delta", "thinking": "hmm"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                     "delta": {"type": "input_json_delta", "partial_json": "{"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2,
                                     "delta": {"type": "text_delta", "text": "visible"}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                               "usage": {"output_tokens": 3}}),
        )
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("m", MESSAGES))
        assert _deltas(events) == ["visible"]

    @pytest.mark.asyncio
    async def test_error_event_is_classified_and_dropped(self):
        body = _sse(
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "COMPLETION-"}}),
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded SECRET-DETAIL"}}),
        )
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        got = []
        with pytest.raises(ExecutorError) as exc:
            async for ev in ex.stream(ChatRequest("m", MESSAGES), KEY):
                got.append(ev)
        assert _deltas(got) == ["COMPLETION-"]
        assert exc.value.outcome == OUTCOME_SERVER_ERROR and "SECRET-DETAIL" not in str(exc.value)


class TestGeminiStreaming:
    @pytest.mark.asyncio
    async def test_ac13c_stream_generate_content_fixture(self):
        transport, seen = _capture_transport(_sse_response(_gemini_stream()))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("gemini-x", MESSAGES, max_tokens=40))
        assert _deltas(events) == ["COMPLETION-", "SENTINEL-XYZ"]   # parts of one chunk are joined
        assert _finish(events) == "stop"
        assert _usage(events) == (31, 9)   # last usageMetadata wins: candidates 5 + thoughts 4
        req = seen["request"]
        assert str(req.url) == (
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:streamGenerateContent?alt=sse"
        )
        assert req.headers["x-goog-api-key"] == KEY and KEY not in str(req.url)
        body = json.loads(req.content)
        assert body["generationConfig"] == {"maxOutputTokens": 40}
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]

    @pytest.mark.asyncio
    async def test_non_streaming_url_is_unchanged(self):
        transport, seen = _capture_transport(_json_response(200, _gemini_ok()))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        await ex.execute(ChatRequest("gemini-x", MESSAGES), KEY)
        assert str(seen["request"].url).endswith(":generateContent")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("finish,expected", [("MAX_TOKENS", "length"), ("SAFETY", "content_filter"),
                                                 ("OTHER", "other")])
    async def test_finish_reason_mapping(self, finish, expected):
        transport, _ = _capture_transport(_sse_response(_gemini_stream(finish)))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        events = await _collect(ex, ChatRequest("m", MESSAGES))
        assert _finish(events) == expected


class TestStreamingUsageUnknownIsNone:
    """A count the chunk did not carry is reported as None (unknown), never 0: the service only
    falls back to its own estimate (and flags the usage as estimated) for None, so a stream that
    fails or is abandoned after an early usage record must not bill 0 output tokens."""

    @pytest.mark.asyncio
    async def test_gemini_early_usage_leaves_output_unknown(self):
        transport, _ = _capture_transport(_sse_response(_gemini_stream()))
        ex = get_executor(EXECUTABLE_PROVIDERS["Google"], 5.0, transport=transport)
        usages = [e for e in await _collect(ex, ChatRequest("gemini-x", MESSAGES)) if e.kind == EVENT_USAGE]
        assert len(usages) == 2
        assert (usages[0].input_tokens, usages[0].output_tokens) == (31, None)   # promptTokenCount only
        assert (usages[1].input_tokens, usages[1].output_tokens) == (31, 9)      # candidates 5 + thoughts 4

    @pytest.mark.asyncio
    async def test_openai_wire_usage_missing_prompt_tokens_is_unknown(self):
        body = _sse(
            (None, _openai_chunk({"content": "hi"})),
            (None, _openai_chunk({}, finish="stop")),
            (None, _openai_chunk(usage={"completion_tokens": 7}, choices=False)),
            (None, "[DONE]"),
        )
        transport, _ = _capture_transport(_sse_response(body))
        ex = get_executor(EXECUTABLE_PROVIDERS["Groq"], 5.0, transport=transport)
        usages = [e for e in await _collect(ex, ChatRequest("m", MESSAGES)) if e.kind == EVENT_USAGE]
        assert len(usages) == 1 and (usages[0].input_tokens, usages[0].output_tokens) == (None, 7)


_STREAM_ERROR_CASES = [
    ("OpenAI", 401, {"error": {"message": "Incorrect API key provided: sk-xxx"}}, OUTCOME_AUTH_ERROR),
    ("OpenAI", 403, {"error": {"message": "forbidden"}}, OUTCOME_AUTH_ERROR),
    ("OpenAI", 429, {"error": {"message": "Rate limit reached"}}, OUTCOME_RATE_LIMITED),
    ("OpenAI", 503, {"error": {"message": "overloaded"}}, OUTCOME_SERVER_ERROR),
    ("OpenAI", 404, {"error": {"message": "The model `nope` does not exist", "code": "model_not_found"}},
     OUTCOME_MODEL_NOT_FOUND),
    ("OpenAI", 400, {"error": {"message": "This model's maximum context length is 8192 tokens.",
                               "code": "context_length_exceeded"}}, OUTCOME_CONTEXT_TOO_LONG),
    ("OpenAI", 400, {"error": {"message": "model_not_found: nope"}}, OUTCOME_MODEL_NOT_FOUND),
    ("OpenAI", 400, {"error": {"message": "Invalid value for 'temperature'"}}, OUTCOME_PROVIDER_REJECTED),
    ("Anthropic", 401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
     OUTCOME_AUTH_ERROR),
    ("Anthropic", 429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
     OUTCOME_RATE_LIMITED),
    ("Anthropic", 529, {"type": "error", "error": {"type": "overloaded_error", "message": "overloaded"}},
     OUTCOME_SERVER_ERROR),
    ("Anthropic", 404, {"type": "error", "error": {"type": "not_found_error", "message": "model: nope"}},
     OUTCOME_MODEL_NOT_FOUND),
    ("Anthropic", 400, {"type": "error", "error": {"type": "invalid_request_error",
                                                   "message": "prompt is too long: 250000 > 200000 maximum"}},
     OUTCOME_CONTEXT_TOO_LONG),
    ("Anthropic", 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "temperature"}},
     OUTCOME_PROVIDER_REJECTED),
    ("Google", 403, {"error": {"code": 403, "message": "PERMISSION_DENIED"}}, OUTCOME_AUTH_ERROR),
    ("Google", 400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}},
     OUTCOME_AUTH_ERROR),
    ("Google", 429, {"error": {"code": 429, "message": "quota"}}, OUTCOME_RATE_LIMITED),
    ("Google", 503, {"error": {"code": 503, "message": "unavailable"}}, OUTCOME_SERVER_ERROR),
    ("Google", 404, {"error": {"code": 404, "message": "models/nope is not found"}}, OUTCOME_MODEL_NOT_FOUND),
    ("Google", 400, {"error": {"code": 400, "message": "The input token count (300000) exceeds the maximum number "
                                                       "of tokens allowed (128000)."}}, OUTCOME_CONTEXT_TOO_LONG),
    ("Google", 400, {"error": {"code": 400, "message": "Invalid JSON payload"}}, OUTCOME_PROVIDER_REJECTED),
]


class TestStreamingErrorClassification:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider,status,body,outcome", _STREAM_ERROR_CASES)
    async def test_ac13d_non_2xx_classified_before_first_yield(self, provider, status, body, outcome):
        transport, _ = _capture_transport(_json_response(status, body))
        ex = get_executor(EXECUTABLE_PROVIDERS[provider], 5.0, transport=transport)
        got = []
        with pytest.raises(ExecutorError) as exc:
            async for ev in ex.stream(ChatRequest("m", MESSAGES), KEY):
                got.append(ev)
        assert got == []                                    # nothing yielded: the service can fail over
        assert exc.value.outcome == outcome and exc.value.http_status == status
        # Same answer the non-streaming executor gives for the same response.
        assert classify_http_error(ex.spec.family, status, body).outcome == outcome
        text = str(exc.value)
        assert "maximum context" not in text and "API key" not in text and KEY not in text

    @pytest.mark.asyncio
    async def test_non_json_error_body_is_still_classified(self):
        transport, _ = _capture_transport(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
        ex = get_executor(EXECUTABLE_PROVIDERS["Anthropic"], 5.0, transport=transport)
        with pytest.raises(ExecutorError) as exc:
            await _collect(ex, ChatRequest("m", MESSAGES))
        assert exc.value.outcome == OUTCOME_SERVER_ERROR and exc.value.http_status == 502


def test_stream_event_defaults():
    ev = StreamEvent(EVENT_DELTA, text="x")
    assert (ev.finish_reason, ev.input_tokens, ev.output_tokens) == (None, None, None)
