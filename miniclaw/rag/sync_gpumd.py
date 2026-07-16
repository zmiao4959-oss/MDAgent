"""从 GPUMD 官方文档和教程仓库同步可检索语料。"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ..config import WORKSPACE_DIR
from .store import RagDocument

SITEMAP_URL = "https://gpumd.org/sitemap.xml"
TUTORIAL_ZIP_URL = (
    "https://codeload.github.com/brucefan1983/GPUMD-Tutorials/zip/refs/heads/main"
)
TUTORIAL_BLOB_BASE = "https://github.com/brucefan1983/GPUMD-Tutorials/blob/main/"
USER_AGENT = "MiniClaw-GPUMD-RAG/1.0"
MAX_CHUNK_CHARS = 2800
CHUNK_OVERLAP_CHARS = 240


def _fetch(url: str, *, attempts: int = 3, timeout: int = 30) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # 网络错误需要重试并保留最终异常
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}：{last_error}") from last_error


def discover_manual_urls(sitemap_xml: bytes) -> list[str]:
    """读取站点地图，并修复当前站点地图中缺失斜杠的稳定版链接。"""
    root = ElementTree.fromstring(sitemap_xml)
    urls: list[str] = []
    for loc in root.findall("{*}url/{*}loc"):
        raw = (loc.text or "").strip()
        match = re.search(r"/v5\.5/(gpumd/.+\.html)$", raw)
        if not match:
            continue
        urls.append(f"https://gpumd.org/{match.group(1)}")
    return sorted(set(urls))


def _clean_text(node) -> str:
    if node.name == "pre":
        return "代码：\n" + node.get_text("\n", strip=True)
    if node.name == "table":
        rows = []
        for row in node.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)
    return node.get_text(" ", strip=True)


def extract_manual_sections(html: bytes) -> tuple[str, list[tuple[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one('div[role="main"]')
    if root is None:
        raise ValueError("页面中没有找到 GPUMD 文档正文")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else "GPUMD documentation"
    page_title = re.sub(r"\s*[—-]?\s*GPUMD\s+documentation\s*$", "", page_title, flags=re.I)

    sections: list[tuple[str, str]] = []
    heading = page_title
    blocks: list[str] = []
    for node in root.find_all(["h1", "h2", "h3", "h4", "p", "pre", "table"]):
        if node.name in {"p", "pre"} and node.find_parent("table") is not None:
            continue
        text = _clean_text(node).strip()
        if not text:
            continue
        if node.name in {"h1", "h2", "h3", "h4"}:
            if blocks:
                sections.append((heading, "\n\n".join(blocks)))
            heading = text.replace("¶", "").strip()
            blocks = []
        else:
            blocks.append(text)
    if blocks:
        sections.append((heading, "\n\n".join(blocks)))
    return page_title, sections


def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            step = max_chars - CHUNK_OVERLAP_CHARS
            chunks.extend(
                paragraph[start:start + max_chars]
                for start in range(0, len(paragraph), step)
            )
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            overlap = current[-CHUNK_OVERLAP_CHARS:]
            current = f"{overlap}\n\n{paragraph}" if overlap else paragraph
    if current:
        chunks.append(current)
    return chunks


def manual_page_documents(url: str, html: bytes) -> list[RagDocument]:
    title, sections = extract_manual_sections(html)
    relative = url.split("gpumd.org/", 1)[-1]
    path = PurePosixPath(relative)
    kind = path.parts[1] if len(path.parts) > 1 else "manual"
    keyword = path.stem if kind == "input_parameters" else ""
    documents: list[RagDocument] = []
    for section_index, (section, text) in enumerate(sections):
        for chunk_index, chunk in enumerate(split_text(text)):
            documents.append(RagDocument(
                id=f"manual:{path.as_posix()}:{section_index}:{chunk_index}",
                title=title,
                section=section,
                content=chunk,
                source=url,
                version="5.5",
                metadata={
                    "kind": kind,
                    "keyword": keyword,
                    "official": True,
                    "language": "en",
                },
            ))
    return documents


def fetch_manual_documents(urls: list[str], workers: int = 8) -> list[RagDocument]:
    documents: list[RagDocument] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                documents.extend(manual_page_documents(url, future.result()))
            except Exception as exc:
                errors.append(f"{url}: {exc}")
    if errors:
        preview = "\n".join(errors[:10])
        raise RuntimeError(f"有 {len(errors)} 个官方文档页面同步失败：\n{preview}")
    return documents


def _tutorial_file_allowed(path: PurePosixPath, size: int) -> bool:
    if size > 200_000 or len(path.parts) < 3:
        return False
    lower_name = path.name.lower()
    if lower_name.endswith((".md", ".in", ".sh")):
        return True
    return lower_name == "model.xyz" and size <= 60_000


def tutorial_documents(zip_bytes: bytes) -> list[RagDocument]:
    documents: list[RagDocument] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if info.is_dir() or not _tutorial_file_allowed(path, info.file_size):
                continue
            raw = archive.read(info)
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            repo_path = PurePosixPath(*path.parts[1:])
            source = TUTORIAL_BLOB_BASE + quote(repo_path.as_posix(), safe="/")
            title = f"GPUMD Tutorial：{repo_path.as_posix()}"
            for index, chunk in enumerate(split_text(content)):
                documents.append(RagDocument(
                    id=f"tutorial:{repo_path.as_posix()}:{index}",
                    title=title,
                    section=repo_path.parent.as_posix(),
                    content=chunk,
                    source=source,
                    version="",
                    metadata={
                        "kind": "tutorial",
                        "official": True,
                        "file_path": repo_path.as_posix(),
                        "language": "code" if repo_path.suffix != ".md" else "en",
                    },
                ))
    return documents


def write_corpus(documents: list[RagDocument], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(documents, key=lambda doc: doc.id)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for document in ordered:
            handle.write(json.dumps(document.__dict__, ensure_ascii=False) + "\n")


def default_output() -> Path:
    return WORKSPACE_DIR / "skills" / "gpumd-script" / "references" / "corpus.jsonl"


def sync(output: Path, workers: int = 8) -> tuple[int, int, int]:
    urls = discover_manual_urls(_fetch(SITEMAP_URL))
    if len(urls) < 100:
        raise RuntimeError(f"发现的 GPUMD 官方页面过少：{len(urls)}")
    manual = fetch_manual_documents(urls, workers=workers)
    tutorials = tutorial_documents(_fetch(TUTORIAL_ZIP_URL, timeout=90))
    write_corpus(manual + tutorials, output)
    return len(urls), len(manual), len(tutorials)


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 GPUMD 官方 RAG 语料")
    parser.add_argument("--output", type=Path, default=default_output())
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    pages, manual_chunks, tutorial_chunks = sync(args.output, workers=args.workers)
    print(
        f"同步完成：官方文档 {pages} 页 / {manual_chunks} 块，"
        f"官方教程 {tutorial_chunks} 块，总计 {manual_chunks + tutorial_chunks} 块。"
    )
    print(f"语料文件：{args.output}")


if __name__ == "__main__":
    main()
