# Backwards Compatibility & API Versioning Guide

## Overview

This guide explains how the LLM Pricing MCP Server manages backwards compatibility and API versioning to ensure seamless upgrades for all users.

## Versioning Policy

### Current Status

- **Stable Version**: v1 (Current - fully backwards compatible)
- **Latest Version**: 1.5.1
- **Support Policy**: All patches maintain backwards compatibility until major version bump to v2

### Semantic Versioning

```
Version: 1.5.1
         │ │ └─ Patch (bug fixes, no breaking changes)
         │ └─── Minor (new features, no breaking changes)
         └───── Major (breaking changes)

Backwards Compatibility Rules:
├─ MAJOR (1→2): Breaking changes allowed
├─ MINOR (1.4→1.5): New features only, no breaking changes
└─ PATCH (1.5.1→1.5.2): Bug fixes only
```

## Backwards Compatibility Guarantees

### Guaranteed Stable Endpoints

These endpoints will never change until v2.0 (scheduled: 2025):

**Core Pricing Endpoints:**
- `GET /` - Server information
- `GET /models` - List all LLM models
- `GET /pricing` - Get pricing data for all models
- `GET /pricing?provider=<name>` - Get specific provider pricing
- `POST /cost-estimate` - Estimate cost for one model
- `POST /cost-estimate/batch` - Estimate costs for multiple models

**Performance & Discovery:**
- `GET /performance` - Get performance metrics
- `GET /performance?sort_by=<metric>` - Sort performance data
- `GET /use-cases` - Get model use case recommendations

**Operations:**
- `GET /health` - Basic health check
- `GET /health/detailed` - Comprehensive health info
- `GET /health/ready` - Readiness probe
- `GET /health/live` - Liveness probe

**Telemetry:**
- `GET /telemetry` - Get usage statistics

### What Can Change Without Breaking Compatibility

✅ Adding new optional query parameters:
```python
# v1.4
GET /pricing

# v1.5 - Old requests still work exactly the same
GET /pricing?detailed=true  # New optional parameter
```

✅ Adding new optional response fields:
```json
// v1.4 response
{
  "models": [...],
  "total_models": 87,
  "provider_status": [...]
}

// v1.5 response - has additional field but old clients work fine
{
  "models": [...],
  "total_models": 87,
  "provider_status": [...],
  "response_time_ms": 145.3  // New optional field
}
```

✅ Adding new endpoints:
```python
# v1.5 adds new endpoint
GET /api/versions  # Completely new, doesn't affect existing endpoints
```

✅ Improving response times

✅ Adding new models and providers to response data

✅ Expanding error messages

### What Causes Version Bump to v2

❌ Removing endpoints:
```python
# Not allowed in v1.x
# DELETE /cost-estimate  (would be breaking change)
```

❌ Removing required response fields:
```json
// v1.4 has these fields
{
  "model_name": "gpt-4",
  "provider": "OpenAI",
  "cost_per_input_token": 0.0003
}

// v2.0 might remove one:
{
  "model_name": "gpt-4",
  // "provider" field removed - BREAKING CHANGE
}
```

❌ Changing response structure:
```python
# v1.4 returns array
GET /pricing → [{ model_name: "gpt-4", ... }, ...]

# v2.0 might return object (breaking change)
GET /pricing → { "data": [{ ... }] }
```

❌ Removing query parameters:
```python
# v1.4 supports this
GET /pricing?provider=openai

# v2.0 would remove it (breaking change)
GET /pricing  # provider parameter no longer works
```

❌ Changing HTTP methods:
```python
# v1.4 uses GET
GET /cost-estimate?model=gpt-4&input_tokens=100&output_tokens=50

# v2.0 changes to POST (breaking change)
POST /cost-estimate  # GET no longer works
```

## Client Resilience Recommendations

### 1. Accept Unknown Fields

Always safely handle unknown response fields:

```python
# ✅ Good - ignores unknown fields gracefully
import json
response = requests.get('http://api/pricing')
models = response.json().get('models', [])

# ❌ Bad - assumes no new fields
models = response.json()['models']
if len(response.json()) > 3:  # Breaks if new fields added
    raise ValueError("Unexpected response structure")
```

### 2. Use Object Destructuring

