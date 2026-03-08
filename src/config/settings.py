"""Configuration settings for the MCP server."""
from pydantic_settings import BaseSettings
from typing import Optional
from src import __version__


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
    anyscale_api_key: Optional[str] = None

    # API Authentication
    mcp_api_key: Optional[str] = None
    mcp_api_key_header: str = "x-api-key"

    # Request limits
    max_body_bytes: int = 1_000_000
    rate_limit_per_minute: int = 60

    # Server Configuration
    server_host: str = "0.0.0.0"  # nosec B104
    server_port: int = 8000
    debug: bool = False

    # Application metadata
    app_name: str = "LLM Pricing MCP Server"
    app_version: str = __version__
    app_description: str = (
        "Dynamic pricing comparison server for LLM models across 12 major providers"
        " with geolocation and health checks"
    )

    # Conversation persistence (None = in-memory only; path = SQLite file)
    conversation_db_path: Optional[str] = None   # e.g. "conversations.db"

    # Agent / RAG
    agent_llm_provider: str = "anthropic"        # "anthropic" | "openai"
    agent_model: str = "claude-sonnet-4-6"       # model name for the chosen provider
    agent_max_history_turns: int = 10            # max conversation turns to keep
    agent_max_iterations: int = 5               # max ReAct loop iterations
    rag_top_k: int = 5                          # number of chunks to retrieve
    rag_docs_path: str = "docs"                 # path to markdown docs folder
    rag_use_embeddings: bool = False            # True = use OpenAI embeddings; False = TF-IDF

    model_config = {
        "env_file": ".env",
        "case_sensitive": False
    }


settings = Settings()
