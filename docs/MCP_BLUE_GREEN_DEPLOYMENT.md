# MCP Server Blue-Green Deployment Guide

**Version**: 1.6.0  
**Last Updated**: February 20, 2026  
**Status**: ✅ Production Deployed (All tests passing)

---

## Overview

Blue-green deployment enables **zero-downtime updates** by running two identical production environments:

- **Blue**: Current production version (active)
- **Green**: New version being tested (standby)
- **Switch**: Route traffic to green only after automated validation (6 tests)
- **Rollback**: Instant revert to blue if issues detected

## Key Benefits

✓ **Zero Downtime**: No service interruption during deployment  
✓ **Fast Rollback**: Switch back instantly if problems occur  
✓ **Full Testing**: Validate new version before production traffic  
✓ **A/B Testing**: Compare both versions before switch  
✓ **Easy Cleanup**: Remove old version after verification

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Production Router                    │
│                  (Traffic Controller)                   │
└──────────────────┬──────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
    ╔═══════╗           ╔═══════╗
    ║ BLUE  ║ ACTIVE    ║GREEN  ║ STANDBY
    ║ v1.5  ║────────→  ║v1.6.0 ║
    ║ :8001 ║           ║:8002  ║
    ╚═══════╝           ╚═══════╝
        ↑                   ↑
        └─── Swap after ────┘
            validation
```

---

## Pre-Deployment Checklist

### Code Readiness
- [x] All 159 tests passing
- [x] All 6 MCP tools functional
- [x] Integration tests: 100% pass rate
- [x] No critical security issues
- [x] Version bumped to 1.6.0

### Infrastructure Readiness
- [ ] Enough disk space for both versions (~500MB)
- [ ] Sufficient memory for parallel processes (~1GB)
- [ ] Network ports 8001, 8002 available
- [ ] Monitoring/logging configured

### Documentation
- [x] Deployment guide reviewed
- [x] Rollback procedure documented
- [x] Health check endpoints identified
- [x] Testing scripts ready

---

## Step 1: Verify Current Production (Blue)

### Check Current Version
```bash
git describe --tags
# Expected: v1.5.4
```

### Start Blue Environment (if not already running)
```bash
cd /path/to/llm-pricing-mcp-server
source .venv/bin/activate  # or .venv\Scripts\Activate.ps1
python mcp/server.py
```

### Verify Blue Health
```bash
python scripts/monitor_mcp_server.py --check
```

**Expected Output**:
```
SERVER STATUS
  Process Running: [PASS] YES
  
IMPORT VALIDATION
  mcp.server: [PASS] OK
  mcp.tools.tool_manager: [PASS] OK
  pricing_aggregator: [PASS] OK

AVAILABLE TOOLS (5 of 5)
  [PASS] get_all_pricing
  [PASS] estimate_cost
  [PASS] compare_costs
  [PASS] get_performance_metrics
  [PASS] get_use_cases
```

---

## Step 2: Deploy Green Environment (v1.6.0)

### Using the Deployment Manager (Recommended)

```bash
# Start green deployment with automated validation
python scripts/mcp_blue_green_deploy.py deploy --version 1.6.0

# Expected output (5 phases):
# [PHASE 1] Starting green environment...
#   [SUCCESS] GREEN environment started (PID: xxxxx)
#
# [PHASE 2] Validating green environment...
#   Running quick_validate.py (6 automated tests)...
#   [PASS] Server started
#   [PASS] Received initialization message
#   [PASS] Initialize request-response successful
#   [PASS] All 6 tools discovered
#   [PASS] get_all_pricing executes successfully
#   [PASS] estimate_cost executes successfully
#   [PASS] compare_costs executes successfully
#   [PASS] get_performance_metrics executes successfully
#   [PASS] get_use_cases executes successfully
#   [PASS] Error handling works correctly
#   [SUCCESS] GREEN validation passed
#
# [PHASE 3] Swapping traffic to green environment...
#   [SUCCESS] Traffic switched to GREEN (v1.6.0)
#
# [PHASE 4] Monitoring previous blue environment for graceful shutdown...
#   [INFO] Stopping BLUE environment...
#
# [PHASE 5] Promoting green to primary (blue)...
#   [SUCCESS] Blue-green deployment to v1.6.0 SUCCESSFUL
```

**What Happens During Deployment**:
1. **Phase 1**: Starts green environment on port 8002
2. **Phase 2**: Runs `scripts/quick_validate.py` automatically (6 tests, ~15 sec)
   - Server startup check
   - MCP initialization handshake
   - Tool discovery (6/6 tools)
   - Tool execution (all 6 tools)
   - Error handling validation
3. **Phase 3**: Switches traffic to green if validation passes
4. **Phase 4**: Gracefully stops old blue environment
5. **Phase 5**: Promotes green to become the new production (blue)

**Deployment Safety**:
- ❌ If Phase 2 validation fails, deployment stops immediately
- ✅ If validation passes, deployment proceeds automatically
- ⏱️ Zero downtime during the traffic switch
- 🔄 Instant rollback available if issues detected

### Manual Deployment (Alternative)

If you prefer step-by-step control:

```powershell
# 1. Start new terminal for green environment
cd /path/to/llm-pricing-mcp-server
source .venv/bin/activate

