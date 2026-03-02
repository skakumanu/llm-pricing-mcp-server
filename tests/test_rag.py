"""Tests for the RAG pipeline: TF-IDF store, chunker, document loader, and pipeline."""
import sys
import pytest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rag.document_loader import Document, load_markdown_docs, pricing_metrics_to_document
from rag.chunker import Chunk, chunk_markdown, chunk_pricing, chunk_documents
from rag.vector_store import TFIDFStore
from rag.pipeline import RAGPipeline
from src.models.pricing import PricingMetrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metrics(**kwargs) -> PricingMetrics:
    defaults = dict(
        model_name="test-model",
        provider="TestProvider",
        cost_per_input_token=0.001,
        cost_per_output_token=0.002,
    )
    defaults.update(kwargs)
    return PricingMetrics(**defaults)


# ---------------------------------------------------------------------------
# TFIDFStore
# ---------------------------------------------------------------------------

class TestTFIDFStore:
    def test_empty_corpus_returns_empty(self):
        store = TFIDFStore()
        store.build([])
        assert store.retrieve("anything") == []

    def test_retrieve_returns_relevant_chunk(self):
        chunks = [
            Chunk("OpenAI GPT-4 costs thirty dollars per thousand tokens", "pricing:gpt-4", "id1"),
            Chunk("Anthropic Claude Haiku is the cheapest smallest model", "pricing:haiku", "id2"),
            Chunk("Architecture overview and design principles", "ARCHITECTURE.md#overview", "id3"),
        ]
        store = TFIDFStore()
        store.build(chunks)

        results = store.retrieve("cheapest smallest model")
        assert len(results) > 0
        assert results[0].source == "pricing:haiku"

    def test_retrieve_top_k_respected(self):
        chunks = [
            Chunk(f"document about cat dog bird number {i}", f"doc{i}", f"id{i}")
            for i in range(10)
        ]
        store = TFIDFStore()
        store.build(chunks)

        results = store.retrieve("cat dog bird", top_k=3)
        assert len(results) <= 3

    def test_retrieve_zero_score_excluded(self):
        chunks = [Chunk("apple banana mango fruit salad", "doc1", "id1")]
        store = TFIDFStore()
        store.build(chunks)

        results = store.retrieve("quantum nuclear physics reactor")
        assert results == []

    def test_single_chunk_corpus(self):
        chunks = [Chunk("only one document exists here", "only.md", "id0")]
        store = TFIDFStore()
        store.build(chunks)

        results = store.retrieve("document exists", top_k=5)
        assert len(results) == 1

    def test_retrieval_ordering_by_relevance(self):
        chunks = [
            Chunk("cost pricing token cheap budget economy", "cheap.md", "id1"),
            Chunk("performance throughput latency speed fast", "perf.md", "id2"),
            Chunk("context window size large tokens maximum", "ctx.md", "id3"),
        ]
        store = TFIDFStore()
        store.build(chunks)

        results = store.retrieve("cheap budget cost pricing")
        assert results[0].source == "cheap.md"

    def test_build_replaces_previous_index(self):
        store = TFIDFStore()
        store.build([Chunk("first corpus apple", "a.md", "id1")])
        store.build([Chunk("second corpus banana", "b.md", "id2")])

        results = store.retrieve("banana second corpus")
        assert len(results) == 1
        assert results[0].source == "b.md"


# ---------------------------------------------------------------------------
# Chunker — markdown
# ---------------------------------------------------------------------------

