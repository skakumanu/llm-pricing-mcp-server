# Blue-Green Deployment & Backwards Compatibility Guide

## Overview

This guide explains how to deploy the LLM Pricing MCP Server with zero downtime using blue-green deployment, and how the service maintains backwards compatibility for all clients.

## Table of Contents

1. [Blue-Green Deployment](#blue-green-deployment)
2. [Backwards Compatibility](#backwards-compatibility)
3. [Production Deployment Best Practices](#production-deployment-best-practices)
4. [Health Checks & Monitoring](#health-checks--monitoring)
5. [Graceful Shutdown](#graceful-shutdown)
6. [Troubleshooting](#troubleshooting)

## Blue-Green Deployment

### What is Blue-Green Deployment?

Blue-green deployment maintains two identical production environments:
- **Blue**: Current production environment handling traffic
- **Green**: New version being deployed

Traffic is switched from Blue to Green after verification, with instant rollback capability if issues arise.

### Architecture

```
                    ┌─────────────────────────┐
                    │   Load Balancer         │
                    │   (Traffic Router)      │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
            ┌───────▼────────┐        ┌──────▼──────────┐
            │   Blue (v1)    │        │   Green (v1.1)  │
            │   Active       │        │   Standby       │
            │   Handling     │        │   Ready to      │
            │   Traffic      │        │   Receive       │
            └────────────────┘        │   Traffic       │
                                      └─────────────────┘
```

### Environment Detection

The application detects its deployment environment via environment variables:

```bash
# Set these when deploying
export ENV=production
export DEPLOYMENT_GROUP=blue  # or green
export REGION=us-east-1
export AVAILABILITY_ZONE=us-east-1a
export INSTANCE_ID=i-0123456789abcdef0
export DEPLOYMENT_TIMESTAMP=2024-02-17T14:30:00Z
```

Verify detection:
```bash
curl http://localhost:8000/deployment/info
```

Response:
```json
{
  "environment": "production",
  "deployment_group": "blue",
  "region": "us-east-1",
  "availability_zone": "us-east-1a",
  "instance_id": "i-0123456789abcdef0",
  "deployment_timestamp": "2024-02-17T14:30:00Z",
  "service_uptime_seconds": 3600
}
```

### Deployment Flow

#### Step 1: Deploy Green Instance

```bash
# Deploy new version
docker pull myregistry.azurecr.io/llm-pricing:v1.1.0

# Start green instance
docker run -d \
  --name llm-pricing-green \
  -e ENV=production \
  -e DEPLOYMENT_GROUP=green \
  -e REGION=us-east-1 \
  -e INSTANCE_ID=green-1 \
  -p 8001:8000 \
  myregistry.azurecr.io/llm-pricing:v1.1.0
```

#### Step 2: Verify Green Instance

```bash
# Health check
curl http://localhost:8001/health/ready

# Detailed health
curl http://localhost:8001/health/detailed

# Functional test
curl http://localhost:8001/pricing

# Verify version
curl http://localhost:8001/deployment/metadata | jq .version
```

#### Step 3: Switch Traffic (Blue → Green)

Update load balancer to route traffic to green:

```bash
# Example with AWS ELB/ALB
# Update target group to point to green instance
aws elbv2 modify-target-group-attributes \
  --target-group-arn arn:aws:elasticloadbalancing:... \
  --attributes Key=deregistration_delay.timeout_seconds,Value=30
```

#### Step 4: Monitor Green Instance

```bash
# Monitor in real-time
watch -n 5 'curl -s http://localhost:8001/health/detailed | jq .'

# Check telemetry
curl http://localhost:8001/telemetry | jq '.overall_stats'

# Verify no errors
curl http://localhost:8001/telemetry | jq '.endpoints[] | select(.error_count > 0)'
```

#### Step 5: Decommission Blue

Once green is stable (5-10 minutes of traffic):

```bash
# Gracefully shut down blue
curl -X POST http://localhost:8000/deployment/shutdown \
  -H "Content-Type: application/json" \
  -d '{"drain_timeout_seconds": 30}'

# Monitor shut down
curl http://localhost:8000/deployment/shutdown/status

# Stop blue container once drained
docker stop llm-pricing-blue
docker rm llm-pricing-blue
```

### Rollback

If issues occur in green, switch traffic back to blue:

```bash
# Immediately switch traffic back
# (Load balancer configuration change)

# Then gracefully shut down green
curl -X POST http://localhost:8001/deployment/shutdown \
  -H "Content-Type: application/json" \
  -d '{"drain_timeout_seconds": 30}'
```

## Backwards Compatibility

### API Versioning Strategy

All endpoints maintain backwards compatibility. The current stable version is **v1**.

#### Checking API Version Support

```bash
curl http://localhost:8000/api/versions

# Response
{
  "current_version": "v1",
  "all_versions": ["v1"],
  "deprecated_versions": [],
  "migration_guide_url": "https://github.com/.../MIGRATION.md"
}
```

#### Deprecated Endpoints

Deprecated endpoints still work but return a deprecation header:

```
Deprecation: true
Sunset: Sun, 01 Jul 2025 23:59:59 GMT
Link: <https://docs.example.com/migration>; rel="deprecation"
```

### Endpoint Stability Guarantee

**Guaranteed backwards compatible (until v2.x):**

- `GET /` - Server info
- `GET /models` - List models
- `GET /pricing` - Get pricing data
- `POST /cost-estimate` - Single model cost
- `POST /cost-estimate/batch` - Batch cost comparison
- `GET /performance` - Performance metrics
- `GET /use-cases` - Use case recommendations
- `GET /health` - Basic health check
- `GET /telemetry` - Usage metrics

**Safe to add without breaking changes:**

- New optional query parameters
- New fields in responses
- New endpoints

**What triggers version bump:**

- Removing endpoints
- Removing required response fields
- Changing response format for existing fields
- Removing query parameters

### Response Format Stability

Responses are designed to be forward/backward compatible:

```json
// v1.5.1 response
{
  "models": [...],
  "total_models": 87,
  "provider_status": [...],
  "timestamp": "2024-02-17T14:30:00Z",
  // New fields added in future versions are optional
  // Clients ignore unknown fields
}
```

Clients should:
- Ignore unknown response fields
- Treat missing optional fields as null
- Not depend on field order
- Never hardcode response structure

### Client Integration Guidelines

**Recommended practices:**

```python
# Good - handles unknown fields gracefully
response = requests.get('http://api/pricing')
models = response.json().get('models', [])
for model in models:
    name = model.get('model_name')
    price_in = model.get('cost_per_input_token')
    # Unknown fields are silently ignored
```

```python
# Bad - breaks if response changes
response = requests.get('http://api/pricing')
data = response.json()
# Accessing by index is fragile
models = data[0]  # What if response structure changes?
```

## Production Deployment Best Practices

### 1. Health Check Configuration

Configure your orchestrator to use:

- **Liveness probe** (K8s livenessProbe): `/health/live`
  - Restart if unhealthy
  - Check every 30 seconds
  - 3 consecutive failures to restart

- **Readiness probe** (K8s readinessProbe): `/health/ready`
  - Remove from load balancer if not ready
  - Check every 10 seconds
  - 1 failure to remove from routing

Example Kubernetes deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-pricing
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: llm-pricing
        image: myregistry.azurecr.io/llm-pricing:v1.5.1
        ports:
        - containerPort: 8000
        
        # Liveness probe - restart if unhealthy
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 30
          timeoutSeconds: 5
          failureThreshold: 3
        
        # Readiness probe - remove from load balancer if not ready
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 1
        
        # Graceful shutdown
        lifecycle:
          preStop:
            exec:
              command: ["/bin/sh", "-c", "sleep 5 && curl -X POST http://localhost:8000/deployment/shutdown -d '{\"drain_timeout_seconds\": 30}'"]
        
        terminationGracePeriodSeconds: 45
        
        env:
        - name: ENV
          value: "production"
        - name: DEPLOYMENT_GROUP
          valueFrom:
            fieldRef:
              fieldPath: metadata.labels['deployment.group']
        - name: INSTANCE_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
```

### 2. Load Balancer Configuration

**AWS ALB:**

```bash
aws elbv2 create-target-group \
  --name llm-pricing \
  --protocol HTTP \
  --port 8000 \
  --vpc-id vpc-xxx \
  --health-check-path /health/ready \
  --health-check-protocol HTTP \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 2
```

**Traffic switching:**

```bash
# Drain connections from blue (30 second timeout)
aws elbv2 modify-target-group-attributes \
  --target-group-arn arn:aws:elasticloadbalancing:... \
  --attributes Key=deregistration_delay.timeout_seconds,Value=30

# Deregister blue from target group (gracefully)
aws elbv2 deregister-targets \
  --target-group-arn arn:aws:elasticloadbalancing:... \
  --targets Id=blue-instance
```

### 3. Docker Best Practices

Dockerfile:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Health check built in
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health/ready || exit 1

# Allow time for graceful shutdown
STOPSIGNAL SIGTERM

ENTRYPOINT ["python", "src/main.py"]
```

### 4. Monitoring & Alerting

Monitor these metrics:

```bash
# Real-time dashboard
watch -n 5 'curl -s http://localhost:8000/health/detailed | jq .'

# Key metrics
curl http://localhost:8000/health/detailed | jq '{
  status: .status,
  uptime: .uptime_seconds,
  active_requests: .metrics.active_requests,
  avg_response_time: .metrics.avg_response_time_ms,
  providers: (.services | length)
}'
```

Set alerts for:

- Service status = "unhealthy"
- Active requests > 1000 (capacity planning)
- Avg response time > 500ms (performance degradation)
- Error rate > 1% (from telemetry)
- Uptime changes (unexpected restarts)

## Health Checks & Monitoring

### Health Check Endpoints

| Endpoint | Purpose | For |
|----------|---------|-----|
| `/health` | Basic health | Backwards compatibility |
| `/health/live` | Liveness probe | K8s, orchestrators |
| `/health/ready` | Readiness probe | Load balancers, routing |
| `/health/detailed` | Comprehensive status | Monitoring dashboards |

### Monitoring Examples

```bash
# Real-time status
curl -s http://localhost:8000/health/detailed | jq '.status'

# Check if ready for traffic
curl -s http://localhost:8000/health/ready | jq '.ready'

# Get performance metrics
curl -s http://localhost:8000/telemetry | jq '.overall_stats'

# List active endpoints being used
curl -s http://localhost:8000/telemetry | jq '.endpoints[] | {path, method, call_count, avg_response_time_ms}'

# Check provider adoption
curl -s http://localhost:8000/telemetry | jq '.provider_adoption[] | {provider_name, model_requests, unique_models_requested}'
```

## Graceful Shutdown

### What Happens During Graceful Shutdown

1. Service stops accepting new requests
2. In-flight requests are allowed to complete
3. Wait up to `drain_timeout_seconds` (default 30)
4. Service exits cleanly
5. Orchestrator detects exit and terminates container

### Initiating Graceful Shutdown

**Via API (for orchestrators):**

```bash
curl -X POST http://localhost:8000/deployment/shutdown \
  -H "Content-Type: application/json" \
  -d '{"drain_timeout_seconds": 30}'
```

**Via signal (K8s/Docker):**

```bash
# Kubernetes will send SIGTERM when terminating pod
# Container handles it automatically via signal handlers
```

**Monitor shutdown progress:**

```bash
curl http://localhost:8000/deployment/shutdown/status

# Response
{
  "shutting_down": true,
  "active_requests": 3,
  "drain_timeout_seconds": 30,
  "started_at": "2024-02-17T14:30:00Z"
}
```

### Kubernetes PreStop Hook

Ensure graceful shutdown in Kubernetes:

```yaml
lifecycle:
  preStop:
    exec:
      command:
      - /bin/sh
      - -c
      - |
        # Wait for health check to fail (removes from load balancer)
        sleep 5
        # Request graceful shutdown
        curl -X POST http://localhost:8000/deployment/shutdown \
          -d '{"drain_timeout_seconds": 25}'
        # Wait for completion
        sleep 25

terminationGracePeriodSeconds: 45  # 5 + 25 + 15 second buffer
```

## Troubleshooting

### Instance Not Receiving Traffic

**Check readiness:**

```bash
curl -v http://localhost:8000/health/ready
# Should return ready=true
```

**Check deployment info:**

```bash
curl http://localhost:8000/deployment/info
# Verify deployment_group is correct
```

**Check load balancer routing:**

```bash
# Verify instance is in target group
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:...
```

### Slow Startup

**Check dependencies:**

```bash
curl http://localhost:8000/health/detailed | jq '.services'
# Verify all services are healthy
```

**Review logs:**

```bash
docker logs <container-id> | grep -i "initialization\|error"
```

### High Error Rate After Deployment

**Check recent changes:**

```bash
curl http://localhost:8000/deployment/metadata | jq '.breaking_changes'
```

**Verify telemetry:**

```bash
curl http://localhost:8000/telemetry | jq '.overall_stats | {total_requests, total_errors, error_rate}'
```

**Identify failing endpoints:**

```bash
curl http://localhost:8000/telemetry | jq '.endpoints | sort_by(.error_count) | reverse | .[0:3]'
```

### Stuck Graceful Shutdown

If instances stay shutting down:

```bash
# Check shutdown status
curl http://localhost:8000/deployment/shutdown/status

# Force timeout (last resort)
docker stop -t 5 <container-id>  # Gives 5 more seconds then kills
```

## Summary

| Task | Command |
|------|---------|
| Deploy green | `docker run ... -e DEPLOYMENT_GROUP=green ...` |
| Verify green | `curl http://localhost:8001/health/ready` |
| Check version | `curl http://localhost:8001/deployment/metadata` |
| Switch traffic | Load balancer configuration change |
| Graceful shutdown | `curl -X POST http://localhost:8000/deployment/shutdown` |
| Check compatibility | `curl http://localhost:8000/api/versions` |
| Monitor health | `curl http://localhost:8000/health/detailed` |

For questions or issues, refer to the main [README.md](README.md) or open an issue on GitHub.
