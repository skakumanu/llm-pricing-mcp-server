"""Generate JSON schemas from Pydantic models for MCP tools."""
import json
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.pricing import (
    PricingMetrics, PricingResponse, CostEstimateRequest, CostEstimateResponse,
    BatchCostEstimateRequest, BatchCostEstimateResponse, PerformanceMetrics,
    PerformanceResponse, ModelUseCase, UseCaseResponse, ProviderStatusInfo,
    TokenVolumePrice, ModelCostComparison, EndpointInfo, ServerInfo
)


def generate_schemas():
    """Generate JSON schemas for all models and save to schemas folder."""
    schemas_dir = Path(__file__).parent / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    
    # Define models to export with their schema file names
    models_to_export = {
        "pricing_metrics.json": PricingMetrics,
        "pricing_response.json": PricingResponse,
        "cost_estimate_request.json": CostEstimateRequest,
        "cost_estimate_response.json": CostEstimateResponse,
        "batch_cost_estimate_request.json": BatchCostEstimateRequest,
        "batch_cost_estimate_response.json": BatchCostEstimateResponse,
        "performance_metrics.json": PerformanceMetrics,
        "performance_response.json": PerformanceResponse,
        "model_use_case.json": ModelUseCase,
        "use_case_response.json": UseCaseResponse,
        "provider_status_info.json": ProviderStatusInfo,
        "token_volume_price.json": TokenVolumePrice,
        "model_cost_comparison.json": ModelCostComparison,
        "endpoint_info.json": EndpointInfo,
        "server_info.json": ServerInfo,
    }
    
    for filename, model_cls in models_to_export.items():
        schema = model_cls.model_json_schema()
        
        # Ensure the schema is serializable by converting datetime references
        # Remove the $defs key as it's not needed and replace with simplified versions
        if "$defs" in schema:
            del schema["$defs"]
        
        # Save the schema
        schema_path = schemas_dir / filename
        with open(schema_path, "w") as f:
            json.dump(schema, f, indent=2)
        
        print(f"✓ Generated {filename}")
    
    print(f"\nSchemas generated in {schemas_dir}")


if __name__ == "__main__":
    generate_schemas()
