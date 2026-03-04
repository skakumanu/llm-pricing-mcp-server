"""RAG pipeline: index, retrieve, and answer questions."""
import logging
from typing import List, Optional
from rag.document_loader import load_markdown_docs, load_pricing_documents
from rag.chunker import chunk_documents, Chunk
from rag.vector_store import TFIDFStore

logger = logging.getLogger(__name__)


class RAGPipeline:
    """Full RAG pipeline: build index from docs + pricing data, retrieve, answer."""

    def __init__(self, docs_path: str = "docs", top_k: int = 5):
        self._docs_path = docs_path
        self._default_top_k = top_k
        self._store = TFIDFStore()
        self._built = False

    async def build_index(self, pricing_data: list) -> None:
        """Build TF-IDF index from markdown docs and live pricing data."""
        docs = load_markdown_docs(self._docs_path)
        pricing_docs = load_pricing_documents(pricing_data)

        all_docs = docs + pricing_docs
        chunks = chunk_documents(all_docs)
        self._store.build(chunks)
        self._built = True

        logger.info(
            "RAG index built: %d doc chunks + %d pricing chunks = %d total",
            sum(1 for d in docs for _ in [d]),
            len(pricing_data),
            len(chunks),
        )

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[Chunk]:
        """Retrieve top-k relevant chunks for a query using TF-IDF similarity."""
        k = top_k if top_k is not None else self._default_top_k
        return self._store.retrieve(query, top_k=k)

    async def answer(self, query: str, llm_backend) -> str:
        """Retrieve context and generate an answer with the LLM backend."""
        chunks = self.retrieve(query)

        if chunks:
            context = "\n\n---\n\n".join(
                f"[Source: {chunk.source}]\n{chunk.content}"
                for chunk in chunks
            )
        else:
            context = "No relevant context found in the knowledge base."

        messages = [
            {
                "role": "user",
                "content": (
                    "Using the following context about LLM pricing, answer the question.\n\n"
                    f"Context:\n{context}\n\n"
                    f"Question: {query}"
                ),
            }
        ]

        response = await llm_backend.complete(messages, tools=[])
        return response.content
