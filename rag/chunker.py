"""Chunker for splitting documents into smaller chunks for RAG retrieval."""
import re
from pathlib import Path
from typing import List
from dataclasses import dataclass
from rag.document_loader import Document


@dataclass
class Chunk:
    """A chunk of text with source metadata."""
    content: str
    source: str
    chunk_id: str


def chunk_markdown(doc: Document) -> List[Chunk]:
    """Split markdown document by ## sections."""
    content = doc.content
    source = doc.source

    # Split by ## headings (keep the heading in each section)
    sections = re.split(r'\n(?=##\s)', content)

    chunks = []
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue

        # Extract heading for display in source
        heading_match = re.match(r'^#{1,6}\s+(.+)', section)
        heading = heading_match.group(1).strip() if heading_match else f"section_{i}"
        heading_slug = re.sub(r'[^a-z0-9]+', '-', heading.lower()).strip('-')

        filename = Path(source).name
        source_display = f"{filename}#{heading_slug}"

        chunks.append(Chunk(
            content=section,
            source=source_display,
            chunk_id=f"{source}#{heading_slug}_{i}",
        ))

    if not chunks:
        chunks.append(Chunk(
            content=content,
            source=Path(source).name,
            chunk_id=f"{source}#0",
        ))

    return chunks


def chunk_pricing(doc: Document) -> List[Chunk]:
    """Pricing documents are one chunk per model (already compact)."""
    return [Chunk(
        content=doc.content,
        source=doc.source,
        chunk_id=doc.source,
    )]


def chunk_document(doc: Document) -> List[Chunk]:
    """Chunk a document based on its type."""
    if doc.doc_type == "markdown":
        return chunk_markdown(doc)
    return chunk_pricing(doc)


def chunk_documents(docs: List[Document]) -> List[Chunk]:
    """Chunk all documents."""
    chunks = []
    for doc in docs:
        chunks.extend(chunk_document(doc))
    return chunks
