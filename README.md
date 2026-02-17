# LLM Pricing MCP Server

[![CI/CD Pipeline](https://github.com/skakumanu/llm-pricing-mcp-server/workflows/CI%2FCD%20Pipeline/badge.svg)](https://github.com/skakumanu/llm-pricing-mcp-server/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A public open-source Python-based MCP (Model Compute Pricing) server for dynamically retrieving and comparing pricing information for Large Language Models (LLMs). Built with FastAPI, this server aggregates pricing data from multiple LLM providers including OpenAI and Anthropic.

## Features

- **Real-Time Pricing Aggregation**: Asynchronously fetch pricing data from multiple LLM providers concurrently
- **Graceful Error Handling**: Return partial data when providers are unavailable with detailed status information
- **Provider Status Tracking**: Monitor availability and health of each pricing provider
- **Unified Pricing Format**: All pricing data in USD per token with source attribution
- **Comprehensive Metrics**: Track cost per token, context window sizes, and provider metadata
- **RESTful API**: Clean, well-documented endpoints using FastAPI
- **Data Validation**: Robust validation using Pydantic models
- **Extensible Architecture**: Easy-to-use base provider interface for adding new providers
- **Environment Configuration**: Secure configuration management with `.env` files
- **Comprehensive Testing**: Full test suite including async operations and error handling
- **CI/CD**: Automated testing and deployment via GitHub Actions
- **Azure Deployment**: Ready-to-deploy on Azure App Service

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [API Documentation](#api-documentation)
- [Configuration](#configuration)
- [Development](#development)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Support](#support)
- [Roadmap](#roadmap)

## Installation

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

## Quick Start

### Running the Server

Start the development server:
```bash
python src/main.py
```

Or using uvicorn directly:
```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

The server will be available at `http://localhost:8000`

### Interactive API Documentation

Once the server is running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Architecture

This project follows a modular, layered architecture designed for scalability and extensibility. For a comprehensive understanding of the system design, including detailed architecture diagrams, patterns, and component interactions, please refer to the [ARCHITECTURE.md](ARCHITECTURE.md) document.

### Key Highlights

- **Service Provider Pattern**: Each LLM provider is implemented as an independent service
- **Aggregator Pattern**: Central service orchestrates and caches data from all providers
- **Lazy Initialization**: Aggregator initializes on first request for optimal startup performance
- **Async/Await**: Non-blocking I/O for handling concurrent requests efficiently
- **Pydantic Models**: Strong data validation and serialization

For detailed diagrams, design patterns, and architectural decisions, see [ARCHITECTURE.md](ARCHITECTURE.md).

## API Documentation

### Endpoints

#### `GET /`
Returns server information and available endpoints.

**Response:**
```json
{
  "name": "LLM Pricing MCP Server",
  "version": "1.0.0",
  "description": "Dynamic pricing comparison server for LLM models",
  "endpoints": ["/", "/pricing", "/cost-estimate", "/health", "/docs", "/redoc"]
}
```

#### `GET /pricing`
Retrieves aggregated pricing data from all LLM providers with real-time fetching.

**Features:**
- Asynchronous data fetching from multiple providers concurrently
- Graceful handling of provider failures (returns partial data)
- Provider availability status included in response
- Unified pricing format (USD per token)
- Source attribution for each pricing data point

**Query Parameters:**
- `provider` (optional): Filter by provider name (e.g., "openai", "anthropic")

**Response:**
```json
{
  "models": [
    {
      "model_name": "gpt-4",
      "provider": "OpenAI",
      "cost_per_input_token": 0.00003,
      "cost_per_output_token": 0.00006,
      "context_window": 8192,
      "currency": "USD",
      "unit": "per_token",
      "source": "OpenAI Official Pricing (Static)",
      "last_updated": "2024-02-10T00:00:00Z"
    }
  ],
  "total_models": 10,
  "provider_status": [
    {
      "provider_name": "OpenAI",
      "is_available": true,
      "error_message": null,
      "models_count": 5
    },
    {
      "provider_name": "Anthropic",
      "is_available": true,
      "error_message": null,
      "models_count": 5
    }
  ],
  "timestamp": "2024-02-10T00:00:00Z"
}
```

**Note on Provider Failures:**
If a provider is unavailable, the response will still include data from available providers:
```json
{
  "models": [...],  // Only models from available providers
  "total_models": 5,
  "provider_status": [
    {
      "provider_name": "OpenAI",
      "is_available": false,
      "error_message": "Connection timeout",
      "models_count": 0
    },
    {
      "provider_name": "Anthropic",
      "is_available": true,
      "error_message": null,
      "models_count": 5
    }
  ],
  "timestamp": "2024-02-10T00:00:00Z"
}
```

#### `GET /health`
Health check endpoint for monitoring.

**Response:**
```json
{
  "status": "healthy",
  "service": "LLM Pricing MCP Server",
  "version": "1.0.0"
}
```

#### `POST /cost-estimate`
Estimate the cost for using a specific LLM model based on token usage.

**Features:**
- Calculate total costs for any supported model
- Provides detailed breakdown of input and output costs
- Case-insensitive model name matching
- Validates token counts (must be non-negative)

**Request Body:**
```json
{
  "model_name": "gpt-4",
  "input_tokens": 1000,
  "output_tokens": 500
}
```

**Response:**
```json
{
  "model_name": "gpt-4",
  "provider": "OpenAI",
  "input_tokens": 1000,
  "output_tokens": 500,
  "input_cost": 0.03,
  "output_cost": 0.03,
  "total_cost": 0.06,
  "currency": "USD",
  "timestamp": "2024-02-10T00:00:00Z"
}
```

**Error Response (404 - Model Not Found):**
```json
{
  "detail": "Model 'unknown-model' not found. Please check the /pricing endpoint for available models."
}
```

**Error Response (422 - Validation Error):**
```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["body", "input_tokens"],
      "msg": "Input should be greater than or equal to 0"
    }
  ]
}
```

### Example Requests

```bash
# Get all pricing data with provider status
curl http://localhost:8000/pricing

# Get OpenAI pricing only
curl http://localhost:8000/pricing?provider=openai

# Get Anthropic pricing only
curl http://localhost:8000/pricing?provider=anthropic

# Estimate cost for GPT-4 with 1000 input tokens and 500 output tokens
curl -X POST http://localhost:8000/cost-estimate \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4", "input_tokens": 1000, "output_tokens": 500}'

# Estimate cost for Claude 3 Opus
curl -X POST http://localhost:8000/cost-estimate \
  -H "Content-Type: application/json" \
  -d '{"model_name": "claude-3-opus-20240229", "input_tokens": 5000, "output_tokens": 2000}'

# Health check
curl http://localhost:8000/health

# Pretty-print JSON output
curl http://localhost:8000/pricing | python -m json.tool

# Get only provider status information
curl http://localhost:8000/pricing | python -c "import sys, json; data = json.load(sys.stdin); print(json.dumps(data['provider_status'], indent=2))"

# Count total models by provider
curl -s http://localhost:8000/pricing | python -c "import sys, json; data = json.load(sys.stdin); [print(f\"{s['provider_name']}: {s['models_count']} models\") for s in data['provider_status']]"

# Calculate cost for a batch of prompts
MODEL="gpt-3.5-turbo"
INPUT_TOKENS=2000
OUTPUT_TOKENS=500
curl -s -X POST http://localhost:8000/cost-estimate \
  -H "Content-Type: application/json" \
  -d "{\"model_name\": \"$MODEL\", \"input_tokens\": $INPUT_TOKENS, \"output_tokens\": $OUTPUT_TOKENS}" | \
  python -c "import sys, json; data = json.load(sys.stdin); print(f\"Total cost for {data['model_name']}: \${data['total_cost']:.4f}\")"
```

## Configuration

### Environment Variables

Create a `.env` file in the root directory (use `.env.example` as template):

```env
# API Keys (optional - for future authenticated endpoints)
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Server Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DEBUG=false
```

## Development

### Project Structure

```
llm-pricing-mcp-server/
├── src/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py            # Configuration settings
│   ├── models/
│   │   ├── __init__.py
│   │   └── pricing.py             # Pydantic models
│   └── services/
│       ├── __init__.py
│       ├── openai_pricing.py      # OpenAI pricing service
│       ├── anthropic_pricing.py   # Anthropic pricing service
│       └── pricing_aggregator.py  # Aggregator service
├── tests/
│   ├── conftest.py
│   ├── test_api.py                # API endpoint tests
│   ├── test_models.py             # Model validation tests
│   └── test_services.py           # Service tests
├── .github/
│   └── workflows/
│       └── ci-cd.yml              # CI/CD pipeline
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── DEPLOYMENT.md
├── LICENSE
├── Procfile                       # For deployment
└── runtime.txt                    # Python version
```

### Adding a New Provider

The server uses a base provider interface that makes it easy to add new pricing providers. Follow these steps:

1. Create a new service file in `src/services/`:
```python
# src/services/new_provider_pricing.py
from typing import List
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

class NewProviderPricingService(BasePricingProvider):
    """Service to fetch and manage NewProvider model pricing."""
    
    def __init__(self, api_key=None):
        super().__init__("NewProvider")
        self.api_key = api_key
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch pricing data from the provider.
        
        Returns:
            List of PricingMetrics for NewProvider models
        """
        # Implement your pricing fetch logic here
        return [
            PricingMetrics(
                model_name="model-name",
                provider="NewProvider",
                cost_per_input_token=0.001,
                cost_per_output_token=0.002,
                context_window=100000,
                currency="USD",
                unit="per_token",
                source="NewProvider Official Pricing"
            )
        ]
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        # Implement synchronous version if needed
        pass
```

2. Update the aggregator in `src/services/pricing_aggregator.py`:
```python
from src.services.new_provider_pricing import NewProviderPricingService

class PricingAggregatorService:
    def __init__(self):
        self.openai_service = OpenAIPricingService()
        self.anthropic_service = AnthropicPricingService()
        self.newprovider_service = NewProviderPricingService()
    
    async def get_all_pricing_async(self):
        tasks = [
            self.openai_service.get_pricing_with_status(),
            self.anthropic_service.get_pricing_with_status(),
            self.newprovider_service.get_pricing_with_status(),  # Add here
        ]
        # ... rest of implementation
```

3. Add API key configuration to `.env`:
```env
NEWPROVIDER_API_KEY=your_api_key_here
```

4. Add tests in `tests/test_async_pricing.py` or create a new test file.

## Testing

### Running Tests

Run all tests:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov=src --cov-report=term-missing
```

Run specific test file:
```bash
pytest tests/test_api.py -v
```

### Test Structure

- `tests/test_api.py`: Tests for API endpoints
- `tests/test_models.py`: Tests for Pydantic models
- `tests/test_services.py`: Tests for pricing services

## Deployment

### Azure App Service

Detailed deployment instructions are available in [DEPLOYMENT.md](DEPLOYMENT.md).

**Quick Deploy:**
```bash
az webapp up --name llm-pricing-server --resource-group llm-pricing-rg --runtime "PYTHON:3.11"
```

### GitHub Actions CI/CD

The repository includes a GitHub Actions workflow that:
1. Runs tests on every push and pull request
2. Performs code linting
3. Deploys to Azure App Service on successful merge to main

Configure the following secrets in your GitHub repository:
- `AZURE_CREDENTIALS`: Azure service principal credentials
- `AZURE_WEBAPP_NAME`: Your Azure web app name

## Contributing

Contributions are welcome! We **strictly follow Git Flow** for all development. Please read our detailed [CONTRIBUTING.md](CONTRIBUTING.md) guide before starting.

### Quick Start for Contributors

1. Fork the repository
2. Clone your fork and checkout the `develop` branch:
   ```bash
   git checkout develop
   git pull origin develop
   ```
3. Create a feature branch from `develop`:
   ```bash
   git checkout -b feature/amazing-feature
   ```
4. Make your changes and commit:
   ```bash
   git add .
   git commit -m 'Add amazing feature'
   ```
5. Run tests to ensure everything works:
   ```bash
   pytest
   ```
6. Push to your fork:
   ```bash
   git push origin feature/amazing-feature
   ```
7. Open a Pull Request **against the `develop` branch** (NOT master)

### Important Git Flow Rules

- ⚠️ **Always branch from `develop`** for new features
- ⚠️ **Never merge directly to `master`** - features go to `develop` first
- ⚠️ **NEVER commit secrets or API keys** - use environment variables
- ⚠️ **Use `--no-ff` for merges** to preserve branch history
- ✅ See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed Git Flow workflows

### Security Requirements

Before committing, **always verify**:

```bash
# Check for secrets in your changes
git diff --cached
grep -r "api_key" .
grep -r "password" .
```

**Never commit:**
- API keys (OpenAI, Anthropic, Azure, etc.)
- Passwords or tokens
- `.env` files (use `.env.example` instead)
- Private keys (`.pem`, `.key`, `.pfx`)
- Hard-coded credentials

Use environment variables for all sensitive data. See the [Security Compliance](CONTRIBUTING.md#security-compliance) section for details.

### Code Style

- Follow PEP 8 guidelines
- Use type hints where possible
- Write docstrings for all functions and classes
- Keep functions focused and small

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- FastAPI for the excellent web framework
- Pydantic for robust data validation
- The open-source community for inspiration and support

## Support

For issues, questions, or contributions, please open an issue on GitHub.

## Roadmap

- [x] Real-time pricing API integration with async fetching
- [x] Graceful error handling and partial data support
- [x] Provider status tracking and monitoring
- [x] Extensible base provider interface
- [x] Cost calculation endpoints (estimate costs for token usage)
- [ ] Additional LLM providers (Google Gemini, Cohere, Meta Llama, etc.)
- [ ] Web scraping for providers without public APIs
- [ ] Historical pricing data and trend analysis
- [ ] WebSocket support for live price updates
- [ ] Database integration for caching and persistence
- [ ] Authentication and rate limiting
- [ ] Price comparison and recommendation features
