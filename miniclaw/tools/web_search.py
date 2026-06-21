"""
tools/web_search.py — 网络搜索工具

双后端策略：Tavily Search API (首选) → DuckDuckGo (免费回退)
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
from typing import List, Optional
from urllib.parse import quote

from .registry import tool_registry
from ..logger import get_logger

logger = get_logger(__name__)

_SEARCH_TIMEOUT = 15  # 秒
_MAX_RESULTS = 5
_TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# 请求间隔 (秒) — 避免触发速率限制
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 1.0

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
]


async def _rate_limit() -> None:
    """确保请求间隔，避免触发速率限制。"""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _strip_html(text: str) -> str:
    """去除 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", text or "")


# ── Tavily API（首选）───────────────────────────────────────────

async def _tavily_search(query: str, max_results: int = _MAX_RESULTS) -> Optional[List[dict]]:
    """Tavily Search API — 结构化搜索结果，质量更高。"""
    if not _TAVILY_API_KEY:
        return None

    await _rate_limit()
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": _TAVILY_API_KEY,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
                timeout=aiohttp.ClientTimeout(total=_SEARCH_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Tavily returned %s", resp.status)
                    return None
                data = await resp.json()
    except ImportError:
        return None
    except Exception as e:
        logger.debug("Tavily search failed: %s", e)
        return None

    results: List[dict] = []
    for r in data.get("results", [])[:max_results]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:300],
        })

    # 如有 Tavily 直接回答
    answer = data.get("answer", "")
    if answer and results:
        results[0]["snippet"] = f"**Answer:** {answer}\n\n{results[0]['snippet']}"

    logger.debug("Tavily returned %s results for '%s'", len(results), query[:50])
    return results if results else None


async def _ddg_instant_answer(query: str) -> Optional[str]:
    """DuckDuckGo Instant Answer API（免费、无认证）。"""
    url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except ImportError:
        return None
    except Exception as e:
        logger.debug("DDG instant answer failed: %s", e)
        return None

    abstract = (data.get("AbstractText") or "").strip()
    answer = (data.get("Answer") or "").strip()
    heading = (data.get("Heading") or "").strip()

    parts = []
    if heading:
        parts.append(f"**{heading}**")
    if answer:
        parts.append(answer)
    if abstract and abstract != answer:
        parts.append(abstract)
    return "\n\n".join(parts) if parts else None


async def _ddg_search_results(query: str, max_results: int = _MAX_RESULTS) -> List[dict]:
    """使用 DuckDuckGo HTML 搜索获取结果列表（Tavily 回退）。"""
    await _rate_limit()
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": _random_ua()}
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=_SEARCH_TIMEOUT)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
    except ImportError:
        return []
    except Exception as e:
        logger.debug("DDG search failed: %s", e)
        return []

    # 简单解析 HTML 搜索结果
    results: List[dict] = []
    # 匹配每个结果块：标题链接 + 摘要
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (href, title) in enumerate(links):
        if i >= max_results:
            break
        title_clean = _strip_html(title).strip()
        snippet = _strip_html(snippets[i]).strip() if i < len(snippets) else ""
        if title_clean:
            results.append({
                "title": title_clean,
                "url": href,
                "snippet": snippet[:300],
            })

    return results


def _format_results(instant: Optional[str], results: List[dict]) -> str:
    lines: List[str] = []

    if instant:
        lines.append(f"## 即时回答\n{instant}\n")

    if results:
        lines.append(f"## 搜索结果 ({len(results)} 条)")
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **{r['title']}**")
            lines.append(f"   URL: {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet']}")
            lines.append("")

    if not lines:
        return "未找到相关结果。请尝试调整搜索词。"
    return "\n".join(lines)


@tool_registry.register(
    name="web_search",
    description="Search the web using Tavily API (preferred) or DuckDuckGo (fallback).",
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Uses Tavily Search API when "
                "TAVILY_API_KEY is set, otherwise falls back to DuckDuckGo (free). "
                "Returns top search result snippets."
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"Max search results (default {_MAX_RESULTS}, max 10)",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["web", "search"],
    timeout_sec=20,
)
async def web_search_tool(query: str, max_results: int = _MAX_RESULTS, **kwargs) -> str:
    max_results = max(1, min(int(max_results or _MAX_RESULTS), 10))

    # 首选 Tavily
    if _TAVILY_API_KEY:
        results = await _tavily_search(query, max_results)
        if results:
            return _format_results(None, results)
        logger.info("Tavily search returned no results, falling back to DuckDuckGo")

    # 回退 DuckDuckGo
    instant, results = await asyncio.gather(
        _ddg_instant_answer(query),
        _ddg_search_results(query, max_results),
    )
    return _format_results(instant, results)
