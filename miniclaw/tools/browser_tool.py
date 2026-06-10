"""
tools/browser_tool.py — 浏览器快照（Playwright，可选依赖）
"""
from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from .registry import tool_registry
from ..logger import get_logger

logger = get_logger(__name__)

_browser = None
_page = None
_playwright = None

MAX_BROWSER_CHARS = 50_000
DEFAULT_BROWSER_CHARS = 10_000
GOTO_TIMEOUT_MS = 20_000

# 禁止访问内网/本机（降低 SSRF 风险）
_BLOCKED_HOST_PATTERNS = (
    re.compile(r"^localhost$", re.I),
    re.compile(r"^127\.\d+\.\d+\.\d+$"),
    re.compile(r"^0\.0\.0\.0$"),
    re.compile(r"^10\.\d+\.\d+\.\d+$"),
    re.compile(r"^192\.168\.\d+\.\d+$"),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\.\d+\.\d+$"),
    re.compile(r"^\[::1\]$"),
)


def _validate_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    raw = (url or "").strip()
    if not raw:
        return None, "Error: url is empty"

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None, (
            f"Error: only http/https URLs are allowed (got scheme {parsed.scheme!r})"
        )
    if not parsed.netloc:
        return None, f"Error: invalid URL (no host): {url}"

    host = parsed.hostname or ""
    for pat in _BLOCKED_HOST_PATTERNS:
        if pat.match(host):
            return None, (
                f"Error: blocked host {host!r} (local/private addresses not allowed)"
            )
    return raw, None


async def _get_page():
    global _browser, _page, _playwright
    if _page is not None:
        return _page
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        )
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True)
    _page = await _browser.new_page()
    return _page


async def _reset_browser():
    global _browser, _page, _playwright
    for obj, closer in ((_page, "close"), (_browser, "close")):
        if obj is not None:
            try:
                await getattr(obj, closer)()
            except Exception:
                pass
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
    _page = _browser = _playwright = None


@tool_registry.register(
    name="browser_snapshot",
    description="Open an http(s) URL and return readable page text (Playwright).",
    schema={
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": (
                "Browse a public http(s) URL and return body text. "
                "Local/private hosts are blocked."
            ),
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL to open"},
                    "maxChars": {
                        "type": "integer",
                        "description": f"Max characters to return (default {DEFAULT_BROWSER_CHARS})",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="medium",
    tags=["browser"],
    timeout_sec=45,
)
async def browser_snapshot_tool(url: str, maxChars: int = DEFAULT_BROWSER_CHARS, **kwargs):
    safe_url, err = _validate_url(url)
    if err:
        return err

    try:
        max_chars = max(500, min(int(maxChars), MAX_BROWSER_CHARS))
    except (TypeError, ValueError):
        max_chars = DEFAULT_BROWSER_CHARS

    try:
        page = await _get_page()
        await page.goto(safe_url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        title = await page.title()
        text = await page.inner_text("body")
    except Exception as e:
        logger.warning("browser_snapshot failed for %s: %s", safe_url, e)
        await _reset_browser()
        return f"Error browsing {url}: {e}"

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated at {max_chars} chars)"
    return f"[Page: {page.url}]\n[Title: {title}]\n\n{text}"
