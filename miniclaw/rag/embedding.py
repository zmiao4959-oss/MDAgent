"""远程 embedding API 抽象及 OpenAI/火山方舟实现。"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, Sequence

import httpx
from openai import OpenAI


class EmbeddingClient(Protocol):
    """向量服务最小接口，后续可替换为任意内部实现。"""

    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingClient:
    """通过 OpenAI 兼容的 ``/embeddings`` API 获取文本向量。"""

    def __init__(self, *, base_url: str, api_key: str, model: str):
        if not api_key:
            raise ValueError("未配置 embedding API 密钥")
        self._model = model
        self._client = OpenAI(base_url=base_url.rstrip("/"), api_key=api_key)

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(text).strip() for text in texts]
        if not items or any(not item for item in items):
            raise ValueError("embedding 输入不能为空")
        response = self._client.embeddings.create(
            model=self._model,
            input=items,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        if len(vectors) != len(items):
            raise RuntimeError("embedding API 返回的向量数量与输入不一致")
        return vectors


class VolcengineMultimodalEmbeddingClient:
    """火山方舟图文向量接口；纯文本也必须使用多模态输入结构。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_concurrency: int = 4,
        timeout_sec: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("未配置 embedding API 密钥")
        self._model = model
        self._max_concurrency = max(1, int(max_concurrency))
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_sec,
            transport=transport,
        )

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(text).strip() for text in texts]
        if not items or any(not item for item in items):
            raise ValueError("embedding 输入不能为空")
        if len(items) == 1:
            return [self._embed_one(items[0])]
        with ThreadPoolExecutor(max_workers=min(self._max_concurrency, len(items))) as executor:
            # executor.map 保持输入顺序，避免文档与向量错位。
            return list(executor.map(self._embed_one, items))

    def _embed_one(self, text: str) -> list[float]:
        body = {
            "model": self._model,
            "encoding_format": "float",
            "input": [{"type": "text", "text": text}],
        }
        response: httpx.Response | None = None
        for attempt in range(4):
            response = self._client.post("embeddings/multimodal", json=body)
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            time.sleep(0.5 * (2 ** attempt))
        assert response is not None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:1000]
            raise RuntimeError(
                f"火山方舟多模态 embedding 请求失败（HTTP {response.status_code}）：{detail}"
            ) from exc
        return self._extract_dense_vector(response.json())

    @staticmethod
    def _extract_dense_vector(payload: dict) -> list[float]:
        data = payload.get("data")
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            raise RuntimeError("火山方舟响应缺少 data 对象")
        vector = data.get("embedding")
        # 部分多模态响应把单个稠密向量再包一层数组。
        while isinstance(vector, list) and len(vector) == 1 and isinstance(vector[0], list):
            vector = vector[0]
        if not isinstance(vector, list) or not vector or not all(
            isinstance(value, (int, float)) for value in vector
        ):
            raise RuntimeError("火山方舟响应中没有可识别的稠密向量")
        return [float(value) for value in vector]


def create_embedding_client(
    *,
    base_url: str,
    api_key: str,
    model: str,
    api_type: str = "auto",
    max_concurrency: int = 4,
) -> EmbeddingClient:
    """根据显式配置或带日期的 vision 模型名选择正确 API。"""
    selected = api_type.strip().lower()
    if selected == "auto":
        selected = (
            "volcengine_multimodal"
            if re.fullmatch(r"doubao-embedding-vision-\d{6}", model, flags=re.I)
            else "openai"
        )
    if selected == "volcengine_multimodal":
        return VolcengineMultimodalEmbeddingClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            max_concurrency=max_concurrency,
        )
    if selected == "openai":
        return OpenAICompatibleEmbeddingClient(
            base_url=base_url, api_key=api_key, model=model
        )
    raise ValueError(f"不支持的 embedding api_type：{api_type}")
