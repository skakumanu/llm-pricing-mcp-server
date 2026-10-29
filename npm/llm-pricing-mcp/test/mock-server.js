'use strict';
// Minimal stand-in for src/main.py's POST /mcp handler, used only to
// exercise bin/llm-pricing-mcp.js's bridging logic in CI/local dev without
// hitting the real hosted server. Not published (outside the "files" allowlist).
const http = require('http');

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', (chunk) => (body += chunk));
  req.on('end', () => {
    let msg;
    try {
      msg = JSON.parse(body);
    } catch {
      res.writeHead(400, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ jsonrpc: '2.0', error: { code: -32700, message: 'Parse error' } }));
      return;
    }

    if (msg.method === 'notifications/initialized') {
      res.writeHead(204);
      res.end();
      return;
    }

    if (msg.method === 'initialize') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(
        JSON.stringify({
          jsonrpc: '2.0',
          id: msg.id,
          result: { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'mock', version: '0.0.0' } },
        })
      );
      return;
    }

    if (msg.method === 'tools/list') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { tools: [{ name: 'estimate_cost' }, { name: 'recommend_model' }] } }));
      return;
    }

    // Test-only: echo whether the bridge sent x-api-key (and its value) so the
    // Python tests can assert the header is forwarded only when configured.
    if (msg.method === 'echo_headers') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(
        JSON.stringify({
          jsonrpc: '2.0',
          id: msg.id,
          result: {
            has_api_key: Object.prototype.hasOwnProperty.call(req.headers, 'x-api-key'),
            api_key: req.headers['x-api-key'] || null,
          },
        })
      );
      return;
    }

    if (msg.method === 'slow_call') {
      setTimeout(() => {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { slept: true } }));
      }, 300);
      return;
    }

    res.writeHead(404, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Method not found' } }));
  });
});

const port = process.env.PORT || 0;
server.listen(port, '127.0.0.1', () => {
  console.error(`mock server listening on ${server.address().port}`);
});
