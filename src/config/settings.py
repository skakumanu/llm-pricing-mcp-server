"""Configuration settings for the MCP server."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""
    
    # API Keys
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    
    # Server Configuration
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug: bool = False
    
    # Application metadata
    app_name: str = "LLM Pricing MCP Server"
    app_version: str = "1.1.0"
    app_description: str = "Dynamic pricing comparison server for LLM models"
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": False
    }


settings = Settings()
