"""小规模知识库使用的 JSON 向量索引。"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import tempfile
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


@dataclass
class BuildStats:
    total: int
    reused: int
    embedded: int
    removed: int
    full_rebuild: bool


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
        force_full: bool = False,
    ) -> BuildStats:
        if batch_size < 1:
            raise ValueError("batch_size 必须大于 0")
        document_ids = [document.id for document in documents]
        if len(document_ids) != len(set(document_ids)):
            duplicates = sorted(
                doc_id for doc_id in set(document_ids) if document_ids.count(doc_id) > 1
            )
            raise ValueError(f"知识库存在重复文档 ID：{', '.join(duplicates[:10])}")

        previous = self._load_existing_index() if not force_full else None
        same_model = bool(previous and previous.get("model") == client.model)
        previous_records = {
            record.get("document", {}).get("id"): record
            for record in (previous or {}).get("records", [])
            if record.get("document", {}).get("id")
        }

        records: list[dict | None] = [None] * len(documents)
        pending: list[tuple[int, RagDocument, str]] = []
        reused = 0
        for index, document in enumerate(documents):
            content_hash = self._content_hash(document)
            old = previous_records.get(document.id) if same_model else None
            if old and self._record_hash(old) == content_hash and old.get("vector"):
                records[index] = {
                    "document": asdict(document),
                    "content_hash": content_hash,
                    "vector": old["vector"],
                }
                reused += 1
            else:
                pending.append((index, document, content_hash))

        if on_progress and reused:
            on_progress(reused, len(documents))
        embedded = 0
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            vectors = client.embed(
                [self._embedding_text(document) for _, document, _ in batch]
            )
            if len(vectors) != len(batch):
                raise RuntimeError("embedding 返回数量与待更新文档数量不一致")
            for (record_index, document, content_hash), vector in zip(batch, vectors):
                records[record_index] = {
                    "document": asdict(document),
                    "content_hash": content_hash,
                    "vector": vector,
                }
                embedded += 1
            if on_progress:
                on_progress(reused + embedded, len(documents))

        final_records = [record for record in records if record is not None]
        if len(final_records) != len(documents):
            raise RuntimeError("索引构建未覆盖全部文档")
        removed = len(set(previous_records) - set(document_ids)) if same_model else 0
        payload = {
            "schema_version": 2,
            "model": client.model,
            "count": len(final_records),
            "records": final_records,
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(payload)
        return BuildStats(
            total=len(final_records),
            reused=reused,
            embedded=embedded,
            removed=removed,
            full_rebuild=force_full or not same_model,
        )

    def _load_existing_index(self) -> dict | None:
        if not self.index_path.exists():
            return None
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _atomic_write_json(self, payload: dict) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.index_path.parent,
                prefix=self.index_path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.index_path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _record_hash(self, record: dict) -> str:
        stored = record.get("content_hash")
        if stored:
            return str(stored)
        # 兼容没有 content_hash 的 v1 索引，首次增量运行可直接复用旧向量。
        try:
            document = RagDocument(**record["document"])
        except (KeyError, TypeError):
            return ""
        return self._content_hash(document)

    @classmethod
    def _content_hash(cls, document: RagDocument) -> str:
        return hashlib.sha256(cls._embedding_text(document).encode("utf-8")).hexdigest()

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
