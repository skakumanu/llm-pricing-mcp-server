"""Request models for the dispatch gateway (v1.77.0).

The dispatch policy rides inside the existing OpenAI-compatible request body as a
`dispatch` object (OpenAI SDK: ``extra_body={"dispatch": {...}}``). Everything here
is optional; an empty policy means "balanced objective, failover on".

Provider keys are deliberately NOT modelled here: they travel in
``X-Provider-Key-<Provider>`` request headers and are read straight into the
request-scoped key resolver (see dispatch_executors.HeaderKeyResolver), so a
validation error can never echo one back and no pydantic dump can contain one.
"""
from typing import Annotated, Any, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

DispatchObjective = Literal["balanced", "cheapest", "best_quality", "lowest_latency", "pinned"]

# Allow/deny entries are persisted verbatim in receipt rows (policy_json), so they are bounded:
# a catalogue model name or 'provider:<name>' is well under 200 chars and no policy needs 100+ of them.
ListEntry = Annotated[str, Field(max_length=200)]


class FailoverPolicy(BaseModel):
    """Reliability knobs for the dispatch loop."""

    enabled: bool = Field(True, description="Re-dispatch to the next-best eligible candidate on a retryable failure")
    max_attempts: Optional[int] = Field(
        None, ge=1, le=10, description="Upper bound on provider attempts (default: DISPATCH_MAX_ATTEMPTS, 3)"
    )
    timeout_ms: Optional[int] = Field(
        None, ge=100, le=600_000,
        description="Per-attempt provider timeout in ms (default: DISPATCH_ATTEMPT_TIMEOUT_MS, 30000)",
    )


class ShadowPolicy(BaseModel):
    """Optionally run a second model concurrently and report both in the receipt.

    Exactly one of `model` / `objective` must be given. Only the primary's completion
    is ever returned; the shadow's text is discarded.
    """

    model: Optional[str] = Field(None, max_length=200, description="Catalogue model name to shadow with")
    objective: Optional[DispatchObjective] = Field(
        None, description="Pick the shadow as the top candidate under this objective (excluding the primary)"
    )

    @model_validator(mode="after")
    def _exactly_one(self):
        if bool(self.model) == bool(self.objective):
            raise ValueError("shadow requires exactly one of 'model' or 'objective'")
        if self.objective == "pinned":
            raise ValueError("shadow.objective cannot be 'pinned'; name the model in shadow.model instead")
        return self


class DispatchPolicy(BaseModel):
    """Opt-in dispatch policy carried as `dispatch` on POST /v1/chat/completions."""

    objective: DispatchObjective = Field("balanced", description="Objective preset used to order eligible candidates")

    # Hard constraints — the router's vocabulary, applied as hard filters.
    min_quality_score: Optional[float] = Field(
        None, ge=0, le=100, description="Quality floor (hard guarantee, incl. failover)"
    )
    max_cost_per_1m_tokens: Optional[float] = Field(None, gt=0, description="Blended $/1M ceiling")
    min_context_window: Optional[int] = Field(None, gt=0, description="Minimum context window in tokens")
    task_type: Optional[str] = Field(
        None, max_length=100, description="Task type (inferred from the messages when omitted)"
    )
    preferred_provider: Optional[str] = Field(
        None, max_length=100, description="Provider that gets the router's preference boost"
    )
    exclude_reasoning_models: bool = Field(False, description="Drop reasoning/thinking models")

    # Allow / deny lists: model names or 'provider:<name>' entries, case-insensitive. Bounded because
    # they are persisted per receipt row.
    allow: Optional[List[ListEntry]] = Field(
        None, max_length=100, description="Only these models/providers may be dispatched"
    )
    deny: Optional[List[ListEntry]] = Field(
        None, max_length=100, description="Never dispatch these models/providers"
    )

    max_request_cost_usd: Optional[float] = Field(
        None, gt=0,
        description="Max estimated USD for this request (prompt tokens x input price + max_tokens x output price); "
                    "with a shadow the cap applies to primary + shadow combined",
    )

    failover: FailoverPolicy = Field(default_factory=FailoverPolicy)
    pinned_model: Optional[str] = Field(
        None, max_length=200, description="Required when objective is 'pinned'; exactly one model"
    )
    shadow: Optional[ShadowPolicy] = Field(None, description="Optional shadow A/B execution")

    request_id: Optional[str] = Field(None, max_length=200, description="Idempotency key for the spend record")
    session_id: Optional[str] = Field(None, max_length=200, description="Session tag for the usage ledger")

    # Locale: explicit statements only (no IP detection on this endpoint).
    currency: Optional[str] = Field(None, description="Present receipt money figures in this currency too")
    country: Optional[str] = Field(None, description="ISO-3166 alpha-2 country whose currency to present")

    @model_validator(mode="after")
    def _pinned_consistency(self):
        if self.objective == "pinned" and not self.pinned_model:
            raise ValueError("objective 'pinned' requires pinned_model")
        if self.pinned_model and self.objective != "pinned":
            raise ValueError("pinned_model requires objective 'pinned'")
        if self.shadow is not None and self.shadow.model and self.pinned_model:
            if self.shadow.model.strip().lower() == self.pinned_model.strip().lower():
                raise ValueError("shadow.model must differ from pinned_model")
        return self


class ChatCompletionsRequest(BaseModel):
    """OpenAI-compatible chat completions body (POST /v1/chat/completions), shared since v1.80.0.

    src/main.py's ``_ProxyRequest`` subclasses it unchanged (keeping the OpenAPI component
    name), and the MCP ``dispatch`` tool validates its HTTP-equivalent body through this same
    class, so every limit and every DispatchPolicy/FailoverPolicy/ShadowPolicy validator is
    identical across both transports.
    """

    model: str
    messages: List[dict]
    max_tokens: Optional[int] = None
    # v1.77.0 — standard OpenAI fields. In dispatch mode: temperature/top_p/stop are forwarded;
    # tools/functions/tool_choice/n>1/response_format are rejected up front (FR16,
    # dispatch_entry.unsupported_dispatch_feature); `user` (OpenAI's end-user abuse-tracking id)
    # is accepted and deliberately not forwarded to the provider. Plain mode ignores every one.
    # v1.78.0 — `stream: true` in dispatch mode streams the completion as OpenAI chunk SSE;
    # `stream_options` (OpenAI's include_usage switch) is accepted and has no effect — the
    # trailing chunk always carries usage. Plain mode still ignores both.
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[Any] = None
    n: Optional[int] = None
    user: Optional[str] = None
    stream: Optional[bool] = None
    stream_options: Optional[Any] = None
    tools: Optional[Any] = None
    functions: Optional[Any] = None
    tool_choice: Optional[Any] = None
    response_format: Optional[Any] = None
    # The opt-in: present -> dispatch mode (real provider call); absent -> today's proxy.
    dispatch: Optional[DispatchPolicy] = None
