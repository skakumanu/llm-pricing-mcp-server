"""In-memory TF-IDF vector store for RAG retrieval."""
import math
import re
from typing import List, Dict
from collections import defaultdict
from rag.chunker import Chunk


def _tokenize(text: str) -> List[str]:
    """Lowercase and split on non-alphanumeric characters."""
    return re.findall(r'[a-z0-9]+', text.lower())


class TFIDFStore:
    """TF-IDF cosine similarity retrieval over a fixed chunk corpus."""

    def __init__(self):
        self.chunks: List[Chunk] = []
        self._idf: Dict[str, float] = {}
        self._chunk_tfidf: List[Dict[str, float]] = []

    def build(self, chunks: List[Chunk]) -> None:
        """Build TF-IDF index from a list of chunks."""
        self.chunks = chunks
        n = len(chunks)
        if n == 0:
            return

        # Document frequency: how many docs contain each term
        df: Dict[str, int] = defaultdict(int)
        all_tokens: List[List[str]] = []
        for chunk in chunks:
            tokens = _tokenize(chunk.content)
            all_tokens.append(tokens)
            for term in set(tokens):
                df[term] += 1

        # Smoothed IDF: log((N+1)/(df+1)) + 1
        self._idf = {
            term: math.log((n + 1) / (count + 1)) + 1
            for term, count in df.items()
        }

        # TF-IDF vectors (term frequency × idf)
        self._chunk_tfidf = []
        for tokens in all_tokens:
            tf: Dict[str, float] = defaultdict(float)
            for token in tokens:
                tf[token] += 1
            total = max(sum(tf.values()), 1)
            tfidf = {
                term: (count / total) * self._idf.get(term, 0.0)
                for term, count in tf.items()
            }
            self._chunk_tfidf.append(tfidf)

    def _query_vector(self, query: str) -> Dict[str, float]:
        tokens = _tokenize(query)
        tf: Dict[str, float] = defaultdict(float)
        for token in tokens:
            tf[token] += 1
        total = max(sum(tf.values()), 1)
        return {
            term: (count / total) * self._idf.get(term, 0.0)
            for term, count in tf.items()
        }

    @staticmethod
    def _cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1.get(k, 0.0) * v for k, v in v2.items())
        norm1 = math.sqrt(sum(x * x for x in v1.values()))
        norm2 = math.sqrt(sum(x * x for x in v2.values()))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot / (norm1 * norm2)

    def retrieve(self, query: str, top_k: int = 5) -> List[Chunk]:
        """Return top-k chunks ranked by cosine similarity to query."""
        if not self.chunks:
            return []

        q_vec = self._query_vector(query)
        scores = [
            (i, self._cosine(q_vec, chunk_tfidf))
            for i, chunk_tfidf in enumerate(self._chunk_tfidf)
        ]
        scores.sort(key=lambda x: x[1], reverse=True)

        return [
            self.chunks[i]
            for i, score in scores[:top_k]
            if score > 0.0
        ]
