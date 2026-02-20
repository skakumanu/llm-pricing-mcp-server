# AGNTCY.org Architecture Principles

This document outlines the implementation of the AGNTCY.org architecture principles, including core components, layered architecture, and adherence to design principles.

## System Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Application Layer                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Endpoints: /health, /models, /pricing, /performance,            │  │
│  │             /use-cases, /cost-estimate, /cost-estimate/batch    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    Pricing Aggregator Service Layer                     │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  PricingAggregatorService: Orchestrates data from providers      │  │
│  │  - Fetches data from all providers concurrently                  │  │
│  │  - Merges and caches pricing data                                │  │
│  │  - Lazy initialization on first request                          │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
    ┌─────────────────────┐ ┌────────────────────┐ ┌──────────────────┐
    │ Base Provider       │ │ Pricing Services   │ │ Config Layer     │
    │ Interface           │ │                    │ │                  │
    │ (Abstract Base)     │ │ • OpenAI           │ │ • Settings       │
    │                     │ │ • Anthropic        │ │ • API Keys       │
    │ Methods:            │ │ • Google           │ │ • Environment    │
    │ • fetch_pricing()   │ │ • Cohere           │ │   Variables      │
    │ • get_models()      │ │ • Mistral AI       │ │                  │
    │ • verify_api_key()  │ │                    │ │                  │
    └─────────────────────┘ └────────────────────┘ └──────────────────┘
                └──────────────────────┬──────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────┐
                    │  Data Models Layer (Pydantic)    │
                    │ ┌──────────────────────────────┐ │
                    │ │ • PricingMetrics             │ │
                    │ │ • PerformanceMetrics         │ │
                    │ │ • PerformanceResponse        │ │
                    │ │ • UseCaseResponse            │ │
                    │ │ • CostEstimateResponse       │ │
                    │ │ • ProviderStatusInfo         │ │
                    │ └──────────────────────────────┘ │
                    └──────────────────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────┐
                    │ External APIs (Data Sources)     │
                    │ • OpenAI API                     │
                    │ • Anthropic API                  │
                    │ • Google AI API                  │
                    │ • Cohere API                     │
                    │ • Mistral API                    │
                    └──────────────────────────────────┘
```

## Core Components

- **Presentation Layer**: This layer handles user interactions and presents data to the user. It includes web interfaces and mobile applications to allow users to interact with the system effectively.

- **Business Logic Layer**: Here lies the core functionality of the application. This layer contains the business rules and logic that govern data processing and workflows.

- **Data Access Layer**: This layer is responsible for database interactions, managing data storage, retrieval, and updates, ensuring data integrity and security.

## Layered Architecture

The AGNTCY.org architecture follows a layered approach to separate concerns. Each layer has distinct responsibilities, promoting separation of concerns and facilitating easier maintenance and scalability:

1. **User Interface Layer**: Directly interacts with users. Responsible for user experience and efficiently presenting information.
2. **Service Layer**: Orchestrates operations and acts as a bridge between the UI and business logic. It ensures that business rules are followed.
3. **Domain Layer**: Contains the business logic, encapsulating data and behavior within models that represent the core entities.
4. **Infrastructure Layer**: Manages persistent data storage and external service communications. This layer is crucial for resistance to changes in technology.

## Adherence to Design Principles

- **Single Responsibility Principle**: Each component or service should have one reason to change, making the architecture easier to manage and evolve.
- **Open/Closed Principle**: Components should be open for extension but closed for modification, minimizing the risk of impacting existing functionality when future requirements arise.
- **Liskov Substitution Principle**: Subtypes must be substitutable for their base types, ensuring that a class derived from a base class can stand in for it without altering the desirable properties.
- **Interface Segregation Principle**: Clients should not be forced to depend on interfaces they do not use, encouraging the creation of smaller, specifically-focused interfaces.
- **Dependency Inversion Principle**: High-level modules should not depend on low-level modules but rather on abstractions, promoting flexibility and resilience to change.

## LLM Pricing MCP Server Architecture

The LLM Pricing MCP Server is designed as a modular, scalable system that aggregates and serves LLM pricing data from multiple providers.

### Key Architectural Patterns

1. **Service Provider Pattern**
   - Each LLM provider (OpenAI, Anthropic, Google, Cohere, Mistral) is implemented as an independent service
   - All providers inherit from `BasePricingProvider` abstract class
   - Enables easy addition of new providers without modifying existing code

2. **Aggregator Pattern**
   - `PricingAggregatorService` orchestrates and caches data from all providers
   - Fetches pricing data concurrently from multiple providers using async/await
   - Provides unified interface for all endpoints to access provider data

3. **Lazy Initialization**
   - Pricing aggregator is initialized on first request, not at startup
   - Reduces initial startup time and resource usage
   - Thread-safe lazy loading with async locks

4. **Dependency Inversion**
   - FastAPI endpoints depend on abstract `BasePricingProvider` interface
   - Concrete implementations are injected via the aggregator
   - Allows swapping provider implementations without changing endpoint code

### File Structure

```
src/
├── main.py                      # FastAPI application and endpoints
├── config/
│   └── settings.py              # Configuration and environment variables
├── models/
│   └── pricing.py               # Pydantic data models and schemas
└── services/
    ├── base_provider.py         # Abstract base class for all providers
    ├── openai_pricing.py        # OpenAI provider implementation
    ├── anthropic_pricing.py     # Anthropic provider implementation
    ├── google_pricing.py        # Google AI provider implementation
    ├── cohere_pricing.py        # Cohere provider implementation
    ├── mistral_pricing.py       # Mistral AI provider implementation
    └── pricing_aggregator.py    # Aggregator service orchestrating all providers
```

### Data Flow

1. **Client Request** → FastAPI Endpoint
2. **Endpoint** → Calls `get_pricing_aggregator()` for lazy initialization
3. **Aggregator** → Fetches data concurrently from all providers
4. **Providers** → Retrieve pricing data (static or via API)
5. **Aggregator** → Merges and caches results
6. **Endpoint** → Processes/transforms data (sorting, filtering, calculations)
7. **Response** → Serialized Pydantic model returned as JSON

### API Endpoints

- **`/health`** - Service health status
- **`/models`** - List all available LLM models
- **`/pricing`** - Get detailed pricing for all models
- **`/performance`** - Performance metrics (throughput, latency, context window)
- **`/use-cases`** - Model recommendations by use cases
- **`/cost-estimate`** - Calculate cost for single model
- **`/cost-estimate/batch`** - Compare costs across multiple models

### Scalability Considerations

- **Async/Await**: All I/O operations are non-blocking for concurrent request handling
- **Caching**: Aggregated data is cached to reduce repeated provider calls
- **Lazy Loading**: Aggregator initializes on-demand, not at startup
- **Stateless Design**: Endpoints are stateless, enabling horizontal scaling
- **Provider Independence**: Each provider is independent, enabling parallel execution