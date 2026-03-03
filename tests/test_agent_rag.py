"""Tests for Agent and RAG related model metadata and discoverability."""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)



def _contains_any_keyword(model: dict, keywords: tuple[str, ...]) -> bool:
    searchable_parts = []
    searchable_parts.extend(model.get("use_cases", []))
    searchable_parts.extend(model.get("strengths", []))
    best_for = model.get("best_for")
    if best_for:
        searchable_parts.append(best_for)

    searchable_text = " ".join(searchable_parts).lower()
    return any(keyword in searchable_text for keyword in keywords)



def test_use_cases_contains_rag_models():
    """At least one model should explicitly support RAG/retrieval use cases."""
    response = client.get("/use-cases")
    assert response.status_code == 200

    data = response.json()
    rag_keywords = ("rag", "retrieval", "retrieval-augmented")

    rag_models = [
        model for model in data["models"]
        if _contains_any_keyword(model, rag_keywords)
    ]

    assert len(rag_models) > 0, "Expected at least one RAG-capable model"



def test_use_cases_contains_agent_models():
    """At least one model should include agent/agentic workflow support."""
    response = client.get("/use-cases")
    assert response.status_code == 200

    data = response.json()
    agent_keywords = ("agent", "agentic", "tool use")

    agent_models = [
        model for model in data["models"]
        if _contains_any_keyword(model, agent_keywords)
    ]

    assert len(agent_models) > 0, "Expected at least one agent-capable model"



def test_rag_and_agent_models_have_pricing_entries():
    """Models found via use-cases should also exist in /pricing results."""
    use_case_response = client.get("/use-cases")
    pricing_response = client.get("/pricing")

    assert use_case_response.status_code == 200
    assert pricing_response.status_code == 200

    use_case_models = use_case_response.json()["models"]
    pricing_models = pricing_response.json()["models"]

    pricing_names = {model["model_name"] for model in pricing_models}
    target_keywords = ("rag", "retrieval", "agent", "agentic", "tool use")

    target_use_case_models = [
        model for model in use_case_models
        if _contains_any_keyword(model, target_keywords)
    ]

    assert len(target_use_case_models) > 0, "Expected target models for Agent/RAG"

    missing_from_pricing = [
        model["model_name"] for model in target_use_case_models
        if model["model_name"] not in pricing_names
    ]

    assert missing_from_pricing == [], (
        "Use-case models missing from pricing endpoint: "
        f"{missing_from_pricing}"
    )



def test_agent_rag_candidates_include_context_window_info():
    """Agent/RAG candidate models should expose context_window for planning/retrieval."""
    response = client.get("/use-cases")
    assert response.status_code == 200

    data = response.json()
    target_keywords = ("rag", "retrieval", "agent", "agentic")

    target_models = [
        model for model in data["models"]
        if _contains_any_keyword(model, target_keywords)
    ]

    assert len(target_models) > 0, "Expected Agent/RAG candidate models"

    assert all(
        isinstance(model.get("context_window"), int) and model["context_window"] > 0
        for model in target_models
    ), "All Agent/RAG candidate models should have a positive integer context_window"