class TestChunkMarkdown:
    def test_splits_on_double_hash_headings(self):
        doc = Document(
            content="# Title\n\nIntro.\n\n## Section One\n\nContent one.\n\n## Section Two\n\nContent two.",
            source="docs/test.md",
            doc_type="markdown",
        )
        chunks = chunk_markdown(doc)
        sources = [c.source for c in chunks]
        assert any("section-one" in s for s in sources)
        assert any("section-two" in s for s in sources)

    def test_no_headings_returns_one_chunk(self):
        doc = Document(
            content="Just plain text with no headings at all.",
            source="docs/plain.md",
            doc_type="markdown",
        )
        chunks = chunk_markdown(doc)
        assert len(chunks) >= 1

    def test_empty_document_returns_one_chunk(self):
        doc = Document(content="", source="docs/empty.md", doc_type="markdown")
        chunks = chunk_markdown(doc)
        assert len(chunks) == 1

    def test_chunk_content_is_preserved(self):
        doc = Document(
            content="## My Section\n\nSome specific content here.",
            source="docs/file.md",
            doc_type="markdown",
        )
        chunks = chunk_markdown(doc)
        all_content = " ".join(c.content for c in chunks)
        assert "Some specific content here" in all_content

    def test_chunk_source_includes_filename(self):
        doc = Document(
            content="## Overview\n\nText.",
            source="docs/ARCHITECTURE.md",
            doc_type="markdown",
        )
        chunks = chunk_markdown(doc)
        assert all("ARCHITECTURE.md" in c.source for c in chunks)

    def test_chunk_ids_are_unique(self):
        doc = Document(
            content="## Alpha\n\nA.\n\n## Beta\n\nB.\n\n## Gamma\n\nC.",
            source="docs/multi.md",
            doc_type="markdown",
        )
        chunks = chunk_markdown(doc)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Chunker — pricing
# ---------------------------------------------------------------------------

class TestChunkPricing:
    def test_one_chunk_per_pricing_doc(self):
        doc = Document(
            content="Model: gpt-4\nProvider: OpenAI\nCost: 0.03",
            source="pricing:gpt-4",
            doc_type="pricing",
        )
        chunks = chunk_pricing(doc)
        assert len(chunks) == 1

    def test_chunk_preserves_full_content(self):
        doc = Document(
            content="Model: claude-haiku\nProvider: Anthropic",
            source="pricing:claude-haiku",
            doc_type="pricing",
        )
        chunks = chunk_pricing(doc)
        assert chunks[0].content == doc.content
        assert chunks[0].source == "pricing:claude-haiku"


# ---------------------------------------------------------------------------
# Chunker — dispatch
# ---------------------------------------------------------------------------

class TestChunkDocuments:
    def test_routes_markdown_and_pricing_by_type(self):
        docs = [
            Document("## Header\n\nText", "docs/a.md", "markdown"),
            Document("Model: test\nProvider: X", "pricing:test", "pricing"),
        ]
        chunks = chunk_documents(docs)
        assert len(chunks) >= 2

    def test_empty_list_returns_empty(self):
        assert chunk_documents([]) == []


# ---------------------------------------------------------------------------
# Document loader
# ---------------------------------------------------------------------------

class TestLoadMarkdownDocs:
    def test_nonexistent_path_returns_empty(self):
        docs = load_markdown_docs("/nonexistent/path/xyz_does_not_exist")
        assert docs == []

    def test_loads_only_md_files(self, tmp_path):
        (tmp_path / "a.md").write_text("# A\nContent A")
        (tmp_path / "b.md").write_text("# B\nContent B")
        (tmp_path / "ignore.txt").write_text("not markdown")
        (tmp_path / "ignore.py").write_text("print('hi')")

        docs = load_markdown_docs(str(tmp_path))
        assert len(docs) == 2
        assert all(d.doc_type == "markdown" for d in docs)

    def test_empty_directory_returns_empty(self, tmp_path):
        docs = load_markdown_docs(str(tmp_path))
        assert docs == []

    def test_doc_content_matches_file(self, tmp_path):
        (tmp_path / "guide.md").write_text("# Guide\n\nHello world.")
        docs = load_markdown_docs(str(tmp_path))
        assert len(docs) == 1
        assert "Hello world" in docs[0].content

    @pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
    def test_symlink_escaping_docs_dir_is_blocked(self, tmp_path):
        outside = tmp_path.parent / "_outside_secret.md"
        outside.write_text("# Secret\nDo not expose.")
        link = tmp_path / "evil.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation not supported")

        docs = load_markdown_docs(str(tmp_path))
        sources = [d.source for d in docs]
        assert not any("evil" in s or "_outside_secret" in s for s in sources)

        outside.unlink(missing_ok=True)

    def test_file_is_within_docs_dir(self, tmp_path):
        (tmp_path / "readme.md").write_text("# README")
        docs = load_markdown_docs(str(tmp_path))
        for doc in docs:
            assert str(tmp_path) in doc.source or tmp_path.name in doc.source