# Set environment variable to use different port
$env:MCP_PORT = "8002"
python mcp/server.py
```

---

## Step 3: Test Green Environment

### Health Check
```bash
python scripts/monitor_mcp_server.py --check
# Verify: All imports OK, all 6 tools discovered
```

### Quick Validation (Automated)
```bash
python scripts/quick_validate.py
# Expected: 6/6 tests passed in ~15 seconds
# Tests: Startup, init, discovery, 6 tool execution, error handling
```

**Expected Output**:
```
[PASS] Server started (PID: xxxx)
[PASS] Received initialization message
[PASS] Initialize request-response successful
[PASS] All 6 tools discovered
[PASS] get_all_pricing executes successfully
[PASS] estimate_cost executes successfully
[PASS] compare_costs executes successfully
[PASS] get_performance_metrics executes successfully
[PASS] get_use_cases executes successfully
[PASS] Error handling works correctly
[SUCCESS] ALL TESTS PASSED - Server is ready for production
```

### Tool Functionality Tests
```bash
python scripts/test_mcp_server.py
# Expected: 7/7 tests passing (tool discovery + 6 tools)
```

### Comprehensive Client Validation (Optional)
```bash
python scripts/validate_mcp_client.py
# Expected: 16 test scenarios
# Focus on: No crashes, error handling works, response times < 2s
```

**Note**: The deployment manager (`mcp_blue_green_deploy.py`) automatically runs `quick_validate.py` during Phase 2 validation. You don't need to run it manually when using the deployment manager.

### Manual Function Tests

**Test 1: Tool Discovery**
```bash
python -c "
import json
import subprocess
import sys
import time

p = subprocess.Popen([sys.executable, 'mcp/server.py'], 
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, text=True)
time.sleep(1)

# Get init message
p.stdout.readline()

# Send initialize
init = {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}
p.stdin.write(json.dumps(init) + '\n')
p.stdin.flush()
p.stdout.readline()

# Send tools/list
req = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}
p.stdin.write(json.dumps(req) + '\n')
p.stdin.flush()
resp = json.loads(p.stdout.readline())

tools = resp.get('result', {}).get('tools', [])
print(f'[PASS] Discovered {len(tools)}/6 tools')

p.terminate()
"
```

**Test 2: Cost Estimation**
```bash
python -c "
import asyncio
from mcp.tools.estimate_cost import EstimateCostTool

tool = EstimateCostTool()
result = asyncio.run(tool.execute({
    'model_name': 'gpt-4',
    'input_tokens': 5000,
    'output_tokens': 2000
}))

if 'input_cost' in result and 'output_cost' in result:
    print(f'[PASS] Cost estimation working: {result}')
else:
    print('[FAIL] Invalid response')
"
```

**Test 3: Cost Comparison**
```bash
python -c "
import asyncio
from mcp.tools.compare_costs import CompareCostsTool

tool = CompareCostsTool()
result = asyncio.run(tool.execute({
    'model_names': ['gpt-4', 'gpt-3.5-turbo', 'claude-3-opus'],
    'input_tokens': 5000,
    'output_tokens': 2000
}))

if 'comparison' in result:
    print(f'[PASS] Cost comparison working: {len(result[\"comparison\"])} models')
else:
    print('[FAIL] Invalid response')
"
```

### Performance Baseline

```bash
python -c "
import subprocess
import sys
import time
import json

