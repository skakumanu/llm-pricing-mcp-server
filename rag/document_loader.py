"""Document loader for RAG pipeline - loads markdown docs and pricing data."""
import logging
from pathlib import Path
from typing import List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Document:
    """A raw document with content and metadata."""
    content: str
    source: str
    doc_type: str  # "markdown" or "pricing"


def load_markdown_docs(docs_path: str) -> List[Document]:
    """Load all .md files from the configured docs directory.

    Only files that resolve to a path *inside* docs_dir are loaded; symlinks
    that escape the directory are silently skipped to prevent path traversal.
    """
    documents = []
    docs_dir = Path(docs_path).resolve()

    if not docs_dir.exists() or not docs_dir.is_dir():
        return documents

    for md_file in sorted(docs_dir.glob("*.md")):
        try:
            # Resolve symlinks and verify the file stays within docs_dir
            resolved = md_file.resolve()
            resolved.relative_to(docs_dir)  # raises ValueError if outside
        except (ValueError, OSError):
            continue  # skip files that escape the allowed directory

        try:
            content = resolved.read_text(encoding="utf-8")
            documents.append(Document(
                content=content,
                source=str(md_file),
                doc_type="markdown",
            ))
        except Exception as e:
            logger.debug(f"Skipping unreadable file {md_file}: {e}")
            continue

    return documents


def pricing_metrics_to_document(metrics) -> Document:
    """Convert a PricingMetrics object to a Document."""
    lines = [
        f"Model: {metrics.model_name}",
        f"Provider: {metrics.provider}",
        f"Cost per input token: ${metrics.cost_per_input_token:.8f} USD"
        f" (per 1k tokens: ${metrics.cost_per_input_token:.6f})",
        f"Cost per output token: ${metrics.cost_per_output_token:.8f} USD"
        f" (per 1k tokens: ${metrics.cost_per_output_token:.6f})",
        f"Currency: {metrics.currency}",
        f"Unit: {metrics.unit}",
    ]

    if metrics.throughput is not None:
        lines.append(f"Throughput: {metrics.throughput} tokens/second")
    if metrics.latency_ms is not None:
        lines.append(f"Latency: {metrics.latency_ms} ms")
    if metrics.context_window is not None:
        lines.append(f"Context window: {metrics.context_window} tokens")
    if metrics.use_cases:
        lines.append(f"Use cases: {', '.join(metrics.use_cases)}")
    if metrics.strengths:
        lines.append(f"Strengths: {', '.join(metrics.strengths)}")
    if metrics.best_for:
        lines.append(f"Best for: {metrics.best_for}")

    cost_10k = metrics.cost_at_10k_tokens
    cost_100k = metrics.cost_at_100k_tokens
    cost_1m = metrics.cost_at_1m_tokens
    lines.append(f"Cost at 10k tokens (avg): ${cost_10k.total_cost:.6f}")
    lines.append(f"Cost at 100k tokens (avg): ${cost_100k.total_cost:.6f}")
    lines.append(f"Cost at 1M tokens (avg): ${cost_1m.total_cost:.4f}")

    content = "\n".join(lines)
    return Document(
        content=content,
        source=f"pricing:{metrics.model_name}",
        doc_type="pricing",
    )


def load_pricing_documents(pricing_data: list) -> List[Document]:
    """Convert list of PricingMetrics to Documents."""
    return [pricing_metrics_to_document(m) for m in pricing_data]
