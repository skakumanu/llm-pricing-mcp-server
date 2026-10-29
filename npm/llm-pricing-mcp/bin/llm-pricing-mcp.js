#!/usr/bin/env node
'use strict';

/**
 * Zero-dependency STDIO<->HTTP bridge for the LLM Pricing MCP server.
 *
 * Claude Desktop (and other STDIO-only MCP clients) spawn this process and
 * speak newline-delimited JSON-RPC 2.0 over stdin/stdout, per the MCP
 * transport spec. Each line is forwarded as-is to the server's HTTP MCP
 * endpoint (POST /mcp), and the JSON response (if any) is written back as a
 * line on stdout. No local Python, pip install, or clone required — the
 * actual tool implementations and pricing data live entirely server-side,
 * so this bridge has nothing to keep in sync with them.
 */

const readline = require('readline');

const ENDPOINT = process.env.LLM_PRICING_MCP_URL || 'https://llm-pricing-api.fly.dev/mcp';
const REQUEST_TIMEOUT_MS = Number(process.env.LLM_PRICING_MCP_TIMEOUT_MS) || 30000;
// Optional service API key (v1.1.0). Only the 7 org-scoped usage/budget tools need
// it; the pricing tools work without it. Forwarded as the x-api-key header and
// NEVER written to stdout, stderr or any error message.
const API_KEY = (process.env.LLM_PRICING_API_KEY || '').trim();
const REQUEST_HEADERS = {
  'content-type': 'application/json',
  ...(API_KEY ? { 'x-api-key': API_KEY } : {}),
};

function writeResponse(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function errorResponse(id, code, message) {
  return { jsonrpc: '2.0', id: id ?? null, error: { code, message } };
}

async function forward(line) {
  let request;
  try {
    request = JSON.parse(line);
  } catch (err) {
    writeResponse(errorResponse(null, -32700, `Parse error: ${err.message}`));
    return;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: REQUEST_HEADERS,
      body: JSON.stringify(request),
      signal: controller.signal,
    });

    // 204 No Content means the server treated this as a notification
    // (e.g. "initialized") — no reply is expected on the wire.
    if (res.status === 204) {
      return;
    }

    const text = await res.text();
    if (!res.ok) {
      writeResponse(
        errorResponse(request.id, -32000, `Upstream HTTP ${res.status}: ${text.slice(0, 500)}`)
      );
      return;
    }

    // Empty body with a 200 also means no reply expected.
    if (text.trim().length === 0) {
      return;
    }

    try {
      writeResponse(JSON.parse(text));
    } catch (err) {
      writeResponse(errorResponse(request.id, -32603, `Invalid JSON from upstream: ${err.message}`));
    }
  } catch (err) {
    const message = err.name === 'AbortError' ? 'Upstream request timed out' : err.message;
    writeResponse(errorResponse(request.id, -32001, message));
  } finally {
    clearTimeout(timeout);
  }
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
const pending = new Set();

rl.on('line', (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  // Each request is handled independently; a slow tools/call never blocks
  // other in-flight requests from being read and dispatched.
  const task = forward(trimmed)
    .catch((err) => {
      process.stderr.write(`llm-pricing-mcp: unexpected error: ${err.stack || err}\n`);
    })
    .finally(() => pending.delete(task));
  pending.add(task);
});

rl.on('close', () => {
  // Stdin closing (the host client exited) must not cut off replies that
  // are still in flight — wait for every dispatched request to settle
  // before this process exits, or the host loses the response race and
  // either hangs or sees a crashed server.
  Promise.allSettled(pending).then(() => process.exit(0));
});

process.on('SIGINT', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
