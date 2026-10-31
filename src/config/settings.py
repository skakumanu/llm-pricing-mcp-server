"""Configuration settings for the MCP server."""
from pydantic import SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from typing import Optional
from src import __version__


# Env keys of settings that have been removed. A deployment's .env may still carry
# them, so they are dropped before validation and startup is unaffected. Any other
# unknown .env key (a typo such as MCP_APIKEY) still fails loudly under extra="forbid".
RETIRED_ENV_KEYS = frozenset({
    "anyscale_api_key",  # v1.81.2: provider retired
})


class _WithoutRetiredKeys(PydanticBaseSettingsSource):
    """Wraps the .env source and drops RETIRED_ENV_KEYS (case-insensitive)."""

    def __init__(self, settings_cls, inner: PydanticBaseSettingsSource):
        super().__init__(settings_cls)
        self._inner = inner

    def get_field_value(self, field, field_name):  # unused: __call__ is overridden
        return None, field_name, False

    def __call__(self):
        return {k: v for k, v in self._inner().items() if k.lower() not in RETIRED_ENV_KEYS}


class Settings(BaseSettings):
    """Application settings."""

    # API Keys (All Optional - System works without them)
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    mistral_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    together_api_key: Optional[str] = None
    fireworks_api_key: Optional[str] = None
    perplexity_api_key: Optional[str] = None
    ai21_api_key: Optional[str] = None
    xai_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    cerebras_api_key: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    replicate_api_key: Optional[str] = None
    salesforce_api_key: Optional[str] = None
    promptql_api_key: Optional[str] = None
    snowflake_api_key: Optional[str] = None
    oracle_api_key: Optional[str] = None
    deepinfra_api_key: Optional[str] = None
    novita_api_key: Optional[str] = None
    vercel_ai_gateway_api_key: Optional[str] = None
    nebius_api_key: Optional[str] = None

    # API Authentication
    mcp_api_key: Optional[str] = None
    mcp_api_key_header: str = "x-api-key"

    # Request limits
    max_body_bytes: int = 1_000_000
    rate_limit_per_minute: int = 60
    rate_limit_free: int = 30
    rate_limit_pro: int = 120
    rate_limit_enterprise: int = 600

    # Server Configuration
    server_host: str = "0.0.0.0"  # nosec B104
    server_port: int = 8000
    debug: bool = False

    # Application metadata
    app_name: str = "LLM Pricing MCP Server"
    app_version: str = __version__
    app_description: str = (
        "Dynamic pricing comparison server for LLM models across 17 major providers"
        " with geolocation and health checks"
    )

    # Conversation persistence (None = in-memory only; path = SQLite file)
    conversation_db_path: Optional[str] = None   # e.g. "conversations.db"

    # Webhook signing secret (None = unsigned; set to a long random string in production)
    webhook_secret: Optional[str] = None

    # v1.80.1 — env WEBHOOK_ALLOW_PRIVATE_TARGETS. LOCAL DEVELOPMENT ONLY, never set on Fly.
    # When on, webhook alerts may target http:// and private/loopback/internal hosts
    # (src/services/webhook_guard.py); a WARNING is logged at startup. No request can change it.
    webhook_allow_private_targets: bool = False

    # Stripe / Billing
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    stripe_price_id_pro: Optional[str] = None
    stripe_price_id_enterprise: Optional[str] = None
    billing_db_path: str = "billing.db"
    billing_base_url: str = "http://localhost:8000"
    # v1.84.0: a paid customer whose renewal failed keeps the stored tier for this many
    # days (billing_status = past_due); after that the *effective* tier is free until
    # Stripe reports the invoice paid. Stored tiers change only through Stripe events.
    billing_past_due_grace_days: int = 14

    # Support agent (v1.84.0). A second ReAct agent (agent/support_agent.py) that answers
    # account, billing, key and FAQ questions on the operator's LLM key. Per-caller daily
    # USD caps by tier (anonymous strictest); the authoritative ceiling is the
    # orchestrator spend ledger (workflow support_triage, global monthly cap). A cap hit
    # returns a deterministic non-LLM fallback (FAQ links + escalation), never a 5xx.
    support_enabled: bool = True
    support_daily_cap_anonymous_usd: float = 0.10
    support_daily_cap_free_usd: float = 0.50
    support_daily_cap_pro_usd: float = 3.00
    support_daily_cap_enterprise_usd: float = 10.00
    support_max_escalations_per_day: int = 3        # identified customers; anonymous callers get 1
    # Used to price a support turn only when AGENT_MODEL is not in the pricing catalogue.
    support_fallback_price_per_1m_in_usd: float = 3.0
    support_fallback_price_per_1m_out_usd: float = 15.0

    # Pricing history
    pricing_history_db_path: str = "pricing_history.db"
    pricing_snapshot_interval_hours: int = 6     # how often to snapshot live prices

    # Benchmark / quality scores
    benchmark_cache_ttl_hours: int = 24     # how long to cache HF leaderboard scores

    # Agent / RAG
    agent_llm_provider: str = "anthropic"        # "anthropic" | "openai"
    agent_model: str = "claude-sonnet-4-6"       # model name for the chosen provider
    agent_max_history_turns: int = 10            # max conversation turns to keep
    agent_max_iterations: int = 5               # max ReAct loop iterations
    rag_top_k: int = 5                          # number of chunks to retrieve
    rag_docs_path: str = "docs"                 # path to markdown docs folder
    rag_use_embeddings: bool = False            # True = use OpenAI embeddings; False = TF-IDF

    # Local currency (v1.76.0). Detection is best-effort and only ever reads the
    # request's IP transiently; the rate table is vendored (never fetched per request).
    currency_detection_enabled: bool = True     # False = never infer a country from the IP
    geo_lookup_timeout_seconds: float = 1.0     # request-path cap on the IP -> country lookup
    exchange_rates_path: Optional[str] = None   # override for data/exchange_rates.json

    # Dispatch gateway (v1.77.0). Provider keys are never read from here — callers
    # bring their own per request (X-Provider-Key-<Provider> headers) or store them
    # per org (v1.79.0, /dispatch/keys); these only shape the failover loop and the
    # receipt store.
    dispatch_enabled: bool = True                # False = POST /v1/chat/completions dispatch mode -> 503
    dispatch_default_max_tokens: int = 1024      # when the caller omits max_tokens (Anthropic requires one)
    dispatch_attempt_timeout_ms: int = 30_000    # per-attempt provider timeout
    dispatch_max_attempts: int = 3               # failover attempts (1 when failover is off or pinned)
    dispatch_receipt_retention_days: int = 90    # metadata-only receipt rows purged past this age

    # Stored per-org provider keys (v1.79.0). Server secret the customers' stored provider
    # keys are encrypted under (AES-256-GCM via HKDF): base64url of >= 32 random bytes, env
    # PROVIDER_KEY_ENCRYPTION_KEY. Unset/invalid = /dispatch/keys answers 503 and dispatch is
    # header-only. Changing it makes every stored key need re-registration. SecretStr so a
    # repr of Settings never shows it.
    provider_key_encryption_key: Optional[SecretStr] = None

    # Orchestrator core (v1.83.0). One logical orchestrator (run ids, run log, approval
    # queue, budget ledger, settings) in its own SQLite file on the volume. The intake key
    # is a SEPARATE credential for the GitHub Actions executor; it is accepted only on
    # POST /orchestrator/events, GET /orchestrator/runs/{id}/actions and
    # POST /orchestrator/runs/{id}/actions/{aid}/result, never anywhere else. SecretStr so
    # a repr of Settings never shows it. Unset = those routes take the operator key only.
    orchestrator_db_path: str = "orchestrator.db"
    orchestrator_intake_key: Optional[SecretStr] = None
    orchestrator_run_retention_days: int = 365   # run / step / event / approval / action rows
    orchestrator_io_retention_days: int = 90     # redacted I/O summaries nulled past this age

    # v1.82.1: back to "forbid" so an unknown .env key fails at startup instead of being
    # silently ignored; retired keys are filtered out first (RETIRED_ENV_KEYS). Process
    # environment variables were never affected: only names matching a field are read.
    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "extra": "forbid",
    }

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (
            init_settings,
            env_settings,
            _WithoutRetiredKeys(settings_cls, dotenv_settings),
            file_secret_settings,
        )


settings = Settings()
