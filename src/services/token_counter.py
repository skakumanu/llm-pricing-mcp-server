"""Token counting utilities using tiktoken."""
import logging


logger = logging.getLogger(__name__)

# cl100k_base is GPT-4's encoding and a close approximation for all major
# frontier models (Anthropic, Gemini, Mistral). Typical error is <10%.
_ENCODING_NAME = "cl100k_base"

# Prompt cache read multipliers by provider (fraction of full input cost)
CACHE_READ_MULTIPLIERS = {
    "Anthropic": 0.10,   # Claude: cache reads at 10% of input price
    "OpenAI": 0.50,      # GPT-4o+: cache reads at 50% of input price
    "Google": 0.25,      # Gemini: context caching at ~25% of input price
}


def count_tokens(text: str) -> int:
    """Count tokens in text using the cl100k_base encoding."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding(_ENCODING_NAME)
        return len(enc.encode(text))
    except Exception as e:
        logger.warning("tiktoken unavailable, falling back to word-count estimate: %s", e)
        return max(1, len(text.split()) * 4 // 3)


def compute_cache_savings(
    provider: str,
    input_tokens: int,
    cost_per_input_token: float,
    cache_hit_ratio: float,
) -> float:
    """Return USD savings from prompt caching for one call."""
    if cache_hit_ratio <= 0:
        return 0.0
    multiplier = CACHE_READ_MULTIPLIERS.get(provider)
    if multiplier is None:
        return 0.0
    # Savings = cached_fraction × tokens × (1 - multiplier) × rate
    per_token_rate = cost_per_input_token / 1000
    savings = cache_hit_ratio * input_tokens * (1.0 - multiplier) * per_token_rate
    return round(savings, 8)


def providers_with_caching() -> list[str]:
    """Return provider names that support prompt caching."""
    return list(CACHE_READ_MULTIPLIERS.keys())
