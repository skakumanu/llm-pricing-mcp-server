"""Task profiles mapping named task types to expected I/O token ratios."""
from typing import Dict, Optional, Tuple

# Each profile: output_fixed is an absolute token count; output_ratio multiplies input_tokens.
# Exactly one of the two is set per profile.
TASK_PROFILES: Dict[str, Dict] = {
    "classification": {
        "description": "Binary/multi-class label, sentiment, routing decision",
        "output_fixed": 15,
        "output_ratio": None,
    },
    "extraction": {
        "description": "Named-entity extraction, key-field parsing, structured data pull",
        "output_fixed": None,
        "output_ratio": 0.25,
    },
    "summarization": {
        "description": "Condense long text into a shorter form",
        "output_fixed": None,
        "output_ratio": 0.15,
    },
    "qa": {
        "description": "Factual or analytical question answering",
        "output_fixed": 180,
        "output_ratio": None,
    },
    "code_generation": {
        "description": "Write, complete, explain, or fix code",
        "output_fixed": 450,
        "output_ratio": None,
    },
    "translation": {
        "description": "Translate text between languages",
        "output_fixed": None,
        "output_ratio": 0.95,
    },
    "chat": {
        "description": "Conversational multi-turn dialogue",
        "output_fixed": 160,
        "output_ratio": None,
    },
    "content_generation": {
        "description": "Blog posts, emails, marketing copy, long-form writing",
        "output_fixed": 850,
        "output_ratio": None,
    },
    "reasoning": {
        "description": "Multi-step logical reasoning, math, chain-of-thought analysis",
        "output_fixed": 650,
        "output_ratio": None,
    },
    "function_calling": {
        "description": "Tool use, API call generation, structured function invocation",
        "output_fixed": 200,
        "output_ratio": None,
    },
    "data_analysis": {
        "description": "Interpret tables, charts, or numeric data; generate insights",
        "output_fixed": 350,
        "output_ratio": None,
    },
    "rewrite": {
        "description": "Rewrite or rephrase existing text at similar length",
        "output_fixed": None,
        "output_ratio": 1.0,
    },
}

# Keyword → task_type inference rules (checked in order; first match wins)
_INFERENCE_RULES: list[Tuple[list[str], str]] = [
    (["classify", "categorize", "label", "sentiment", "is it", "true or false", "yes or no"], "classification"),
    (["summarize", "summarise", "tldr", "tl;dr", "brief summary", "key points", "condense", "in short"], "summarization"),
    (["translate", " in french", " in spanish", " in german", " in chinese", " in japanese", " in arabic"], "translation"),
    (["rewrite", "rephrase", "paraphrase", "improve this text", "make it sound"], "rewrite"),
    (["extract", "parse", "find all", "list all", "pull out", "identify all", "entities"], "extraction"),
    (["write code", "def ", "function ", "implement", "debug", "fix this code", "class ", "```python", "```js", "```ts", "sql query", "bash script"], "code_generation"),
    (["analyze data", "analyse data", "interpret this", "what does this data", "trend", "chart", "table"], "data_analysis"),
    (["reason", "solve", "calculate", "math", "step by step", "chain of thought", "proof"], "reasoning"),
    (["write a blog", "write an email", "write a post", "write an article", "draft a", "compose a"], "content_generation"),
    (["function_call", "use tool", "call the", "invoke", "tool call"], "function_calling"),
]


def infer_task_type(prompt: str) -> str:
    """Return the most likely task type by scanning prompt keywords."""
    lower = prompt.lower()
    for keywords, task_type in _INFERENCE_RULES:
        if any(kw in lower for kw in keywords):
            return task_type
    return "chat"


def estimate_output_tokens(task_type: str, input_tokens: int) -> int:
    """Return estimated output token count for a given task type and input size."""
    profile = TASK_PROFILES.get(task_type, TASK_PROFILES["chat"])
    if profile["output_fixed"] is not None:
        return profile["output_fixed"]
    return max(1, round(input_tokens * profile["output_ratio"]))


def get_task_description(task_type: str) -> str:
    """Return the human-readable description for a task type."""
    profile = TASK_PROFILES.get(task_type)
    if profile:
        return profile["description"]
    return "General-purpose task"


def list_task_types() -> list[str]:
    """Return all supported task type names."""
    return list(TASK_PROFILES.keys())
