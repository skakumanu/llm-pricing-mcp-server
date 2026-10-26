"""Provider executor layer for the dispatch gateway (v1.77.0).

One common request/result shape, three wire families, one error classification
table. Every provider is reached with a bare ``httpx.AsyncClient`` rather than a
vendor SDK: the pinned SDKs auto-retry 429/5xx and would fight the failover loop
and its timeout budget, no Gemini SDK is pinned at all, and one HTTP path gives
one test seam (``httpx.MockTransport``).

Privacy by construction:
  * The caller's provider key enters this module through ``HeaderKeyResolver``
    and leaves only as the outgoing auth header of the provider call. It is never
    stored on an object that is logged, never part of an exception, never in a
    receipt.
  * Provider error bodies are *classified* into an ``ExecutorError.outcome`` and
    then dropped — never copied into an exception message, a log line or a
    response.
  * Prompt and completion text exist only inside ``ChatRequest`` / ``ChatResult``
    and are never logged here at any level.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"

# Outcomes an attempt can end in. ``ok`` is the success marker in receipts.
OUTCOME_OK = "ok"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_SERVER_ERROR = "server_error"
OUTCOME_CONTEXT_TOO_LONG = "context_too_long"
OUTCOME_AUTH_ERROR = "auth_error"
OUTCOME_MODEL_NOT_FOUND = "model_not_found"
OUTCOME_PROVIDER_REJECTED = "provider_rejected"

# Failover-eligible outcomes (the loop moves on to the next candidate).
RETRYABLE_OUTCOMES = frozenset({
    OUTCOME_TIMEOUT, OUTCOME_RATE_LIMITED, OUTCOME_SERVER_ERROR,
    OUTCOME_CONTEXT_TOO_LONG, OUTCOME_MODEL_NOT_FOUND,
})


# ---------------------------------------------------------------------------
# Common model
# ---------------------------------------------------------------------------

@dataclass
class ChatRequest:
    """Provider-neutral chat request. ``messages`` are ``{role, content: str}`` only."""

    model: str
    messages: List[Dict[str, str]]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    # Catalogue flag (PricingMetrics.is_reasoning_model). OpenAI's reasoning models reject
    # any non-default temperature/top_p with HTTP 400, so the OpenAI executor drops them.
    reasoning_model: bool = False


@dataclass
class ChatResult:
    """Normalised completion. ``finish_reason`` is one of stop | length | content_filter | other."""

    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    provider_latency_ms: float
    http_status: int
    raw_model: Optional[str] = None


class ExecutorError(Exception):
    """A classified provider failure. Carries no provider body, no key, no prompt."""

    def __init__(self, outcome: str, http_status: Optional[int] = None):
        self.outcome = outcome
        self.http_status = http_status
        self.retryable = outcome in RETRYABLE_OUTCOMES
        super().__init__(f"provider call failed: {outcome}" + (f" (HTTP {http_status})" if http_status else ""))


# ---------------------------------------------------------------------------
# Registry of executable providers
# ---------------------------------------------------------------------------

FAMILY_OPENAI_WIRE = "openai_wire"
FAMILY_ANTHROPIC = "anthropic_messages"
FAMILY_GEMINI = "gemini_rest"


@dataclass(frozen=True)
class ExecutorSpec:
    provider: str          # the catalogue's exact provider display name
    family: str
    base_url: str
    key_header: str        # request header the caller supplies the provider key in


# Keyed by the catalogue's provider display names (see each *_pricing.py constructor).
EXECUTABLE_PROVIDERS: Dict[str, ExecutorSpec] = {
    "OpenAI": ExecutorSpec("OpenAI", FAMILY_OPENAI_WIRE, "https://api.openai.com/v1", "X-Provider-Key-OpenAI"),
    "Groq": ExecutorSpec("Groq", FAMILY_OPENAI_WIRE, "https://api.groq.com/openai/v1", "X-Provider-Key-Groq"),
    "Together AI": ExecutorSpec(
        "Together AI", FAMILY_OPENAI_WIRE, "https://api.together.xyz/v1", "X-Provider-Key-Together"
    ),
    "Fireworks AI": ExecutorSpec(
        "Fireworks AI", FAMILY_OPENAI_WIRE, "https://api.fireworks.ai/inference/v1", "X-Provider-Key-Fireworks"
    ),
    "DeepSeek": ExecutorSpec("DeepSeek", FAMILY_OPENAI_WIRE, "https://api.deepseek.com/v1", "X-Provider-Key-DeepSeek"),
    "Mistral AI": ExecutorSpec(
        "Mistral AI", FAMILY_OPENAI_WIRE, "https://api.mistral.ai/v1", "X-Provider-Key-Mistral"
    ),
    "xAI": ExecutorSpec("xAI", FAMILY_OPENAI_WIRE, "https://api.x.ai/v1", "X-Provider-Key-XAI"),
    "Anthropic": ExecutorSpec("Anthropic", FAMILY_ANTHROPIC, "https://api.anthropic.com", "X-Provider-Key-Anthropic"),
    "Google": ExecutorSpec(
        "Google", FAMILY_GEMINI, "https://generativelanguage.googleapis.com/v1beta", "X-Provider-Key-Google"
    ),
}

# Lower-cased lookup so a provider string from any source resolves to the canonical spec.
_PROVIDER_BY_LOWER = {name.lower(): spec for name, spec in EXECUTABLE_PROVIDERS.items()}


def executor_spec_for(provider: Optional[str]) -> Optional[ExecutorSpec]:
    """Return the ExecutorSpec for a catalogue provider name, or None if recommend-only."""
    if not provider:
        return None
    return _PROVIDER_BY_LOWER.get(provider.strip().lower())


def is_executable_provider(provider: Optional[str]) -> bool:
    return executor_spec_for(provider) is not None


# ---------------------------------------------------------------------------
# Key resolution — the BYOK seam
# ---------------------------------------------------------------------------

class ProviderKeyResolver(Protocol):
    """Where a provider key for this request comes from. v1 ships the header resolver only;
    a stored-per-account resolver would implement the same one method later."""

    def resolve(self, provider: str) -> Optional[str]: ...

    def providers_with_keys(self) -> List[str]: ...


class HeaderKeyResolver:
    """Reads ``X-Provider-Key-<Provider>`` headers once; holds them only for this request.

    ``repr()``/``str()`` never show a key — only which providers have one.
    """

    def __init__(self, headers: Mapping[str, str]) -> None:
        # Starlette's Headers is case-insensitive; a plain dict from tests may not be, so
        # normalise through a lower-cased view.
        lowered = {str(k).lower(): v for k, v in headers.items()}
        self._keys: Dict[str, str] = {}
        for spec in EXECUTABLE_PROVIDERS.values():
            value = lowered.get(spec.key_header.lower())
            if value and str(value).strip():
                self._keys[spec.provider] = str(value).strip()

    def resolve(self, provider: str) -> Optional[str]:
        spec = executor_spec_for(provider)
        if spec is None:
            return None
        return self._keys.get(spec.provider)

    def providers_with_keys(self) -> List[str]:
        return sorted(self._keys.keys())

    def __repr__(self) -> str:  # redacted on purpose
        return f"HeaderKeyResolver(providers={self.providers_with_keys()})"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_CONTEXT_RE_OPENAI = re.compile(
    r"maximum context length|context window|too many tokens|exceeds the limit|context_length_exceeded", re.I
)
_CONTEXT_RE_ANTHROPIC = re.compile(r"prompt is too long|exceeds the maximum", re.I)
_CONTEXT_RE_GEMINI = re.compile(r"token count|exceeds the maximum number of tokens|input is too long", re.I)
_MODEL_NOT_FOUND_RE = re.compile(
    r"model[_ ]not[_ ]found|does not exist|is not found|unknown model|no such model", re.I
)
_GEMINI_BAD_KEY_RE = re.compile(r"API key not valid|API_KEY_INVALID|API key expired", re.I)


def _safe_json(response: httpx.Response) -> Optional[dict]:
    try:
        data = response.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _error_fields(body: Optional[dict]) -> Dict[str, str]:
    """Pull code/type/message/status out of the provider's error envelope, as strings.

    The message is used for pattern matching only and is dropped afterwards.
    """
    out = {"code": "", "type": "", "message": "", "status": ""}
    if not body:
        return out
    err = body.get("error")
    if isinstance(err, dict):
        for k in out:
            v = err.get(k)
            if v is not None:
                out[k] = str(v)
    elif isinstance(err, str):
        out["message"] = err
    # Anthropic nests {"type": "error", "error": {"type": ..., "message": ...}}
    if not out["type"] and isinstance(body.get("type"), str):
        out["type"] = body["type"]
    return out


def classify_http_error(family: str, status: int, body: Optional[dict]) -> ExecutorError:
    """Map an HTTP error response to an ExecutorError outcome. Never keeps the body."""
    fields = _error_fields(body)
    message = fields["message"]
    code = fields["code"]

    if status in (401, 403):
        return ExecutorError(OUTCOME_AUTH_ERROR, status)
    if status == 429:
        return ExecutorError(OUTCOME_RATE_LIMITED, status)
    if status >= 500:
        return ExecutorError(OUTCOME_SERVER_ERROR, status)
    if status == 404:
        return ExecutorError(OUTCOME_MODEL_NOT_FOUND, status)

    if status == 400 or status == 422 or status == 413:
        if family == FAMILY_OPENAI_WIRE:
            if code in ("context_length_exceeded", "string_above_max_length") or _CONTEXT_RE_OPENAI.search(message):
                return ExecutorError(OUTCOME_CONTEXT_TOO_LONG, status)
            if code == "model_not_found" or _MODEL_NOT_FOUND_RE.search(message):
                return ExecutorError(OUTCOME_MODEL_NOT_FOUND, status)
        elif family == FAMILY_ANTHROPIC:
            if _CONTEXT_RE_ANTHROPIC.search(message):
                return ExecutorError(OUTCOME_CONTEXT_TOO_LONG, status)
            if fields["type"] == "not_found_error" or _MODEL_NOT_FOUND_RE.search(message):
                return ExecutorError(OUTCOME_MODEL_NOT_FOUND, status)
        elif family == FAMILY_GEMINI:
            # Gemini reports a bad key as 400 INVALID_ARGUMENT, not 401.
            if _GEMINI_BAD_KEY_RE.search(message):
                return ExecutorError(OUTCOME_AUTH_ERROR, status)
            if _CONTEXT_RE_GEMINI.search(message):
                return ExecutorError(OUTCOME_CONTEXT_TOO_LONG, status)
            if _MODEL_NOT_FOUND_RE.search(message):
                return ExecutorError(OUTCOME_MODEL_NOT_FOUND, status)

    return ExecutorError(OUTCOME_PROVIDER_REJECTED, status)


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

@dataclass
class BaseExecutor:
    spec: ExecutorSpec
    timeout_s: float = 30.0
    # Test seam: an httpx transport (httpx.MockTransport) replaces the network.
    transport: Optional[httpx.AsyncBaseTransport] = field(default=None, repr=False)

    async def execute(self, req: ChatRequest, api_key: str) -> ChatResult:
        url, headers, body = self.build(req, api_key)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            raise ExecutorError(OUTCOME_TIMEOUT)
        except httpx.HTTPError:
            # Connect/read/protocol failures — treated like an unavailable server.
            raise ExecutorError(OUTCOME_SERVER_ERROR)
        latency_ms = (time.perf_counter() - started) * 1000.0

        if response.status_code >= 400:
            err = classify_http_error(self.spec.family, response.status_code, _safe_json(response))
            logger.debug(
                "dispatch executor %s: %s (HTTP %d, %.0f ms)",
                self.spec.provider, err.outcome, response.status_code, latency_ms,
            )
            raise err

        data = _safe_json(response)
        if data is None:
            raise ExecutorError(OUTCOME_SERVER_ERROR, response.status_code)
        try:
            result = self.parse(data, latency_ms, response.status_code)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ExecutorError(OUTCOME_SERVER_ERROR, response.status_code)
        return result

    # Subclasses implement the wire translation.
    def build(self, req: ChatRequest, api_key: str):  # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, data: dict, latency_ms: float, status: int) -> ChatResult:  # pragma: no cover - abstract
        raise NotImplementedError


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class OpenAIWireExecutor(BaseExecutor):
    """OpenAI chat-completions wire format (OpenAI, Groq, Together, Fireworks, DeepSeek, Mistral, xAI)."""

    _FINISH = {"stop": "stop", "length": "length", "content_filter": "content_filter"}

    def build(self, req: ChatRequest, api_key: str):
        body: Dict[str, Any] = {"model": req.model, "messages": req.messages, "stream": False}
        is_openai = self.spec.provider == "OpenAI"
        if req.max_tokens is not None:
            # OpenAI itself: `max_tokens` is deprecated and *rejected* (400) by the reasoning
            # models (o-series, gpt-5*); `max_completion_tokens` is accepted by every current
            # chat model. The other OpenAI-wire hosts still speak `max_tokens`.
            body["max_completion_tokens" if is_openai else "max_tokens"] = req.max_tokens
        # OpenAI reasoning models only accept the default temperature/top_p (400 otherwise).
        sampling_ok = not (is_openai and req.reasoning_model)
        if req.temperature is not None and sampling_ok:
            body["temperature"] = req.temperature
        if req.top_p is not None and sampling_ok:
            body["top_p"] = req.top_p
        if req.stop:
            body["stop"] = req.stop
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        return f"{self.spec.base_url}/chat/completions", headers, body

    def parse(self, data: dict, latency_ms: float, status: int) -> ChatResult:
        choice = data["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some hosts return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        usage = data.get("usage") or {}
        return ChatResult(
            content=content or "",
            finish_reason=self._FINISH.get(choice.get("finish_reason"), "other"),
            input_tokens=_as_int(usage.get("prompt_tokens")),
            output_tokens=_as_int(usage.get("completion_tokens")),
            provider_latency_ms=latency_ms,
            http_status=status,
            raw_model=data.get("model"),
        )


class AnthropicExecutor(BaseExecutor):
    """Anthropic Messages API."""

    _STOP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}

    def __init__(self, spec: ExecutorSpec, timeout_s: float = 30.0, transport=None, default_max_tokens: int = 1024):
        super().__init__(spec, timeout_s, transport)
        self.default_max_tokens = default_max_tokens

    def build(self, req: ChatRequest, api_key: str):
        system_parts = [m["content"] for m in req.messages if m.get("role") == "system" and m.get("content")]
        messages = [
            {"role": m["role"], "content": m.get("content", "")}
            for m in req.messages if m.get("role") in ("user", "assistant")
        ]
        body: Dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens if req.max_tokens is not None else self.default_max_tokens,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop:
            body["stop_sequences"] = req.stop
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        return f"{self.spec.base_url}/v1/messages", headers, body

    def parse(self, data: dict, latency_ms: float, status: int) -> ChatResult:
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") or {}
        return ChatResult(
            content=text,
            finish_reason=self._STOP.get(data.get("stop_reason"), "other"),
            input_tokens=_as_int(usage.get("input_tokens")),
            output_tokens=_as_int(usage.get("output_tokens")),
            provider_latency_ms=latency_ms,
            http_status=status,
            raw_model=data.get("model"),
        )


class GeminiExecutor(BaseExecutor):
    """Google Gemini API (generateContent)."""

    _FINISH = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter", "RECITATION": "content_filter"}

    def build(self, req: ChatRequest, api_key: str):
        system_parts = [m["content"] for m in req.messages if m.get("role") == "system" and m.get("content")]
        contents = []
        for m in req.messages:
            if m.get("role") == "user":
                contents.append({"role": "user", "parts": [{"text": m.get("content", "")}]})
            elif m.get("role") == "assistant":
                contents.append({"role": "model", "parts": [{"text": m.get("content", "")}]})
        body: Dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        generation: Dict[str, Any] = {}
        if req.max_tokens is not None:
            generation["maxOutputTokens"] = req.max_tokens
        if req.temperature is not None:
            generation["temperature"] = req.temperature
        if req.top_p is not None:
            generation["topP"] = req.top_p
        if req.stop:
            generation["stopSequences"] = req.stop
        if generation:
            body["generationConfig"] = generation
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        return f"{self.spec.base_url}/models/{req.model}:generateContent", headers, body

    def parse(self, data: dict, latency_ms: float, status: int) -> ChatResult:
        candidates = data.get("candidates") or []
        text = ""
        finish = "other"
        if candidates:
            cand = candidates[0]
            parts = (cand.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            finish = self._FINISH.get(cand.get("finishReason"), "other")
        usage = data.get("usageMetadata") or {}
        output = _as_int(usage.get("candidatesTokenCount")) + _as_int(usage.get("thoughtsTokenCount"))
        return ChatResult(
            content=text,
            finish_reason=finish,
            input_tokens=_as_int(usage.get("promptTokenCount")),
            output_tokens=output,
            provider_latency_ms=latency_ms,
            http_status=status,
            raw_model=data.get("modelVersion"),
        )


def get_executor(
    spec: ExecutorSpec,
    timeout_s: float = 30.0,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    default_max_tokens: int = 1024,
) -> BaseExecutor:
    """Executor factory — the DI seam DispatchService(executor_factory=...) uses."""
    if spec.family == FAMILY_OPENAI_WIRE:
        return OpenAIWireExecutor(spec, timeout_s, transport)
    if spec.family == FAMILY_ANTHROPIC:
        return AnthropicExecutor(spec, timeout_s, transport, default_max_tokens=default_max_tokens)
    if spec.family == FAMILY_GEMINI:
        return GeminiExecutor(spec, timeout_s, transport)
    raise ValueError(f"unknown executor family: {spec.family}")
