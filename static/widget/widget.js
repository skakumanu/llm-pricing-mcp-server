/**
 * LLM Pricing Widget — v1.27.0
 *
 * Drop this script into any HTML page to embed a live LLM pricing table.
 *
 * Usage:
 *   <div data-llm-pricing
 *        data-models="gpt-4o,claude-sonnet-4-6"
 *        data-theme="dark"
 *        data-layout="table"
 *        data-refresh="300">
 *   </div>
 *   <script src="https://your-server/widget/widget.js"></script>
 *
 * Attributes (all optional):
 *   data-models    Comma-separated model names. Omit to show all models.
 *   data-theme     "dark" (default) or "light"
 *   data-layout    "table" (default) or "compact"
 *   data-refresh   Seconds between auto-refresh. 0 = disabled. Default: 300.
 *   data-server    Override the server base URL. Auto-detected from script src.
 */
(function () {
  "use strict";

  // ── Detect server base URL from script src ─────────────────────────────
  function detectServer() {
    var scripts = document.querySelectorAll("script[src]");
    for (var i = 0; i < scripts.length; i++) {
      var src = scripts[i].src || "";
      if (src.indexOf("widget.js") !== -1) {
        try {
          var u = new URL(src);
          return u.origin;
        } catch (_) {}
      }
    }
    return window.location.origin;
  }

  var DEFAULT_SERVER = detectServer();

  // ── Styles ─────────────────────────────────────────────────────────────
  var DARK_VARS = [
    "--llmw-bg:#1a1d27",
    "--llmw-surface:#22263a",
    "--llmw-border:#2e3347",
    "--llmw-text:#e2e8f0",
    "--llmw-muted:#8892a4",
    "--llmw-accent:#7c6af7",
    "--llmw-accent2:#4ecdc4",
    "--llmw-success:#48bb78",
    "--llmw-head-bg:#181b28",
  ].join(";");

  var LIGHT_VARS = [
    "--llmw-bg:#ffffff",
    "--llmw-surface:#f8fafc",
    "--llmw-border:#e2e8f0",
    "--llmw-text:#1e293b",
    "--llmw-muted:#64748b",
    "--llmw-accent:#6366f1",
    "--llmw-accent2:#0d9488",
    "--llmw-success:#16a34a",
    "--llmw-head-bg:#f1f5f9",
  ].join(";");

  var BASE_CSS = [
    ".llmw-wrap{font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;border:1px solid var(--llmw-border);border-radius:10px;overflow:hidden;background:var(--llmw-bg);color:var(--llmw-text);}",
    ".llmw-header{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:var(--llmw-head-bg);border-bottom:1px solid var(--llmw-border);}",
    ".llmw-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--llmw-muted);}",
    ".llmw-updated{font-size:11px;color:var(--llmw-muted);}",
    ".llmw-table{width:100%;border-collapse:collapse;}",
    ".llmw-table th{background:var(--llmw-head-bg);text-align:left;padding:7px 12px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--llmw-muted);border-bottom:1px solid var(--llmw-border);}",
    ".llmw-table td{padding:8px 12px;border-bottom:1px solid var(--llmw-border);color:var(--llmw-text);}",
    ".llmw-table tr:last-child td{border-bottom:none;}",
    ".llmw-table tr:hover td{background:var(--llmw-surface);}",
    ".llmw-model{font-weight:600;}",
    ".llmw-provider{font-size:11px;color:var(--llmw-muted);}",
    ".llmw-price{font-family:monospace;font-weight:600;}",
    ".llmw-ctx{font-size:11px;color:var(--llmw-muted);}",
    ".llmw-best{color:var(--llmw-success);}",
    ".llmw-badge{display:inline-block;background:rgba(72,187,120,.15);color:var(--llmw-success);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:1px 5px;border-radius:3px;margin-left:5px;vertical-align:middle;}",
    // Compact layout
    ".llmw-compact{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;padding:12px;}",
    ".llmw-compact-card{background:var(--llmw-surface);border:1px solid var(--llmw-border);border-radius:8px;padding:10px 12px;}",
    ".llmw-compact-model{font-size:13px;font-weight:700;margin-bottom:2px;}",
    ".llmw-compact-provider{font-size:10px;color:var(--llmw-muted);margin-bottom:8px;}",
    ".llmw-compact-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;}",
    ".llmw-compact-label{font-size:11px;color:var(--llmw-muted);}",
    ".llmw-compact-val{font-family:monospace;font-size:12px;font-weight:600;}",
    // Footer
    ".llmw-footer{padding:6px 14px;font-size:10px;color:var(--llmw-muted);text-align:right;border-top:1px solid var(--llmw-border);}",
    ".llmw-footer a{color:var(--llmw-accent);text-decoration:none;}",
    ".llmw-footer a:hover{text-decoration:underline;}",
    ".llmw-error{padding:16px;color:#f87171;font-size:13px;text-align:center;}",
    ".llmw-spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--llmw-border);border-top-color:var(--llmw-accent);border-radius:50%;animation:llmw-spin .7s linear infinite;vertical-align:middle;margin-right:6px;}",
    "@keyframes llmw-spin{to{transform:rotate(360deg)}}",
  ].join("");

  // Inject styles once
  var _stylesInjected = false;
  function ensureStyles() {
    if (_stylesInjected) return;
    var style = document.createElement("style");
    style.textContent = BASE_CSS;
    document.head.appendChild(style);
    _stylesInjected = true;
  }

  // ── Helpers ────────────────────────────────────────────────────────────
  function fmtPrice(usd) {
    if (usd === 0) return "$0.00";
    if (usd < 0.001) return "$" + usd.toFixed(6);
    if (usd < 1) return "$" + usd.toFixed(4);
    return "$" + usd.toFixed(2);
  }

  function fmtCtx(n) {
    if (!n) return "—";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(0) + "K";
    return String(n);
  }

  function relativeTime(isoStr) {
    try {
      var diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
      if (diff < 60) return "just now";
      if (diff < 3600) return Math.floor(diff / 60) + "m ago";
      return Math.floor(diff / 3600) + "h ago";
    } catch (_) {
      return "";
    }
  }

  // ── Fetch pricing data ─────────────────────────────────────────────────
  function fetchPricing(server, modelsParam, providerParam, callback) {
    var url = server + "/pricing/public";
    var params = [];
    if (modelsParam) params.push("models=" + encodeURIComponent(modelsParam));
    if (providerParam) params.push("provider=" + encodeURIComponent(providerParam));
    if (params.length) url += "?" + params.join("&");

    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status === 200) {
        try {
          callback(null, JSON.parse(xhr.responseText));
        } catch (e) {
          callback(e, null);
        }
      } else {
        callback(new Error("HTTP " + xhr.status), null);
      }
    };
    xhr.send();
  }

  // ── Render table layout ────────────────────────────────────────────────
  function renderTable(container, data, updatedAt) {
    var models = data.models || [];
    if (models.length === 0) {
      container.innerHTML = '<div class="llmw-error">No pricing data available.</div>';
      return;
    }

    var minInputPrice = Math.min.apply(null, models.map(function (m) { return m.input_per_1m_usd; }));

    var rows = models.map(function (m) {
      var isBest = m.input_per_1m_usd === minInputPrice;
      return [
        '<tr>',
        '<td>',
        '<div class="llmw-model">', escHtml(m.model_name),
        isBest ? '<span class="llmw-badge">Cheapest</span>' : "",
        '</div>',
        '<div class="llmw-provider">', escHtml(m.provider), '</div>',
        '</td>',
        '<td class="llmw-price', isBest ? ' llmw-best' : '', '">', fmtPrice(m.input_per_1m_usd), '</td>',
        '<td class="llmw-price">', fmtPrice(m.output_per_1m_usd), '</td>',
        '<td class="llmw-ctx">', fmtCtx(m.context_window), '</td>',
        '</tr>',
      ].join("");
    }).join("");

    container.innerHTML = [
      '<div class="llmw-header">',
      '<span class="llmw-title">LLM Pricing</span>',
      '<span class="llmw-updated">Updated ' + relativeTime(updatedAt) + '</span>',
      '</div>',
      '<table class="llmw-table">',
      '<thead><tr>',
      '<th>Model</th>',
      '<th>Input / 1M tokens</th>',
      '<th>Output / 1M tokens</th>',
      '<th>Context</th>',
      '</tr></thead>',
      '<tbody>', rows, '</tbody>',
      '</table>',
      '<div class="llmw-footer">Powered by <a href="' + escHtml(container._llmwServer) + '/compare" target="_blank">LLM Pricing</a></div>',
    ].join("");
  }

  // ── Render compact layout ──────────────────────────────────────────────
  function renderCompact(container, data, updatedAt) {
    var models = data.models || [];
    if (models.length === 0) {
      container.innerHTML = '<div class="llmw-error">No pricing data available.</div>';
      return;
    }

    var cards = models.map(function (m) {
      return [
        '<div class="llmw-compact-card">',
        '<div class="llmw-compact-model">', escHtml(m.model_name), '</div>',
        '<div class="llmw-compact-provider">', escHtml(m.provider), '</div>',
        '<div class="llmw-compact-row">',
        '<span class="llmw-compact-label">Input</span>',
        '<span class="llmw-compact-val">', fmtPrice(m.input_per_1m_usd), '/1M</span>',
        '</div>',
        '<div class="llmw-compact-row">',
        '<span class="llmw-compact-label">Output</span>',
        '<span class="llmw-compact-val">', fmtPrice(m.output_per_1m_usd), '/1M</span>',
        '</div>',
        m.context_window ? [
          '<div class="llmw-compact-row">',
          '<span class="llmw-compact-label">Context</span>',
          '<span class="llmw-compact-val">', fmtCtx(m.context_window), '</span>',
          '</div>',
        ].join("") : "",
        '</div>',
      ].join("");
    }).join("");

    container.innerHTML = [
      '<div class="llmw-header">',
      '<span class="llmw-title">LLM Pricing</span>',
      '<span class="llmw-updated">Updated ' + relativeTime(updatedAt) + '</span>',
      '</div>',
      '<div class="llmw-compact">', cards, '</div>',
      '<div class="llmw-footer">Powered by <a href="' + escHtml(container._llmwServer) + '/compare" target="_blank">LLM Pricing</a></div>',
    ].join("");
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Init a single widget element ───────────────────────────────────────
  function initWidget(el) {
    if (el._llmwInit) return;
    el._llmwInit = true;

    var modelsParam  = (el.getAttribute("data-models")   || "").trim();
    var providerParam = (el.getAttribute("data-provider") || "").trim();
    var theme        = (el.getAttribute("data-theme")    || "dark").toLowerCase();
    var layout       = (el.getAttribute("data-layout")   || "table").toLowerCase();
    var refresh      = parseInt(el.getAttribute("data-refresh") || "300", 10);
    var server       = (el.getAttribute("data-server")   || DEFAULT_SERVER).replace(/\/$/, "");

    el._llmwServer = server;

    // Apply CSS variables
    var vars = theme === "light" ? LIGHT_VARS : DARK_VARS;
    el.setAttribute("style", vars.split(";").map(function (v) { return v.trim(); }).join(";") + ";display:block;");
    el.className = (el.className ? el.className + " " : "") + "llmw-wrap";

    function load() {
      // Show spinner while loading (only on first load if empty)
      if (!el.innerHTML.trim()) {
        el.innerHTML = '<div style="padding:16px;text-align:center"><span class="llmw-spinner"></span>Loading pricing…</div>';
      }
      fetchPricing(server, modelsParam || null, providerParam || null, function (err, data) {
        if (err || !data) {
          el.innerHTML = '<div class="llmw-error">⚠ Could not load pricing data.</div>';
          return;
        }
        var updatedAt = data.updated_at || new Date().toISOString();
        if (layout === "compact") {
          renderCompact(el, data, updatedAt);
        } else {
          renderTable(el, data, updatedAt);
        }
      });
    }

    load();
    if (refresh > 0) {
      setInterval(load, refresh * 1000);
    }
  }

  // ── Bootstrap: find all [data-llm-pricing] elements ───────────────────
  function bootstrap() {
    ensureStyles();
    var elements = document.querySelectorAll("[data-llm-pricing]");
    for (var i = 0; i < elements.length; i++) {
      initWidget(elements[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
