"""
tools/web_search.py — 网络搜索工具（免费，无需 API Key）

后端：DuckDuckGo 即时回答 + 搜索结果摘要
"""
from __future__ import annotations

import asyncio
import re
from typing import List, Optional
from urllib.parse import quote

from .registry import tool_registry
from ..logger import get_logger

logger = get_logger(__name__)

_SEARCH_TIMEOUT = 15  # 秒
_MAX_RESULTS = 5


def _strip_html(text: str) -> str:
    """去除 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", text or "")


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
    """使用 DuckDuckGo HTML 搜索获取结果列表。"""
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
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
    description="Search the web for information using DuckDuckGo (free, no API key needed).",
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Returns instant answers "
                "and top search result snippets. Use this when you need up-to-date "
                "information beyond your knowledge cutoff."
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
    instant, results = await asyncio.gather(
        _ddg_instant_answer(query),
        _ddg_search_results(query, max_results),
    )
    return _format_results(instant, results)