# ---------------------------------------------------------------------------
# pricing_metrics_to_document
# ---------------------------------------------------------------------------

class TestPricingMetricsToDocument:
    def test_required_fields_present(self):
        m = _make_metrics()
        doc = pricing_metrics_to_document(m)
        assert "test-model" in doc.content
        assert "TestProvider" in doc.content
        assert doc.source == "pricing:test-model"
        assert doc.doc_type == "pricing"

    def test_optional_fields_included_when_set(self):
        m = _make_metrics(
            use_cases=["summarization", "coding"],
            strengths=["fast", "cheap"],
            best_for="rapid prototyping",
            throughput=50.0,
            latency_ms=300.0,
            context_window=128000,
        )
        doc = pricing_metrics_to_document(m)
        assert "summarization" in doc.content
        assert "fast" in doc.content
        assert "rapid prototyping" in doc.content
        assert "50.0" in doc.content
        assert "128000" in doc.content

    def test_cost_volume_breakdowns_included(self):
        m = _make_metrics()
        doc = pricing_metrics_to_document(m)
        assert "10k tokens" in doc.content
        assert "100k tokens" in doc.content
        assert "1M tokens" in doc.content

    def test_source_follows_pricing_prefix_convention(self):
        m = _make_metrics(model_name="gemini-flash")
        doc = pricing_metrics_to_document(m)
        assert doc.source == "pricing:gemini-flash"


# ---------------------------------------------------------------------------
# RAGPipeline
# ---------------------------------------------------------------------------

class TestRAGPipeline:
    @pytest.mark.asyncio
    async def test_build_and_retrieve_from_docs(self, tmp_path):
        (tmp_path / "guide.md").write_text(
            "## Pricing Guide\n\nCheapest models save the most money on large workloads."
        )
        pipeline = RAGPipeline(docs_path=str(tmp_path), top_k=3)
        await pipeline.build_index([])

        results = pipeline.retrieve("cheapest models save money")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_build_with_pricing_data(self, tmp_path):
        metrics = [_make_metrics(model_name=f"model-{i}") for i in range(5)]
        pipeline = RAGPipeline(docs_path=str(tmp_path), top_k=5)
        await pipeline.build_index(metrics)

        results = pipeline.retrieve("TestProvider model cost token")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_retrieve_before_index_has_no_docs_returns_empty(self, tmp_path):
        pipeline = RAGPipeline(docs_path=str(tmp_path), top_k=3)
        await pipeline.build_index([])
        results = pipeline.retrieve("anything at all")
        assert isinstance(results, list)
        assert results == []

    @pytest.mark.asyncio
    async def test_top_k_default_respected(self, tmp_path):
        for i in range(10):
            (tmp_path / f"doc{i}.md").write_text(
                f"## Section {i}\n\nContent about pricing for model number {i}."
            )
        pipeline = RAGPipeline(docs_path=str(tmp_path), top_k=3)
        await pipeline.build_index([])

        results = pipeline.retrieve("pricing model content")
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_top_k_override_in_retrieve(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i}.md").write_text(
                f"## File {i}\n\nContent about tokens and cost {i}."
            )
        pipeline = RAGPipeline(docs_path=str(tmp_path), top_k=10)
        await pipeline.build_index([])

        results = pipeline.retrieve("tokens cost content", top_k=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_nonexistent_docs_path_still_indexes_pricing(self, tmp_path):
        metrics = [_make_metrics(model_name="gpt-4", provider="OpenAI")]
        pipeline = RAGPipeline(docs_path="/nonexistent/docs/path", top_k=5)
        await pipeline.build_index(metrics)

        results = pipeline.retrieve("OpenAI gpt-4 cost")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_rebuild_replaces_old_index(self, tmp_path):
        (tmp_path / "v1.md").write_text("## V1\n\nOriginal apple banana content.")
        pipeline = RAGPipeline(docs_path=str(tmp_path), top_k=5)
        await pipeline.build_index([])

        # Rebuild with different content
        (tmp_path / "v1.md").write_text("## V2\n\nNew mango pineapple content.")
        await pipeline.build_index([])

        results = pipeline.retrieve("mango pineapple new content")
        assert len(results) > 0
