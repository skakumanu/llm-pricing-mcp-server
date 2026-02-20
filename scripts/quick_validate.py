#!/usr/bin/env python
"""
Quick MCP Server Validation

Simple validation focused on functionality rather than strict response schema.
Validates that server starts and tools execute without errors.
"""

import subprocess
import sys
import time
import json
from pathlib import Path

# Fix encoding on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def validate_server():
    """Quick validation that server works."""
    print("Starting MCP Server for quick validation...\n")
    
    # Start server
    server = subprocess.Popen(
        [sys.executable, "mcp/server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    time.sleep(2)
    
    try:
        if server.poll() is not None:
            print("[FAIL] Server failed to start")
            return False
        
        print(f"[PASS] Server started (PID: {server.pid})\n")
        
        # Skip init message
        init_msg = server.stdout.readline()
        if not init_msg:
            print("[FAIL] No initialization message")
            return False
        print("[PASS] Received initialization message\n")
        
        # Send initialize request
        init_req = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {}
        }
        server.stdin.write(json.dumps(init_req) + "\n")
        server.stdin.flush()
        
        init_resp = server.stdout.readline()
        if not init_resp:
            print("[FAIL] No initialize response")
            return False
        print("[PASS] Initialize request-response successful\n")
        
        # Test tool discovery
        print("Testing TOOL DISCOVERY...")
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        tools = resp.get("result", {}).get("tools", [])
        if len(tools) != 6:
            print(f"[FAIL] Expected 6 tools, got {len(tools)}")
            return False
        print(f"[PASS] All 6 tools discovered\n")
        
        # Test each tool
        tests_passed = 0
        tests_failed = 0
        
        print("Testing TOOL EXECUTION...")
        
        # Test 1: get_all_pricing
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_all_pricing", "arguments": {}}
        }
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        if "result" in resp and resp["result"]:
            print("[PASS] get_all_pricing executes successfully")
            tests_passed += 1
        else:
            print("[FAIL] get_all_pricing failed")
            tests_failed += 1
        
        # Test 2: estimate_cost
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "estimate_cost",
                "arguments": {
                    "model_name": "gpt-4",
                    "input_tokens": 1000,
                    "output_tokens": 500
                }
            }
        }
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        if "result" in resp and resp["result"]:
            print("[PASS] estimate_cost executes successfully")
            tests_passed += 1
        else:
            print("[FAIL] estimate_cost failed")
            tests_failed += 1
        
        # Test 3: compare_costs
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "compare_costs",
                "arguments": {
                    "model_names": ["gpt-4", "gpt-3.5-turbo"],
                    "input_tokens": 1000,
                    "output_tokens": 500
                }
            }
        }
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        if "result" in resp and resp["result"]:
            print("[PASS] compare_costs executes successfully")
            tests_passed += 1
        else:
            print("[FAIL] compare_costs failed")
            tests_failed += 1
        
        # Test 4: get_performance_metrics
        req = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_performance_metrics",
                "arguments": {"model_name": "gpt-4"}
            }
        }
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        if "result" in resp and resp["result"]:
            print("[PASS] get_performance_metrics executes successfully")
            tests_passed += 1
        else:
            print("[FAIL] get_performance_metrics failed")
            tests_failed += 1
        
        # Test 5: get_use_cases
        req = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "get_use_cases",
                "arguments": {"model_name": "gpt-4"}
            }
        }
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        if "result" in resp and resp["result"]:
            print("[PASS] get_use_cases executes successfully")
            tests_passed += 1
        else:
            print("[FAIL] get_use_cases failed")
            tests_failed += 1
        
        print(f"\nTool Execution Results: {tests_passed}/5 passed\n")
        
        # Test error handling
        print("Testing ERROR HANDLING...")
        req = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}}
        }
        server.stdin.write(json.dumps(req) + "\n")
        server.stdin.flush()
        resp = json.loads(server.stdout.readline())
        
        # Check if response indicates error (either JSON-RPC error or result.success=false)
        if ("error" in resp and resp["error"]) or \
           ("result" in resp and resp["result"] and resp["result"].get("success") == False):
            print("[PASS] Error handling works correctly")
        else:
            print("[FAIL] Error handling failed")
            tests_failed += 1
        
        print("\n" + "="*70)
        
        if tests_failed == 0:
            print("[SUCCESS] ALL TESTS PASSED - Server is ready for production")
            print("="*70)
            return True
        else:
            print(f"[FAILED] {tests_failed} test(s) failed")
            print("="*70)
            return False
    
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        print("\nServer stopped")


if __name__ == "__main__":
    success = validate_server()
    sys.exit(0 if success else 1)
