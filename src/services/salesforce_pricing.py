"""Service for retrieving Salesforce AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class SalesforcePricingService(BasePricingProvider):
    """Service to fetch and manage Salesforce AI model pricing.

    Salesforce offers two distinct pricing models:
    - Commercial products (Agentforce, Einstein): billed per conversation or via
      Flex Credits / Einstein Requests bundled with Salesforce licenses.
    - Open-source research models (xLAM, xGen, CodeGen): Salesforce-authored models
      released on HuggingFace, self-hosted or accessed via third-party inference
      providers (Together AI, Fireworks AI, etc.).

    Per-token values for commercial models are estimates derived from Salesforce's
    published per-conversation rates ($2/conversation for Agentforce) and are
    labelled accordingly.
    """

    STATIC_PRICING = {
        # ── Commercial products ──────────────────────────────────────────────
        "agentforce": {
            # Salesforce Agentforce: $2.00 per autonomous agent conversation.
            # Estimate assumes ~5 000 input tokens + ~1 500 output tokens / conversation.
            "input": 0.40,
            "output": 1.33,
            "context_window": 128000,
            "use_cases": [
                "Autonomous CRM agents", "Customer service resolution",
                "Sales pipeline automation", "Case deflection",
                "Order management", "Field service orchestration",
            ],
            "strengths": [
                "Native Salesforce data access", "Einstein Trust Layer",
                "Pre-built CRM actions", "No-code agent builder",
                "Flows and Apex integration",
            ],
            "best_for": (
                "Autonomous end-to-end CRM task resolution within Salesforce orgs. "
                "Priced at $2/conversation, not per-token — values above are estimates."
            ),
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "einstein-copilot": {
            # Einstein Copilot: bundled with Einstein AI add-on ($50–75/user/month).
            # Approximate token cost derived from reported 1 000–5 000 Einstein Requests
            # included per license at ~$0.025/request.
            "input": 0.10,
            "output": 0.30,
            "context_window": 32768,
            "use_cases": [
                "Conversational CRM guidance", "Opportunity summaries",
                "Email drafting", "Knowledge article suggestions",
                "Pipeline insights", "Meeting prep briefs",
            ],
            "strengths": [
                "Embedded in Salesforce UI", "CRM context-aware",
                "Einstein Trust Layer", "Zero-copy data privacy",
                "Works across Sales/Service/Marketing Cloud",
            ],
            "best_for": (
                "In-app conversational AI assistant for Salesforce users. "
                "Billed via Einstein Requests bundled with licenses, not per-token."
            ),
            "supports_function_calling": True,
        },
        # ── Open-source / research models (Salesforce Research on HuggingFace) ─
        "xlam-2-70b-fc-r": {
            # xLAM-2 70B: Salesforce Large Action Model for agentic function calling.
            # Priced via third-party inference (Together AI / Fireworks AI).
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 131072,
            "use_cases": [
                "Complex multi-step agentic workflows", "API orchestration",
                "Tool use and function calling", "Multi-turn agent planning",
                "Enterprise process automation",
            ],
            "strengths": [
                "State-of-the-art function calling", "128K context",
                "Strong multi-step reasoning", "Designed for agent frameworks",
                "Apache-2.0 open source",
            ],
            "best_for": (
                "High-accuracy agentic pipelines requiring reliable tool selection "
                "and multi-step planning. Self-host or use via Together/Fireworks AI."
            ),
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "xlam-2-8b-fc-r": {
            # xLAM-2 8B: efficient Salesforce action model for function calling.
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 131072,
            "use_cases": [
                "Lightweight agentic tasks", "API call generation",
                "Tool selection", "Edge agent deployment",
                "Cost-sensitive agentic workflows",
            ],
            "strengths": [
                "Efficient 8B footprint", "Competitive function-calling accuracy",
                "128K context", "Apache-2.0 open source",
                "Fast inference on consumer hardware",
            ],
            "best_for": (
                "Cost-efficient agentic function calling where the 70B is overkill. "
                "Runs on a single A100 or via cheap third-party inference."
            ),
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "xgen-7b-8k-instruct": {
            # xGen-7B: Salesforce Research instruction model, released on HuggingFace.
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": [
                "Instruction following", "Summarization",
                "General Q&A", "Developer workflows", "Code generation",
            ],
            "strengths": [
                "Apache-2.0 open source", "8K context window",
                "Strong instruction following for 7B scale",
            ],
            "best_for": (
                "General instruction-following tasks where a lightweight open-source "
                "model suffices. Available on HuggingFace (Salesforce/xgen-7b-8k-instruct)."
            ),
        },
        "codegen25-7b-instruct": {
            # CodeGen 2.5 7B: Salesforce Research code generation model.
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": [
                "Code generation", "Code completion", "Apex/SOQL generation",
                "Multi-language coding", "Technical documentation",
            ],
            "strengths": [
                "Specialised for code", "Multi-language support",
                "Salesforce CodeGen research lineage", "Apache-2.0 open source",
            ],
            "best_for": (
                "Code generation and completion built on Salesforce's CodeGen "
                "research. Available on HuggingFace (Salesforce/codegen25-7b-instruct)."
            ),
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Salesforce AI pricing service."""
        super().__init__("Salesforce")
        self.api_key = api_key or getattr(settings, 'salesforce_api_key', None)

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """Return Salesforce AI model pricing (static — no public token-price API)."""
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning("Error fetching Salesforce pricing data: %s", e)
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Build PricingMetrics list from static data."""
        pricing_list = []
        for model_name, info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Salesforce",
                    cost_per_input_token=info["input"] / 1000,
                    cost_per_output_token=info["output"] / 1000,
                    context_window=info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Salesforce AI Pricing (Static — see best_for for billing model)",
                    throughput=70.0,
                    latency_ms=700.0,
                    use_cases=info.get("use_cases", []),
                    strengths=info.get("strengths", []),
                    best_for=info.get("best_for", ""),
                    supports_vision=info.get("supports_vision", False),
                    supports_function_calling=info.get("supports_function_calling", False),
                    supports_json_mode=info.get("supports_json_mode", False),
                    batch_available=info.get("batch_available", False),
                    is_reasoning_model=info.get("is_reasoning_model", False),
                )
            )
        return pricing_list

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return SalesforcePricingService()._get_static_pricing_data()
