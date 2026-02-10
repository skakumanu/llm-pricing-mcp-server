# LLM Pricing MCP Server

MCP server to compare LLM models using latest pricing data from various providers.

## Overview

This FastAPI-based server provides endpoints to access and compare pricing information for various Large Language Model (LLM) providers including OpenAI, Anthropic, and others.

## Features

- **Health Check Endpoint**: Simple endpoint to verify server status
- **Pricing Endpoint**: Aggregated pricing data from multiple LLM providers
- **Environment Configuration**: Secure configuration using environment variables
- **Logging**: Built-in logging for monitoring and debugging
- **API Documentation**: Auto-generated OpenAPI documentation

## Setup

### Prerequisites

- Python 3.12+
- pip

### Installation

1. Clone the repository:
```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

### Running the Server

Start the server using:

```bash
python main.py
```

Or with uvicorn directly:

```bash
uvicorn main:app --reload
```

The server will start on `http://0.0.0.0:8000` by default (configurable via .env).

## API Endpoints

### Health Check
```
GET /
```

Returns server status:
```json
{
  "status": "ok"
}
```

### Pricing Information
```
GET /pricing
```

Returns pricing data for LLM models from various providers.

**Note**: Currently returns mock data. Real pricing data implementation coming in future versions.

## API Documentation

Once the server is running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Configuration

Environment variables (defined in `.env`):

- `HOST`: Server host address (default: `0.0.0.0`)
- `PORT`: Server port (default: `8000`)
- `LOG_LEVEL`: Logging level (default: `INFO`)

## Project Structure

```
llm-pricing-mcp-server/
├── main.py              # Main application entry point
├── routers/             # API route handlers
│   ├── __init__.py
│   └── pricing.py       # Pricing endpoint implementation
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variables template
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Development

### Future Enhancements

- Integration with real-time pricing APIs from:
  - OpenAI
  - Anthropic
  - Google (Gemini)
  - Other LLM providers
- Caching mechanism for pricing data
- Rate limiting
- Authentication and API keys
- Historical pricing data
- Cost comparison tools

## License

See LICENSE file for details.
