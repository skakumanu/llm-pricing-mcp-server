"""
LLM Pricing MCP Server - Main Application
"""
import logging
import os
from fastapi import FastAPI
from dotenv import load_dotenv
from routers import pricing

# Load environment variables
load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="LLM Pricing MCP Server",
    description="MCP server to compare LLM models using latest pricing data",
    version="0.1.0"
)

# Include routers
app.include_router(pricing.router)


@app.get("/")
async def health_check():
    """
    Health check endpoint
    
    Returns:
        dict: Status information
    """
    logger.info("Health check endpoint called")
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
