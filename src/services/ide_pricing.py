"""Pricing data for subscription-based AI coding IDEs and tools.

Covers GitHub Copilot, Cursor, Windsurf (Codeium), Claude Code / Claude.ai,
JetBrains AI, Amazon Q Developer, and Tabnine.

Per-token costs are *derived estimates* based on each plan's monthly subscription
divided by a representative monthly token budget for that tier. They exist only so
these tools can be compared on the same scale as API-based models; the canonical
cost signal for these tools is ``subscription_monthly_usd``.

Sources (all public pricing pages, June 2026):
- GitHub Copilot: docs.github.com/en/copilot/about-github-copilot/subscription-plans
- Cursor: cursor.com/pricing
- Windsurf: codeium.com/pricing
- Claude Code: claude.ai/pricing (Claude Max / Team)
- JetBrains AI: jetbrains.com/ai/ (AI Pro, AI Ultimate)
- Amazon Q Developer: aws.amazon.com/q/developer/pricing/
- Tabnine: tabnine.com/pricing
"""
from typing import List
import logging

from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)

# Monthly subscription / estimated monthly tokens → per-1k-token rate.
# Estimates assume solo-developer usage; heavy users pay proportionally less.
_IDE_TOOLS: List[dict] = [
    # ── GitHub Copilot ──────────────────────────────────────────────────────
    {
        "model_name": "copilot-individual",
        "provider": "GitHub Copilot",
        "subscription_monthly_usd": 10.0,
        # ~10 M completions-tokens/mo at individual tier
        "input": 0.0005,
        "output": 0.0010,
        "context_window": 8000,
        "latency_ms": 250.0,
        "use_cases": [
            "Inline code completion", "Code suggestions", "Chat in IDE",
            "Code explanation", "Bug fixes",
        ],
        "strengths": [
            "Deep GitHub integration", "Fast inline completions",
            "Multi-language support", "VS Code / JetBrains / Neovim",
        ],
        "best_for": "Inline completions for developers already on GitHub",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "copilot-business",
        "provider": "GitHub Copilot",
        "subscription_monthly_usd": 19.0,
        "input": 0.0005,
        "output": 0.0010,
        "context_window": 8000,
        "latency_ms": 250.0,
        "use_cases": [
            "Inline code completion", "Team-wide policy controls",
            "Code suggestions", "Chat in IDE", "PR summaries",
        ],
        "strengths": [
            "Team seat management", "IP indemnity", "Audit logs",
            "Deep GitHub integration", "Fast completions",
        ],
        "best_for": "Teams wanting admin controls and IP indemnity on top of Copilot",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "copilot-enterprise",
        "provider": "GitHub Copilot",
        "subscription_monthly_usd": 39.0,
        "input": 0.0004,
        "output": 0.0008,
        "context_window": 32000,
        "latency_ms": 300.0,
        "use_cases": [
            "Inline code completion", "Codebase-aware chat",
            "PR reviews", "Custom models", "Enterprise security",
        ],
        "strengths": [
            "Codebase indexing", "Custom fine-tuning", "Enterprise SSO",
            "Docset knowledge", "Fine-grained policy controls",
        ],
        "best_for": "Large organisations needing codebase-aware assistance and enterprise controls",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    # ── Cursor ───────────────────────────────────────────────────────────────
    {
        "model_name": "cursor-hobby",
        "provider": "Cursor",
        "subscription_monthly_usd": 0.0,
        # Free tier: limited fast requests; estimate based on ~2M tokens/mo
        "input": 0.0010,
        "output": 0.0020,
        "context_window": 128000,
        "latency_ms": 400.0,
        "use_cases": [
            "AI-powered code editing", "Inline completions", "Code chat",
            "Codebase Q&A", "Refactoring",
        ],
        "strengths": [
            "Powerful codebase context", "Multiple frontier models",
            "Composer (multi-file edits)", "Free tier available",
        ],
        "best_for": "Individual developers wanting a free AI-first coding environment",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": True,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "cursor-pro",
        "provider": "Cursor",
        "subscription_monthly_usd": 20.0,
        # Unlimited fast requests; ~50M tokens/mo heavy use
        "input": 0.0002,
        "output": 0.0004,
        "context_window": 128000,
        "latency_ms": 350.0,
        "use_cases": [
            "AI-powered code editing", "Unlimited fast completions",
            "Composer (multi-file edits)", "Code chat", "Refactoring",
        ],
        "strengths": [
            "Unlimited requests", "Access to GPT-4o/Claude/Gemini",
            "Composer for large refactors", "Codebase indexing",
        ],
        "best_for": "Professional developers wanting unlimited AI assistance across frontier models",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": True,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "cursor-business",
        "provider": "Cursor",
        "subscription_monthly_usd": 40.0,
        "input": 0.0002,
        "output": 0.0004,
        "context_window": 128000,
        "latency_ms": 350.0,
        "use_cases": [
            "AI-powered code editing", "Team seat management",
            "Centralized billing", "Audit logs", "Privacy controls",
        ],
        "strengths": [
            "Team billing", "Privacy mode", "All Pro features",
            "Centralized admin",
        ],
        "best_for": "Teams needing centralized billing and privacy controls on top of Cursor Pro",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": True,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    # ── Windsurf (Codeium) ───────────────────────────────────────────────────
    {
        "model_name": "windsurf-free",
        "provider": "Windsurf",
        "subscription_monthly_usd": 0.0,
        "input": 0.0008,
        "output": 0.0016,
        "context_window": 128000,
        "latency_ms": 380.0,
        "use_cases": [
            "Inline code completion", "AI chat", "Code explanation",
            "Refactoring", "Bug fixes",
        ],
        "strengths": [
            "Free unlimited completions", "Cascade (agentic coding)",
            "Multi-IDE support", "Fast completions",
        ],
        "best_for": "Developers wanting free unlimited completions with agentic coding features",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "windsurf-pro",
        "provider": "Windsurf",
        "subscription_monthly_usd": 15.0,
        "input": 0.0002,
        "output": 0.0004,
        "context_window": 128000,
        "latency_ms": 350.0,
        "use_cases": [
            "Inline code completion", "Cascade (agentic coding)",
            "Advanced AI chat", "Code generation", "Unlimited fast requests",
        ],
        "strengths": [
            "SWE-1 model", "Unlimited flow actions",
            "Priority access", "Cascade agentic mode",
        ],
        "best_for": "Individual developers wanting fast agentic coding with Windsurf's Cascade",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "windsurf-teams",
        "provider": "Windsurf",
        "subscription_monthly_usd": 35.0,
        "input": 0.0002,
        "output": 0.0004,
        "context_window": 128000,
        "latency_ms": 350.0,
        "use_cases": [
            "Team-wide AI coding", "Centralized billing",
            "Cascade agentic mode", "Admin controls",
        ],
        "strengths": [
            "Team management", "All Pro features",
            "Centralized billing", "Priority support",
        ],
        "best_for": "Teams wanting Windsurf Pro with admin controls and centralized billing",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    # ── Claude (Anthropic) ───────────────────────────────────────────────────
    {
        "model_name": "claude-ai-pro",
        "provider": "Anthropic",
        "subscription_monthly_usd": 20.0,
        # ~5x more usage than free tier; ~10M tokens/mo
        "input": 0.0010,
        "output": 0.0020,
        "context_window": 200000,
        "latency_ms": 800.0,
        "use_cases": [
            "Advanced reasoning", "Long-document analysis",
            "Code review", "Research", "Writing",
        ],
        "strengths": [
            "200k context window", "Projects and memory",
            "Advanced code understanding", "Extended thinking",
        ],
        "best_for": "Individuals wanting Claude's full capabilities via chat interface",
        "supports_inline_completion": False,
        "supports_function_calling": True,
        "supports_json_mode": True,
        "supports_vision": True,
        "is_reasoning_model": False,
        "ide_native": False,
        "pricing_model": "subscription",
    },
    {
        "model_name": "claude-ai-max-5x",
        "provider": "Anthropic",
        "subscription_monthly_usd": 100.0,
        # ~50M tokens/mo heavy usage
        "input": 0.0010,
        "output": 0.0020,
        "context_window": 200000,
        "latency_ms": 800.0,
        "use_cases": [
            "High-volume reasoning", "Agentic tasks",
            "Complex code generation", "Research at scale",
        ],
        "strengths": [
            "5x usage limits vs Pro", "200k context",
            "Extended thinking", "Priority access",
        ],
        "best_for": "Power users needing significantly more Claude capacity than Pro",
        "supports_inline_completion": False,
        "supports_function_calling": True,
        "supports_json_mode": True,
        "supports_vision": True,
        "is_reasoning_model": True,
        "ide_native": False,
        "pricing_model": "subscription",
    },
    # ── JetBrains AI ─────────────────────────────────────────────────────────
    {
        "model_name": "jetbrains-ai-pro",
        "provider": "JetBrains AI",
        "subscription_monthly_usd": 8.33,
        # ~5M tokens/mo
        "input": 0.0008,
        "output": 0.0017,
        "context_window": 32000,
        "latency_ms": 400.0,
        "use_cases": [
            "Inline code completion", "AI chat in JetBrains IDEs",
            "Code explanation", "Test generation", "Refactoring",
        ],
        "strengths": [
            "Deep JetBrains IDE integration", "Fast completions",
            "Cloud + local model support", "Multi-language",
        ],
        "best_for": "JetBrains IDE users (IntelliJ, PyCharm, WebStorm, etc.) wanting native AI",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "jetbrains-ai-ultimate",
        "provider": "JetBrains AI",
        "subscription_monthly_usd": 16.67,
        "input": 0.0006,
        "output": 0.0013,
        "context_window": 128000,
        "latency_ms": 380.0,
        "use_cases": [
            "Inline code completion", "Extended chat", "Test generation",
            "Code review", "Documentation", "Refactoring",
        ],
        "strengths": [
            "Higher quota than AI Pro", "All JetBrains IDE integrations",
            "Access to frontier models", "Full AI toolkit",
        ],
        "best_for": "JetBrains users needing higher AI quotas and frontier model access",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    # ── Amazon Q Developer ───────────────────────────────────────────────────
    {
        "model_name": "amazon-q-developer-free",
        "provider": "Amazon Q Developer",
        "subscription_monthly_usd": 0.0,
        "input": 0.0008,
        "output": 0.0015,
        "context_window": 32000,
        "latency_ms": 450.0,
        "use_cases": [
            "Inline code completion", "Chat in IDE",
            "AWS resource Q&A", "Code explanation", "Security scanning",
        ],
        "strengths": [
            "Free tier", "Deep AWS integration", "Security vulnerability detection",
            "Multi-language completions",
        ],
        "best_for": "AWS-centric developers wanting free AI completions and AWS-aware chat",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "amazon-q-developer-pro",
        "provider": "Amazon Q Developer",
        "subscription_monthly_usd": 19.0,
        "input": 0.0004,
        "output": 0.0008,
        "context_window": 128000,
        "latency_ms": 400.0,
        "use_cases": [
            "Unlimited inline completions", "Code transformation",
            "Security remediation", "AWS architecture Q&A",
            "Software agent tasks",
        ],
        "strengths": [
            "Unlimited completions", "Code transformation (Java upgrades)",
            "Admin controls", "Deep AWS knowledge", "Security scanning",
        ],
        "best_for": "AWS teams needing unlimited completions, admin controls, and code modernisation",
        "supports_inline_completion": True,
        "supports_function_calling": True,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    # ── Tabnine ──────────────────────────────────────────────────────────────
    {
        "model_name": "tabnine-starter",
        "provider": "Tabnine",
        "subscription_monthly_usd": 0.0,
        "input": 0.0010,
        "output": 0.0020,
        "context_window": 4000,
        "latency_ms": 200.0,
        "use_cases": [
            "Inline code completion", "Basic AI chat",
            "Code explanation",
        ],
        "strengths": [
            "Very fast completions", "Runs locally (privacy)",
            "Free tier", "Wide IDE support",
        ],
        "best_for": "Privacy-conscious developers wanting fast free completions with a local model option",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
    {
        "model_name": "tabnine-dev",
        "provider": "Tabnine",
        "subscription_monthly_usd": 9.0,
        "input": 0.0005,
        "output": 0.0010,
        "context_window": 8000,
        "latency_ms": 200.0,
        "use_cases": [
            "Inline code completion", "AI chat", "Whole-file completion",
            "Code explanation", "Test generation",
        ],
        "strengths": [
            "Privacy-first (no code leaves machine option)", "Fast completions",
            "Wide IDE support", "Whole-file completion",
        ],
        "best_for": "Individual developers wanting fast privacy-preserving completions",
        "supports_inline_completion": True,
        "supports_function_calling": False,
        "supports_json_mode": False,
        "supports_vision": False,
        "is_reasoning_model": False,
        "ide_native": True,
        "pricing_model": "subscription",
    },
]


class IDEPricingService(BasePricingProvider):
    """Static pricing data for subscription-based AI coding IDE tools."""

    def __init__(self):
        super().__init__("IDETools")

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        results = []
        for tool in _IDE_TOOLS:
            try:
                results.append(PricingMetrics(
                    model_name=tool["model_name"],
                    provider=tool["provider"],
                    cost_per_input_token=tool["input"],
                    cost_per_output_token=tool["output"],
                    context_window=tool.get("context_window"),
                    latency_ms=tool.get("latency_ms"),
                    use_cases=tool.get("use_cases"),
                    strengths=tool.get("strengths"),
                    best_for=tool.get("best_for"),
                    supports_vision=tool.get("supports_vision", False),
                    supports_function_calling=tool.get("supports_function_calling", False),
                    supports_json_mode=tool.get("supports_json_mode", False),
                    batch_available=False,
                    is_reasoning_model=tool.get("is_reasoning_model", False),
                    pricing_model=tool.get("pricing_model", "subscription"),
                    subscription_monthly_usd=tool.get("subscription_monthly_usd"),
                    supports_inline_completion=tool.get("supports_inline_completion", False),
                    ide_native=tool.get("ide_native", False),
                    source="public_pricing_page",
                ))
            except Exception as exc:
                logger.warning("IDEPricingService: skipped %s — %s", tool.get("model_name"), exc)
        return results
