"""小规模知识库使用的 JSON 向量索引。"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .embedding import EmbeddingClient


@dataclass
class RagDocument:
    id: str
    title: str
    content: str
    source: str
    version: str = ""
    section: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    document: RagDocument
    score: float
    mode: str


def load_documents(path: Path) -> list[RagDocument]:
    documents: list[RagDocument] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                documents.append(RagDocument(**json.loads(line)))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"知识库第 {line_number} 行格式无效: {exc}") from exc
    return documents


class JsonVectorStore:
    def __init__(self, index_path: Path):
        self.index_path = index_path

    def build(
        self,
        documents: Sequence[RagDocument],
        client: EmbeddingClient,
        *,
        batch_size: int = 16,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        records: list[dict] = []
        for start in range(0, len(documents), batch_size):
            batch = documents[start:start + batch_size]
            vectors = client.embed([self._embedding_text(doc) for doc in batch])
            for document, vector in zip(batch, vectors):
                records.append({"document": asdict(document), "vector": vector})
            if on_progress:
                on_progress(len(records), len(documents))
        payload = {"model": client.model, "count": len(records), "records": records}
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return len(records)

    def search(
        self,
        query: str,
        client: EmbeddingClient,
        *,
        top_k: int = 5,
        version: str = "",
    ) -> list[SearchResult]:
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        if payload.get("model") != client.model:
            raise ValueError("索引模型与当前 embedding 模型不一致，请重建索引")
        query_vector = client.embed([query])[0]
        results: list[SearchResult] = []
        for record in payload.get("records", []):
            document = RagDocument(**record["document"])
            if version and document.version and document.version != version:
                continue
            score = self._cosine(query_vector, record["vector"])
            results.append(SearchResult(document, score, "vector"))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def keyword_search(
        query: str,
        documents: Iterable[RagDocument],
        *,
        top_k: int = 5,
        version: str = "",
    ) -> list[SearchResult]:
        terms = JsonVectorStore._query_terms(query)
        results: list[SearchResult] = []
        for document in documents:
            if version and document.version and document.version != version:
                continue
            haystack = f"{document.title} {document.section} {document.content}".lower()
            score = sum((2.0 if term in document.title.lower() else 1.0) * haystack.count(term) for term in terms)
            if score > 0:
                results.append(SearchResult(document, score, "keyword"))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        """提取英文词和中文二元短语，保证无向量索引时仍可中文检索。"""
        terms: list[str] = []
        for token in re.findall(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]+", query):
            token = token.lower()
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                if len(token) <= 2:
                    terms.append(token)
                else:
                    terms.extend(token[index:index + 2] for index in range(len(token) - 1))
            else:
                terms.append(token)
        return list(dict.fromkeys(terms))

    @staticmethod
    def _embedding_text(document: RagDocument) -> str:
        return f"标题：{document.title}\n章节：{document.section}\n{document.content}"

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            raise ValueError("查询向量与索引向量维度不一致")
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
