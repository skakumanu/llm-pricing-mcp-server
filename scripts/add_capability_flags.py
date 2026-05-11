"""Add capability flag fields to PricingMetrics constructor calls in all provider files."""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Each tuple: (old_string, new_string) - exact string replacements
REPLACEMENTS = [
    # Pattern A: 20-space indent, best_for with "" default, closing )
    (
        '                    best_for=pricing_info.get("best_for", "")\n                )\n',
        '                    best_for=pricing_info.get("best_for", ""),\n'
        '                    supports_vision=pricing_info.get("supports_vision", False),\n'
        '                    supports_function_calling=pricing_info.get("supports_function_calling", False),\n'
        '                    supports_json_mode=pricing_info.get("supports_json_mode", False),\n'
        '                    batch_available=pricing_info.get("batch_available", False),\n'
        '                    is_reasoning_model=pricing_info.get("is_reasoning_model", False),\n'
        '                )\n'
    ),
    # Pattern B: 20-space indent, best_for no default, closing )
    (
        '                    best_for=pricing_info.get("best_for")\n                )\n',
        '                    best_for=pricing_info.get("best_for"),\n'
        '                    supports_vision=pricing_info.get("supports_vision", False),\n'
        '                    supports_function_calling=pricing_info.get("supports_function_calling", False),\n'
        '                    supports_json_mode=pricing_info.get("supports_json_mode", False),\n'
        '                    batch_available=pricing_info.get("batch_available", False),\n'
        '                    is_reasoning_model=pricing_info.get("is_reasoning_model", False),\n'
        '                )\n'
    ),
    # Pattern C: 24-space indent, static_info, no default, closing )
    (
        '                        best_for=static_info.get("best_for")\n                    )\n',
        '                        best_for=static_info.get("best_for"),\n'
        '                        supports_vision=static_info.get("supports_vision", False),\n'
        '                        supports_function_calling=static_info.get("supports_function_calling", False),\n'
        '                        supports_json_mode=static_info.get("supports_json_mode", False),\n'
        '                        batch_available=static_info.get("batch_available", False),\n'
        '                        is_reasoning_model=static_info.get("is_reasoning_model", False),\n'
        '                    )\n'
    ),
    # Pattern D: 24-space indent, pricing_info, no default (Bedrock fetch_pricing_data)
    (
        '                        best_for=pricing_info.get("best_for")\n                    )\n',
        '                        best_for=pricing_info.get("best_for"),\n'
        '                        supports_vision=pricing_info.get("supports_vision", False),\n'
        '                        supports_function_calling=pricing_info.get("supports_function_calling", False),\n'
        '                        supports_json_mode=pricing_info.get("supports_json_mode", False),\n'
        '                        batch_available=pricing_info.get("batch_available", False),\n'
        '                        is_reasoning_model=pricing_info.get("is_reasoning_model", False),\n'
        '                    )\n'
    ),
    # Pattern E: Bedrock _get_static_pricing - 20-space, best_for no default, but ordering differs
    # The bedrock _get_static_pricing has best_for BEFORE source/throughput/latency
    # so we need a different pattern
    (
        '                    best_for=pricing_info.get("best_for"),\n'
        '                    source=f"{self.provider_name} Official Pricing (Fallback - Static)",\n',
        '                    best_for=pricing_info.get("best_for"),\n'
        '                    supports_vision=pricing_info.get("supports_vision", False),\n'
        '                    supports_function_calling=pricing_info.get("supports_function_calling", False),\n'
        '                    supports_json_mode=pricing_info.get("supports_json_mode", False),\n'
        '                    batch_available=pricing_info.get("batch_available", False),\n'
        '                    is_reasoning_model=pricing_info.get("is_reasoning_model", False),\n'
        '                    source=f"{self.provider_name} Official Pricing (Fallback - Static)",\n'
    ),
]

TARGET_FILES = [
    "src/services/groq_pricing.py",
    "src/services/mistral_pricing.py",
    "src/services/cohere_pricing.py",
    "src/services/together_pricing.py",
    "src/services/fireworks_pricing.py",
    "src/services/perplexity_pricing.py",
    "src/services/ai21_pricing.py",
    "src/services/cerebras_pricing.py",
    "src/services/nvidia_pricing.py",
    "src/services/replicate_pricing.py",
    "src/services/bedrock_pricing.py",
    "src/services/anyscale_pricing.py",
    "src/services/salesforce_pricing.py",
    "src/services/promptql_pricing.py",
    "src/services/snowflake_pricing.py",
    "src/services/oracle_pricing.py",
]

for rel_path in TARGET_FILES:
    filepath = os.path.join(BASE, rel_path)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    original = content
    for old, new in REPLACEMENTS:
        content = content.replace(old, new)

    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated: {rel_path}")
    else:
        print(f"No match found: {rel_path}")
