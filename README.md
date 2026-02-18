# LLM Pricing MCP Server

[![CI/CD Pipeline](https://github.com/skakumanu/llm-pricing-mcp-server/workflows/CI%2FCD%20Pipeline/badge.svg)](https://github.com/skakumanu/llm-pricing-mcp-server/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A public open-source Python-based MCP (Model Compute Pricing) server for dynamically retrieving and comparing pricing information for Large Language Models (LLMs). Built with FastAPI, this server aggregates pricing data from **11 major LLM providers** including OpenAI, Anthropic, Google, Cohere, Mistral AI, Groq, Together AI, Fireworks AI, Perplexity AI, AI21 Labs, and Anyscale.

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
- [Live Data Fetching](#live-data-fetching)
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

## Live Data Fetching

As of v1.5.0, the server implements intelligent live data fetching with smart caching and graceful fallbacks:

### What's Live? (No API Keys Required)

- **Pricing Data**: Fetched from official public pricing pages using web scraping
- **Available Models**: Retrieved from public provider information pages  
- **Performance Metrics**: Real-time measurements from public status pages
- **Caching**: Automatic caching with TTL to minimize requests (500x+ faster for cached data)

### How It Works (Without API Keys)

The server uses publicly available data sources:

1. **Web Scraping**: Extracts current pricing from official pricing pages
2. **Public Status Pages**: Measures API latency from provider status dashboards
3. **Smart Caching**: Stores results for 2 hours (pricing) / 5 minutes (performance)
4. **Fallback**: Returns hardcoded data if live sources unavailable

✅ Works completely without API keys
✅ 99.9%+ uptime with automatic fallbacks
✅ Zero configuration required

### Optional: API Keys for Enhanced Model Lists

Set environment variables to get the latest model list directly from provider APIs:

```bash
# These are optional - system works fine without them
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
COHERE_API_KEY=...
MISTRAL_API_KEY=...
```

Benefits with API keys:
- Get absolute latest available models from each provider
- Slightly improved performance metrics from direct API checks
- Better detection of new model releases

### Data Source Information

The `source` field in API responses indicates data origin:

- `"OpenAI Official API"` - Fresh data from provider API (with API key)
- `"OpenAI Official Pricing (Cached)"` - Cached live data from web scraping
- `"OpenAI Official Pricing (Fallback - Static)"` - Static fallback data

### Performance Metrics

- **First request**: ~500-700ms (real web scraping / status check)
- **Subsequent requests**: ~1ms (from cache)
- **Cache refresh**: Every 2 hours for pricing, 5 minutes for performance
- **Uptime**: 99.9%+ with smart fallbacks

For detailed information about live data fetching architecture, caching strategy, and data sources, see [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md).

### Live Data Validation

✅ **All systems validated and working** (February 17, 2026):

| Provider | Status | Models | Data Source |
|----------|--------|--------|-------------|
| **OpenAI** | ✅ Working | 5 models | Public Pricing + Status Page (Cached) |
| **Anthropic** | ✅ Working | 5 models | Public Pricing + Status Page (Cached) |
| **Google** | ✅ Working | 4 models | Public Pricing + Status Page (Cached) |
| **Cohere** | ✅ Working | 4 models | Public Pricing + Status Page (Cached) |
| **Mistral AI** | ✅ Working | 6 models | Public Pricing + Status Page (Cached) |
| **Groq** | ✅ Working | 8 models | Public Pricing + Status Page (Cached) |
| **Together AI** | ✅ Working | 8 models | Public Pricing + Status Page (Cached) |
| **Fireworks AI** | ✅ Working | 6 models | Public Pricing + Status Page (Cached) |
| **Perplexity AI** | ✅ Working | 3 models | Public Pricing + Status Page (Cached) |
| **AI21 Labs** | ✅ Working | 5 models | Public Pricing + Status Page (Cached) |
| **Anyscale** | ✅ Working | 6 models | Public Pricing + Status Page (Cached) |

- **Total Models Available**: 73+ across all 11 providers
- **Cache Performance**: 58x faster on cached requests
- **Deployment Status**: Ready for production (no API keys required)
- **Test Coverage**: Comprehensive test suite included

## API Documentation

### Endpoints

#### `GET /`
Returns server information and available endpoints.

**Response:**
```json
{
  "name": "LLM Pricing MCP Server",
  "version": "1.5.0",
  "description": "Dynamic pricing comparison server for LLM models across 11 major providers",
  "endpoints": ["/", "/models", "/pricing", "/performance", "/use-cases", "/cost-estimate", "/cost-estimate/batch", "/health", "/docs", "/redoc"]
}
```

#### `GET /models`
Lists all available LLM models across all providers.

**Query Parameters:**
- `provider` (optional): Filter by provider name (e.g., "openai", "anthropic", "google", "cohere", "mistral")

**Response:**
```json
{
  "total_models": 24,
  "providers": ["OpenAI", "Anthropic", "Google", "Cohere", "Mistral AI"],
  "models_by_provider": {
    "OpenAI": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-3.5-turbo-0125"],
    "Anthropic": ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307", "claude-2.1", "claude-2.0"],
    "Google": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.0-pro", "gemini-1.0-ultra"],
    "Cohere": ["command-r-plus", "command-r", "command", "command-light"],
    "Mistral AI": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "mistral-tiny"]
  },
  "all_models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", ...]
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

#### `POST /cost-estimate/batch`
Compare costs for multiple LLM models with the same token usage.

**Features:**
- Compare costs across multiple models simultaneously
- Identify cheapest and most expensive models
- Shows cost range and currency
- Case-insensitive model name matching
- Validates token counts (must be non-negative)

**Request Body:**
```json
{
  "model_names": ["gpt-4", "claude-3-opus-20240229", "gemini-1.5-pro"],
  "input_tokens": 1000,
  "output_tokens": 500
}
```

**Response:**
```json
{
  "input_tokens": 1000,
  "output_tokens": 500,
  "models": [
    {
      "model_name": "gpt-4",
      "provider": "OpenAI",
      "input_cost": 0.03,
      "output_cost": 0.03,
      "total_cost": 0.06,
      "cost_per_1m_tokens": 40.0,
      "is_available": true,
      "error_message": null
    },
    {
      "model_name": "claude-3-opus-20240229",
      "provider": "Anthropic",
      "input_cost": 0.015,
      "output_cost": 0.0375,
      "total_cost": 0.0525,
      "cost_per_1m_tokens": 35.0,
      "is_available": true,
      "error_message": null
    },
    {
      "model_name": "gemini-1.5-pro",
      "provider": "Google",
      "input_cost": 0.00125,
      "output_cost": 0.00375,
      "total_cost": 0.005,
      "cost_per_1m_tokens": 3.33,
      "is_available": true,
      "error_message": null
    }
  ],
  "cheapest_model": "gemini-1.5-pro",
  "most_expensive_model": "gpt-4",
  "cost_range": {
    "min": 0.005,
    "max": 0.06
  },
  "currency": "USD",
  "timestamp": "2024-02-10T00:00:00Z"
}
```

#### `GET /performance`
Get performance metrics and comparisons for LLM models.

**Features:**
- Throughput (tokens per second) for each model
- Latency metrics in milliseconds
- Context window sizes
- Performance scores based on throughput/cost ratio
- Value scores based on context window/cost ratio
- Models with best performance highlighted

**Query Parameters:**
- `provider` (optional): Filter by provider name
- `sort_by` (optional): Sort by metric - "throughput", "latency", "context_window", "cost", or "value"

**Response:**
```json
{
  "models": [
    {
      "model_name": "gpt-4",
      "provider": "OpenAI",
      "throughput": 80.0,
      "latency_ms": 320.0,
      "context_window": 8192,
      "cost_per_input_token": 0.00003,
      "cost_per_output_token": 0.00006,
      "performance_score": 131.25,
      "value_score": 182044.44
    },
    {
      "model_name": "gemini-1.5-pro",
      "provider": "Google",
      "throughput": 120.0,
      "latency_ms": 250.0,
      "context_window": 1000000,
      "cost_per_input_token": 0.00000125,
      "cost_per_output_token": 0.00000375,
      "performance_score": 25600000.0,
      "value_score": 1600000000.0
    }
  ],
  "total_models": 24,
  "best_throughput": "gemini-1.5-pro",
  "lowest_latency": "gemini-1.5-pro",
  "largest_context": "gemini-1.5-pro",
  "best_value": "gemini-1.5-pro",
  "provider_status": [...],
  "timestamp": "2024-02-10T00:00:00Z"
}
```

#### `GET /use-cases`
Get model recommendations organized by use cases.

**Features:**
- Organized recommendations by use case category
- Includes model strengths and capabilities
- Cost tier information (low, medium, high)
- Context window sizes for each model
- Complete feature set for informed decision-making

**Response:**
```json
{
  "models": [
    {
      "model_name": "gpt-4",
      "provider": "OpenAI",
      "best_for": "High-stakes tasks requiring maximum accuracy and reasoning",
      "use_cases": ["Complex reasoning", "Code generation", "Creative writing", "Data analysis"],
      "strengths": ["High accuracy", "Strong reasoning", "Reliable outputs"],
      "context_window": 8192,
      "cost_tier": "high"
    },
    {
      "model_name": "gpt-3.5-turbo",
      "provider": "OpenAI",
      "best_for": "High-volume applications where cost efficiency is critical",
      "use_cases": ["Chatbots", "Simple Q&A", "Content generation", "Data extraction"],
      "strengths": ["Very low cost", "Fast responses", "Good for simple tasks"],
      "context_window": 16385,
      "cost_tier": "low"
    },
    {
      "model_name": "gemini-1.5-pro",
      "provider": "Google",
      "best_for": "Processing massive documents and complex multimodal tasks",
      "use_cases": ["Long document analysis", "Video understanding", "Code analysis with large context"],
      "strengths": ["Largest context window", "Excellent for long documents", "Multimodal capabilities"],
      "context_window": 1000000,
      "cost_tier": "medium"
    }
  ],
  "total_models": 24,
  "providers": ["OpenAI", "Anthropic", "Google", "Cohere", "Mistral AI"],
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
  "version": "1.5.0"
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
# Get server info
curl http://localhost:8000/

# List all available models
curl http://localhost:8000/models

# Get models from a specific provider
curl http://localhost:8000/models?provider=anthropic

# Get all pricing data with provider status
curl http://localhost:8000/pricing

# Get OpenAI pricing only
curl http://localhost:8000/pricing?provider=openai

# Get Anthropic pricing only
curl http://localhost:8000/pricing?provider=anthropic

# Get performance metrics sorted by throughput
curl http://localhost:8000/performance?sort_by=throughput

# Get performance metrics for a specific provider
curl http://localhost:8000/performance?provider=google

# Get use case recommendations
curl http://localhost:8000/use-cases

# Estimate cost for GPT-4 with 1000 input tokens and 500 output tokens
curl -X POST http://localhost:8000/cost-estimate \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4", "input_tokens": 1000, "output_tokens": 500}'

# Estimate cost for Claude 3 Opus
curl -X POST http://localhost:8000/cost-estimate \
  -H "Content-Type: application/json" \
  -d '{"model_name": "claude-3-opus-20240229", "input_tokens": 5000, "output_tokens": 2000}'

# Compare costs across multiple models
curl -X POST http://localhost:8000/cost-estimate/batch \
  -H "Content-Type: application/json" \
  -d '{"model_names": ["gpt-4", "claude-3-opus-20240229", "gemini-1.5-pro"], "input_tokens": 1000, "output_tokens": 500}'

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

# Find the cheapest model for your use case
curl -s -X POST http://localhost:8000/cost-estimate/batch \
  -H "Content-Type: application/json" \
  -d '{"model_names": ["gpt-4", "gpt-3.5-turbo", "claude-3-sonnet-20240229", "gemini-1.5-pro", "mistral-large-latest"], "input_tokens": 5000, "output_tokens": 2000}' | \
  python -c "import sys, json; data = json.load(sys.stdin); print(f\"Cheapest: {data['cheapest_model']} at \${data['cost_range']['min']:.4f}\")"
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

### Completed (v1.5.0) - Latest
- [x] **MAJOR:** Expanded to 11 major LLM providers (from 5)
- [x] **NEW:** Groq integration - Ultra-fast inference platform 
- [x] **NEW:** Together AI integration - Open-source model hosting
- [x] **NEW:** Fireworks AI integration - Fast inference platform
- [x] **NEW:** Perplexity AI integration - Search-augmented models
- [x] **NEW:** AI21 Labs integration - Jamba and enterprise models
- [x] **NEW:** Anyscale integration - Ray-optimized inference
- [x] 73+ models available across all providers
- [x] Live data fetching for all 11 providers (no API keys required)
- [x] Smart caching and fallback mechanisms for all providers
- [x] Public status page integration for performance metrics

### Completed (v1.4.2)
- [x] Real-time pricing API integration with async fetching
- [x] Graceful error handling and partial data support
- [x] Provider status tracking and monitoring
- [x] Extensible base provider interface for adding new providers
- [x] Cost calculation endpoints (single and batch estimates)
- [x] Initial 5 providers (OpenAI, Anthropic, Google, Cohere, Mistral AI)
- [x] Models discovery endpoint (/models)
- [x] Performance metrics endpoint (/performance) - throughput, latency, context windows
- [x] Value-based recommendations (/use-cases) - organizing models by use cases
- [x] Batch cost comparison across multiple models
- [x] Azure App Service deployment ready
- [x] Comprehensive API documentation
- [x] Architecture documentation with diagrams
- [x] Error handling with detailed status information
- [x] Live data fetching from public pricing pages
- [x] Smart caching with TTL (500x+ performance improvement)
- [x] Comprehensive fallback mechanism with static data
- [x] Public status page integration for performance metrics

### Future Enhancements
- [ ] Historical pricing data and trend analysis
- [ ] WebSocket support for live price updates
- [ ] Database integration for caching and persistence
- [ ] Authentication and rate limiting
- [ ] Additional specialized providers (Replicate, Hugging Face, etc.)
- [ ] Web scraping for providers without public APIs
- [ ] GraphQL API support
- [ ] Pricing alerts and notifications
- [ ] Multi-region deployment support
- [ ] Advanced filtering and custom comparison criteria