times = []
p = subprocess.Popen([sys.executable, 'mcp/server.py'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
time.sleep(2)
p.stdout.readline()  # init
init = {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}
p.stdin.write(json.dumps(init) + '\n')
p.stdin.flush()
p.stdout.readline()

for i in range(10):
    start = time.time()
    req = {'jsonrpc': '2.0', 'id': i+1, 'method': 'tools/call', 
           'params': {'name': 'estimate_cost', 
                     'arguments': {'model_name': 'gpt-4', 'input_tokens': 1000, 'output_tokens': 500}}}
    p.stdin.write(json.dumps(req) + '\n')
    p.stdin.flush()
    p.stdout.readline()
    elapsed = (time.time() - start) * 1000
    times.append(elapsed)

print(f'[PASS] Response times (ms): avg={sum(times)/len(times):.1f}, min={min(times):.1f}, max={max(times):.1f}')
p.terminate()
"
```

---

## Step 4: Validation Success Criteria

All of the following must pass before switching traffic:

| Test | Expected | Status |
|------|----------|--------|
| Process Running | YES | [ ] |
| All 6 Tools Discovered | 6/6 | [ ] |
| Tool Listing Test | PASS | [ ] |
| estimate_cost Test | PASS | [ ] |
| compare_costs Test | PASS | [ ] |
| get_performance_metrics Test | PASS | [ ] |
| get_use_cases Test | PASS | [ ] |
| Error Handling | Works | [ ] |
| Average Response Time | < 10ms | [ ] |
| Max Response Time | < 100ms | [ ] |
| No Crashes | Clean shutdown | [ ] |

---

## Step 5: Traffic Switch (Blue → Green)

### Automatic Switch
```bash
# The deployment manager handles this automatically
python scripts/mcp_blue_green_deploy.py deploy --version 1.6.0
# After all validations pass, traffic automatically switches
```

### Manual Switch (if needed)
```bash
# 1. Verify green is ready
python scripts/test_mcp_server.py  # Should pass

# 2. Update configuration/router to point to green
# (Implementation depends on your environment)

# 3. Monitor for errors (next 5 minutes)
python scripts/monitor_mcp_server.py --interval 30 --duration 300

# 4. Once verified, stop blue
pkill -f "python mcp/server.py"  # or taskkill on Windows
```

---

## Step 6: Post-Deployment Monitoring

### Immediate (First 5 Minutes)
```bash
# Monitor every 30 seconds
python scripts/monitor_mcp_server.py --start --interval 30

# Watch for:
# - Server running
# - All tools available
# - No error messages
# - Response times normal
```

### Short-term (First Hour)
- [  ] Check server logs: `tail -100 mcp_server.log`
- [  ] Verify no unusual resource usage
- [  ] Test with real client queries
- [  ] Check for any error patterns

### Daily (First Week)
- [  ] Run full test suite: `python scripts/test_mcp_server.py`
- [  ] Review error logs
- [  ] Monitor resource usage
- [  ] Collect metrics for baseline

---

## Rollback Procedure

If issues are detected post-deployment:

### Automatic Rollback
```bash
python scripts/mcp_blue_green_deploy.py rollback --previous-version 1.5.4
# Automatically stops green, restarts blue with v1.5.4
```

### Manual Rollback
```bash
# 1. Stop green (1.6.0)
pkill -f "python mcp/server.py"

# 2. Start blue (1.5.4)
git checkout v1.5.4
source .venv/bin/activate
python mcp/server.py

# 3. Verify health
python scripts/monitor_mcp_server.py --check

# 4. Confirm functionality
python scripts/test_mcp_server.py
```

### When to Rollback

**Immediate Rollback Required**:
- Server crashes on startup
- All tools unavailable
- High error rate (> 10% of requests)
- Database connection failures
- Memory leak detected

**Review & Plan Rollback**:
- Intermittent failures
- Performance degradation
- Specific feature broken
- Data integrity issues

---

## Deployment Status Commands

### Check Status
```bash
python scripts/mcp_blue_green_deploy.py status
```

Expected output:
```
Blue Environment (Port 8001):
  Status: RUNNING
  Health: Process running
  Process: 12345

Green Environment (Port 8002):
  Status: STOPPED
  Health: Process not running
  Process: None

Active Environment: BLUE
```

### View Deployment Log
```bash
cat deployment.log
```

---

## Troubleshooting

### Port Already in Use

**Error**: `Address already in use`

**Solution**:
```bash
# Find process using port
lsof -i :8001  # macOS/Linux
Get-Process -Id (Get-NetTCPConnection -LocalPort 8001).OwningProcess  # Windows

# Kill process
kill -9 <PID>  # macOS/Linux
taskkill /PID <PID> /F  # Windows
```

### Green Validation Fails

**Error**: `Validation failed, rolling back`

**Steps**:
1. Check error logs: `cat mcp_server.log`
2. Verify imports: `python -c "from mcp.server import MCPServer"`
3. Test specific tool: `python -c "from mcp.tools.estimate_cost import EstimateCostTool"`
4. Check dependencies: `pip list | grep -E "pydantic|httpx"`

### Memory Issues

**Error**: High memory usage, process slowdown

**Check**:
```bash
# Monitor resource usage
python scripts/monitor_mcp_server.py --interval 10 --duration 120

# Check for memory leaks
python -c "import psutil; p = psutil.Process(); print(p.memory_info())"
```

### Comparison Tool Fails

**Error**: `compare_costs` returns invalid format

**Test**:
```bash
python -c "
import asyncio
from mcp.tools.compare_costs import CompareCostsTool

result = asyncio.run(CompareCostsTool().execute({
    'model_names': ['gpt-4', 'gpt-3.5-turbo'],
    'input_tokens': 1000,
    'output_tokens': 500
}))
print(result)
"
```

---

## Success Indicators

You'll know deployment succeeded when:

✓ Green environment starts without errors  
✓ All 6 tools successfully discovered  
✓ Tool execution returns valid responses  
✓ Response times < 100ms  
✓ Error handling works for invalid inputs  
✓ Process stays running for > 5 minutes  
✓ Blue can be safely shut down  
✓ Blue-green swap completes  
✓ Monitoring shows stable metrics  
✓ No errors in logs  

---

## Post-Deployment Tasks

### Week 1
- [  ] Monitor daily for issues
- [  ] Collect performance metrics
- [  ] Document any anomalies
- [  ] Gather user feedback

### Week 2+
- [  ] Remove old version if stable
- [  ] Archive deployment logs
- [  ] Update runbooks if needed
- [  ] Plan next feature release

### Monthly
- [  ] Review deployment logs
- [  ] Update documentation
- [  ] Plan optimizations
- [  ] Security review

---

## Testing Scripts Reference

The deployment process uses three testing scripts:

### 1. quick_validate.py (Automated in Phase 2)
**Purpose**: Fast pre-deployment validation  
**Location**: `scripts/quick_validate.py`  
**Duration**: ~15 seconds  
**Tests**: 7 core tests (startup, init, discovery, 6 tools, error handling)  
**Usage**: Automatically run by deployment manager  
**Exit Code**: 0 = pass (deploy), 1 = fail (abort)

### 2. test_mcp_server.py (Manual Integration Testing)
**Purpose**: Integration testing with detailed output  
**Location**: `scripts/test_mcp_server.py`  
**Duration**: ~30 seconds  
**Tests**: Tool discovery + 5 tool executions  
**Usage**: `python scripts/test_mcp_server.py`  
**Best For**: Local development, post-deployment verification

### 3. validate_mcp_client.py (Comprehensive QA)
**Purpose**: Exhaustive validation with schema checks  
**Location**: `scripts/validate_mcp_client.py`  
**Duration**: ~2 minutes  
**Tests**: 16 scenarios with edge cases  
**Usage**: `python scripts/validate_mcp_client.py`  
**Best For**: Release candidates, full QA cycles

**For detailed testing documentation**, see [MCP_TESTING.md](MCP_TESTING.md)

---

## Reference

**Deployment Script**: [scripts/mcp_blue_green_deploy.py](../scripts/mcp_blue_green_deploy.py)  
**Quick Validation**: [scripts/quick_validate.py](../scripts/quick_validate.py)  
**Integration Tests**: [scripts/test_mcp_server.py](../scripts/test_mcp_server.py)  
**Comprehensive Tests**: [scripts/validate_mcp_client.py](../scripts/validate_mcp_client.py)  
**Monitoring Guide**: [docs/MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md)  
**Testing Guide**: [docs/MCP_TESTING.md](MCP_TESTING.md)  
**Production Checklist**: [docs/MCP_PRODUCTION_CHECKLIST.md](MCP_PRODUCTION_CHECKLIST.md)  

---

## Emergency Contact

If deployment fails:
1. Check logs: `deployment.log` and `mcp_server.log`
2. Run rollback: `python scripts/mcp_blue_green_deploy.py rollback --previous-version 1.5.4`
3. Verify recovery: `python scripts/quick_validate.py`
4. Review errors and try again

**Last Updated**: February 20, 2026  
**Current Version**: 1.6.0  
**Deployment Status**: ✅ Production (All tests passing)