```python
# ✅ Good - only accesses needed fields
def process_model(model_data):
    name = model_data.get('model_name')
    cost = model_data.get('cost_per_input_token', 0)
    return f"{name}: ${cost}"

# ❌ Bad - depends on field order or all fields existing
def process_model(model_data):
    return f"{model_data[0]}: ${model_data[1]}"
```

### 3. Check Before Accessing

```python
# ✅ Good - checks before using
provider = model.get('provider') or 'Unknown'
context_window = model.get('context_window')
if context_window:
    print(f"Context: {context_window}")

# ❌ Bad - assumes field always exists
print(f"Provider: {model['provider']}")  # KeyError if removed
```

### 4. Handle Missing Fields

```python
# ✅ Good - provides defaults
class ModelData:
    def __init__(self, data):
        self.name = data.get('model_name', 'Unknown')
        self.cost_in = data.get('cost_per_input_token', 0)
        self.cost_out = data.get('cost_per_output_token', 0)

# ❌ Bad - breaks if fields are missing
class ModelData:
    def __init__(self, data):
        self.name = data['model_name']  # KeyError
        self.cost_in = data['cost_per_input_token']  # KeyError
```

### 5. Validate API Version Before Critical Operations

```python
# ✅ Good - checks compatibility before critical operation
def cost_estimate(model_name, tokens):
    # Check API version supports cost-estimate
    version_info = requests.get('http://api/api/versions').json()
    if 'cost-estimate' not in version_info.get('current_version', ''):
        raise ValueError(f"Cost estimation not supported in {version_info['current_version']}")
    
    return requests.post('http://api/cost-estimate', json={
        'model_name': model_name,
        'input_tokens': tokens[0],
        'output_tokens': tokens[1]
    })
```

## Migration Planning

### Check for Breaking Changes Before Upgrading

```bash
# Get current version
curl http://localhost:8000/deployment/metadata | \
  jq '{version, api_versions, breaking_changes}'

# Check migration guide
curl http://localhost:8000/api/versions | \
  jq '.migration_guide_url'
```

### Timeline for v2.0 Migration

**v1.x Support Timeline:**
- **Now - Jun 2025**: v1.x is stable, actively supported
- **Jun 2025**: v2.0 released with breaking changes
- **Jun 2025 - Jun 2026**: Both v1 and v2 supported (dual-running)
- **Jun 2026**: v1.x support ends
- **After Jun 2026**: v1.x no longer available

**Recommended Migration Path:**

```
Now (v1.5)
    ↓
Q2 2025: Review v2 breaking changes (when released)
    ↓
Q3 2025: Update client code for v2 compatibility
    ↓
Q4 2025: Test with v2 beta runtime
    ↓
Q1 2026: Deploy v2 in production
    ↓
Mid 2026: v1 support ends
```

## Endpoint Stability Matrix

