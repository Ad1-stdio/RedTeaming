"""Retrieval for the support bot: split policy docs into chunks, rank them with TF-IDF."""
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"


def chunk_markdown(source: str, text: str) -> list[dict]:
    """Split one markdown document into chunks, one per '## ' section."""
    chunks = []
    heading = "Overview"
    lines: list[str] = []

    def flush():
        body = " ".join(lines).strip()
        if body:
            chunks.append({"source": source, "heading": heading, "text": body})

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            lines = []
        elif line.startswith("# "):
            continue  # document title, not content
        else:
            lines.append(line.strip())
    flush()
    return chunks


class Retriever:
    def __init__(self, docs_dir: Path = DOCS_DIR, top_k: int = 3):
        self.top_k = top_k
        self.chunks: list[dict] = []
        for path in sorted(Path(docs_dir).glob("*.md")):
            self.chunks += chunk_markdown(path.name, path.read_text(encoding="utf-8"))
        self._fit()

    def _fit(self) -> None:
        """(Re)build the TF-IDF index from the current chunks."""
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        # The heading is part of the searchable text, so a query matching a heading helps.
        texts = [f"{c['heading']}. {c['text']}" for c in self.chunks]
        self.matrix = self.vectorizer.fit_transform(texts)

    def add_document(self, name: str, text: str) -> None:
        """Add a document at runtime. The attack suite uses this to plant poisoned pages."""
        self.chunks += chunk_markdown(name, text)
        self._fit()

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """Return the best chunks for a query, each with a similarity score."""
        k = top_k or self.top_k
        scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix)[0]
        best = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [{**self.chunks[i], "score": round(float(scores[i]), 3)}
                for i in best if scores[i] > 0]
