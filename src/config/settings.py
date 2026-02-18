"""Configuration settings for the MCP server."""
from pydantic_settings import BaseSettings
from typing import Optional


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
    
    # Server Configuration
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug: bool = False
    
    # Application metadata
    app_name: str = "LLM Pricing MCP Server"
    app_version: str = "1.5.0"
    app_description: str = "Dynamic pricing comparison server for LLM models across 12 major providers with geolocation and health checks"
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": False
    }


settings = Settings()