| Endpoint | Stable Until | Notes |
|----------|--------------|-------|
| GET / | v2.0 | Server info always available |
| GET /models | v2.0 | Model listing stable |
| GET /pricing | v2.0 | Pricing data format stable |
| POST /cost-estimate | v2.0 | Single model estimation stable |
| POST /cost-estimate/batch | v2.0 | Batch comparison stable |
| GET /performance | v2.0 | Performance metrics stable |
| GET /use-cases | v2.0 | Use case recommendations stable |
| GET /health | v2.0 | Health check stable |
| GET /telemetry | v2.1 | Telemetry added in v1.5, stable until v2.1 |
| GET /deployment/* | v2.0 | Deployment info stable |

## Response Field Stability

### PricingMetrics (Model Pricing Data)

**Guaranteed fields (v1.x - v2.0):**
- `model_name` (string)
- `provider` (string)
- `cost_per_input_token` (float)
- `cost_per_output_token` (float)
- `currency` (string, default "USD")
- `unit` (string)
- `source` (string, optional)
- `last_updated` (datetime)

**Computed fields added in v1.5 (stable until v2.0):**
- `cost_at_10k_tokens` (TokenVolumePrice)
- `cost_at_100k_tokens` (TokenVolumePrice)
- `cost_at_1m_tokens` (TokenVolumePrice)
- `estimated_time_1m_tokens` (float)

**May be added without breaking compatibility:**
- New optional fields
- New computed properties
- Expanded use_cases, strengths lists

### CostEstimateResponse

**Guaranteed fields:**
- `model_name` (string)
- `provider` (string)
- `input_tokens` (integer)
- `output_tokens` (integer)
- `input_cost` (float)
- `output_cost` (float)
- `total_cost` (float)
- `currency` (string)
- `timestamp` (datetime)

## Testing Your Client for Compatibility

### 1. Test with Latest Version

```bash
# Run against v1.5
curl http://localhost:8000/deployment/metadata | jq .version
# "1.5.1"

# Run your client code
python my_client.py
```

### 2. Simulate New Fields

```python
# Test that your code handles unknown fields
test_response = {
    "model_name": "gpt-4",
    "provider": "OpenAI",
    "cost_per_input_token": 0.0003,
    "new_field_v2": "some_value",  # Simulate field added in future
    "another_new_field": {"nested": "data"}
}

# Your code should handle this without errors
result = process_model_response(test_response)
assert result is not None
```

### 3. Test Missing Optional Fields

```python
# Test that your code handles missing optional fields
test_response = {
    "model_name": "gpt-4",
    "provider": "OpenAI",
    "cost_per_input_token": 0.0003,
    # "source" field is optional - not included
    # "context_window" field missing
}

# Your code should handle gracefully
result = process_model_response(test_response)
assert result is not None
```

### 4. Version Check

```python
# Always check API version before using new features
def use_new_feature():
    version_info = requests.get('http://api/api/versions').json()
    
    # Only use feature if API version supports it
    if 'v1.5' in version_info['current_version']:
        # v1.5+ supports telemetry_access feature
        return requests.get('http://api/telemetry')
    else:
        print("Feature requires API v1.5 or later")
        return None
```

## Common Migration Scenarios

### Scenario 1: Adding New Query Parameter

**Before (v1.4):**
```bash
curl http://api/pricing
```

**After (v1.5):**
```bash
curl http://api/pricing?include_predictions=true
```

**Compatibility:** ✅ Old code works unchanged, new code can use new parameter

### Scenario 2: Model Cost Calculation Change

**Before (v1.4):**
Real costs from providers without any computed fields

**After (v1.5):**
Costs + computed volume pricing (10K/100K/1M tokens)

**Your code should do:**
```python
# ✅ Good - use computed fields if available
model = get_model_pricing('gpt-4')
if 'cost_at_1m_tokens' in model:
    million_token_cost = model['cost_at_1m_tokens']['total_cost']
else:
    # Fallback for older API versions
    million_token_cost = calculate_manually(model)
```

### Scenario 3: New Provider Added

**Before (v1.4):**
```json
{
  "providers": ["OpenAI", "Anthropic", "Google", ...],
  "total_models": 60
}
```

**After (v1.5):**
```json
{
  "providers": ["OpenAI", "Anthropic", "Google", ..., "Amazon Bedrock"],
  "total_models": 87
}
```

**Compatibility:** ✅ Response structure unchanged, just more data

**Your code should:**
```python
# ✅ Good - don't hardcode expected number of providers
models = get_all_models()
print(f"Available providers: {len(models)}")  # Works with any count

# ❌ Bad - fails if count changes
models = get_all_models()
assert len(models) == 60  # Breaks in v1.5 with 87 models
```

## FAQ

### Q: Will my client code break when I upgrade?

**A:** Only if you:
1. Access by array index instead of field name
2. Assume all fields always exist
3. Depend on exact response field count
4. Parse JSON as strings instead of objects

Use recommended practices above and you'll be fine.

### Q: How do I know if a parameter is required vs optional?

**A:** Check the API documentation or your type hints:

```python
# Optional parameter (no default required)
GET /pricing?provider=openai

# Required parameter (must provide)
POST /cost-estimate with body:
{
  "model_name": "required",      # Always needed
  "input_tokens": 100,            # Always needed
  "output_tokens": 50             # Always needed
}
```

### Q: Can I use beta API features now?

**A:** Features in v1.5 (like telemetry) are fully stable and supported. "Beta" is only for experimental endpoints not documented for production use.

### Q: What if I find a bug in behavior?

**A:** Bugs are fixed in patch versions (1.5.1 → 1.5.2) without breaking compatibility. Your client code doesn't need changes for patch updates.

## Support

For backwards compatibility questions:
- Check this guide
- Review [BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md) for deployment practices
- Open an issue on GitHub with details
- Check [API version info](http://api/api/versions) for official status

---

**Last Updated:** February 17, 2026  
**Status:** v1.5.1 - Stable and backwards compatible until June 2025 migration to v2.0
