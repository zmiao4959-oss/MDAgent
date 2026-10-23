"""Synchronize full GPUMD manual pages from the pinned official v5.5 RST.

The rolling documentation site and unversioned Tutorials/main repository are
not inputs to the default corpus. Syntax, units, examples and caveats stay in
one parent document per page; raw sources are cached for reproducibility.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .paths import gpumd_corpus_path, gpumd_skill_path
from .store import RagDocument

GPUMD_VERSION = "5.5"
SOURCE_REF = "v5.5"
MANUAL_ROOT = "doc/gpumd/"
MANUAL_INDEX = MANUAL_ROOT + "index.rst"
POTENTIALS_ROOT = "doc/potentials/"
DOMAIN_PAGES = (
    POTENTIALS_ROOT + "index.rst",
    "doc/nep/output_files/nep_txt.rst",
    "doc/nep/input_parameters/type.rst",
)
RAW_BASE = f"https://raw.githubusercontent.com/brucefan1983/GPUMD/{SOURCE_REF}/"
BLOB_BASE = f"https://github.com/brucefan1983/GPUMD/blob/{SOURCE_REF}/"
SITEMAP_URL = "https://gpumd.org/sitemap.xml"
TUTORIAL_BLOB_BASE = "https://github.com/brucefan1983/GPUMD-Tutorials/blob/main/"
USER_AGENT = "MiniClaw-GPUMD-RAG/2.0"
MAX_CHUNK_CHARS = 2800  # Soft limit for the legacy tutorial helper only.


def _fetch(url: str, *, attempts: int = 3, timeout: int = 30) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}：{last_error}") from last_error


def discover_manual_urls(sitemap_xml: bytes) -> list[str]:
    """Legacy sitemap mapper: return pinned raw sources, never rolling HTML.

    The default sync instead follows the pinned manual's RST toctrees.
    """
    root = ElementTree.fromstring(sitemap_xml)
    urls: set[str] = set()
    for loc in root.findall("{*}url/{*}loc"):
        raw = (loc.text or "").strip()
        match = re.search(r"/(?:v5\.5/)?(gpumd/.+\.html)(?:[?#].*)?$", raw)
        if match:
            relative = "doc/" + match.group(1)[:-5] + ".rst"
            urls.add(RAW_BASE + relative)
    return sorted(urls)


def _clean_text(node) -> str:
    if node.name == "pre":
        # Pygments wraps individual tokens in spans. A separator between spans
        # would turn one GPUMD command into many broken lines.
        return "```text\n" + node.get_text("", strip=False).strip("\n") + "\n```"
    if node.name == "table":
        rows = []
        for row in node.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)
    return node.get_text(" ", strip=True)


def extract_manual_sections(html: bytes) -> tuple[str, list[tuple[str, str]]]:
    """Legacy HTML reader; callers can recombine sections into full pages."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one('div[role="main"]') or soup.select_one("main")
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


def _text_blocks(text: str) -> list[str]:
    """Keep fenced code and RST literal blocks intact, including blank lines."""
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start = index
        fence = re.match(r"^\s*(`{3,}|~{3,})", lines[index])
        if fence:
            marker = fence.group(1)
            index += 1
            while index < len(lines):
                closing = re.match(r"^\s*([`~]+)\s*$", lines[index])
                index += 1
                if closing and closing.group(1)[0] == marker[0] and len(closing.group(1)) >= len(marker):
                    break
        else:
            while index < len(lines) and lines[index].strip():
                if index > start and re.match(r"^\s*(`{3,}|~{3,})", lines[index]):
                    break
                index += 1
            literal = any(re.match(r"^\s*\.\.\s+(?:code(?:-block)?|sourcecode|parsed-literal)::", line)
                          for line in lines[start:index]) or lines[index - 1].rstrip().endswith("::")
            if literal or lines[start][:1].isspace():
                next_index = index
                while next_index < len(lines):
                    line = lines[next_index]
                    if line.strip() and not line[:1].isspace():
                        break
                    next_index += 1
                index = next_index
        blocks.append("\n".join(lines[start:index]).rstrip())
    return blocks


def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Legacy soft chunking; never slice a command, line, or code block.

    Manual pages do not call this function. Indivisible tutorial blocks may
    intentionally exceed max_chars rather than becoming invalid code.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    chunks: list[str] = []
    current = ""
    for block in _text_blocks(text):
        candidate = f"{current}\n\n{block}" if current else block
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _manual_path(value: str) -> str:
    """Normalize an official source URL/path and reject other trees or refs."""
    if "://" in value:
        parsed = urlsplit(value)
        value = unquote(parsed.path)
        if parsed.netloc == "raw.githubusercontent.com":
            prefix = f"/brucefan1983/GPUMD/{SOURCE_REF}/"
        elif parsed.netloc == "github.com":
            prefix = f"/brucefan1983/GPUMD/blob/{SOURCE_REF}/"
        else:
            raise ValueError(f"不是官方固定版本的 GPUMD 来源：{value}")
        if prefix:
            if not value.startswith(prefix):
                raise ValueError(f"来源必须固定为 {SOURCE_REF}：{value}")
            value = value[len(prefix):]
    value = value.lstrip("/")
    if value.startswith("gpumd/"):
        value = "doc/" + value
    if value.endswith(".html"):
        value = value[:-5] + ".rst"
    value = posixpath.normpath(value)
    allowed = value.startswith((MANUAL_ROOT, POTENTIALS_ROOT)) or value in DOMAIN_PAGES
    if not allowed or not value.endswith(".rst"):
        raise ValueError(f"不属于 GPUMD 手册的 RST 路径：{value}")
    return value


def _rst_title(text: str, fallback: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        title, adornment = line.strip(), lines[index + 1].strip()
        if (title and not title.startswith("..") and len(adornment) >= 3
                and len(set(adornment)) == 1 and adornment[0] in "=~-^\"'*+#"):
            return title.replace("``", "").strip()
    return fallback


def manual_page_documents(url: str, raw: bytes) -> list[RagDocument]:
    """Return exactly one complete parent document per official manual page."""
    source_path = _manual_path(url)
    path = PurePosixPath(source_path)
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if re.match(r"\s*(?:<!doctype\s+html|<html)", text, re.I):
        raise ValueError(f"固定版本 RST 来源意外返回 HTML：{source_path}")
    title = _rst_title(text, path.stem)
    content = text.strip()
    if not content:
        raise ValueError(f"文档正文为空：{source_path}")
    category = path.parts[2] if len(path.parts) > 3 else "manual"
    if source_path.startswith(POTENTIALS_ROOT):
        kind = "potentials"
    elif source_path.startswith("doc/nep/"):
        kind = "potential_files"
    else:
        kind = category if category in {"input_parameters", "input_files", "output_files"} else "manual"
    keyword = path.stem
    aliases = sorted(set(re.findall(r"\b(?:nve|nvt|npt|nph|heat)_[a-z0-9_]+\b", content))) if keyword.startswith("ensemble") else []
    aliases = sorted(set(aliases + re.findall(r"^\.\. _([^:]+):\s*$", content, re.M)))
    return [RagDocument(
        id=f"manual:{SOURCE_REF}:{source_path}",
        title=title,
        section=kind,
        content=content,
        source=BLOB_BASE + quote(source_path, safe="/"),
        version=GPUMD_VERSION,
        metadata={
            "kind": kind, "keyword": keyword, "keywords": [keyword, *aliases],
            "aliases": aliases, "granularity": "page", "source_path": source_path,
            "source_ref": SOURCE_REF, "source_format": "rst",
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "official": True, "language": "en",
        },
    )]


def _resolve_target(source_path: str, target: str) -> str | None:
    target = target.strip()
    explicit = re.search(r"<([^<>]+)>\s*$", target)
    if explicit:
        target = explicit.group(1).strip()
    target = target.split("#", 1)[0]
    if not target or target == "self" or "://" in target:
        return None
    if any(char in target for char in "*?["):
        raise ValueError(f"固定手册出现未展开的 glob：{source_path}: {target}")
    if target.startswith("/"):
        relative = "doc/" + target.lstrip("/")
    else:
        relative = posixpath.join(posixpath.dirname(source_path), target)
    if not PurePosixPath(relative).suffix:
        relative += ".rst"
    relative = posixpath.normpath(relative)
    if (relative.startswith((MANUAL_ROOT, POTENTIALS_ROOT)) or relative in DOMAIN_PAGES) and relative.endswith(".rst"):
        return relative
    return None


def _rst_targets(source_path: str, raw: bytes) -> set[str]:
    """Follow internal toctree/doc/include targets without GitHub API calls."""
    text = raw.decode("utf-8-sig")
    candidates: list[str] = []
    in_toctree = False
    directive_indent = 0
    for line in text.splitlines():
        directive = re.match(r"^(\s*)\.\.\s+toctree::", line)
        if directive:
            in_toctree = True
            directive_indent = len(directive.group(1))
            continue
        if in_toctree:
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= directive_indent:
                in_toctree = False
            elif not line.lstrip().startswith(":"):
                candidates.append(line.strip())
    candidates.extend(re.findall(r":doc:`([^`]+)`", text))
    candidates.extend(re.findall(r"^\s*\.\.\s+include::\s+([^\n]+\.rst)\s*$", text, re.M))
    targets: set[str] = set()
    for candidate in candidates:
        target = _resolve_target(source_path, candidate)
        if target is not None:
            targets.add(target)
    return targets


def _atomic_write_bytes(output: Path, data: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_source(source_path: str, source_root: Path | None, refresh: bool) -> bytes:
    source_path = _manual_path(source_path)
    cached = source_root.joinpath(*PurePosixPath(source_path).parts) if source_root else None
    if cached is not None and cached.exists() and not refresh:
        raw = cached.read_bytes()
    else:
        raw = _fetch(RAW_BASE + quote(source_path, safe="/"))
    if not raw.strip():
        raise ValueError(f"源文件为空：{source_path}")
    text = raw.decode("utf-8-sig")
    if re.match(r"\s*(?:<!doctype\s+html|<html)", text, re.I):
        raise ValueError(f"固定版本 RST 来源意外返回 HTML：{source_path}")
    if cached is not None and (refresh or not cached.exists()):
        _atomic_write_bytes(cached, raw)
    return raw


def collect_manual_sources(source_root: Path, workers: int = 8, *, refresh: bool = False, include_domains: bool = True) -> dict[str, bytes]:
    """Traverse the pinned manual graph; any failure aborts publication."""
    if workers < 1:
        raise ValueError("workers must be positive")
    sources: dict[str, bytes] = {}
    pending = {MANUAL_INDEX, *(DOMAIN_PAGES if include_domains else ())}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        while pending:
            batch = sorted(pending - sources.keys())
            if not batch:
                break
            pending = set()
            futures = {executor.submit(_read_source, path, source_root, refresh): path for path in batch}
            errors: list[str] = []
            for future in as_completed(futures):
                path = futures[future]
                try:
                    raw = future.result()
                    sources[path] = raw
                    pending.update(_rst_targets(path, raw))
                except Exception as exc:
                    errors.append(f"{path}: {exc}")
            if errors:
                raise RuntimeError("固定版本手册同步失败，现有语料未替换：\n" + "\n".join(sorted(errors)[:10]))
    return sources


def fetch_manual_documents(urls: list[str], workers: int = 8) -> list[RagDocument]:
    """Compatibility helper for explicitly selected pinned source pages."""
    documents: list[RagDocument] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_read_source, _manual_path(url), None, False): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                documents.extend(manual_page_documents(url, future.result()))
            except Exception as exc:
                errors.append(f"{url}: {exc}")
    if errors:
        raise RuntimeError(f"有 {len(errors)} 个固定版本页面同步失败：\n" + "\n".join(errors[:10]))
    return sorted(documents, key=lambda document: document.id)


def _tutorial_file_allowed(path: PurePosixPath, size: int) -> bool:
    if size > 200_000 or len(path.parts) < 3:
        return False
    return path.name.lower().endswith((".md", ".in", ".sh"))


def tutorial_documents(zip_bytes: bytes) -> list[RagDocument]:
    """Explicit legacy import only; unversioned tutorials never enter sync()."""
    documents: list[RagDocument] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if info.is_dir() or not _tutorial_file_allowed(path, info.file_size):
                continue
            try:
                content = archive.read(info).decode("utf-8")
            except UnicodeDecodeError:
                continue
            repo_path = PurePosixPath(*path.parts[1:])
            for index, chunk in enumerate(split_text(content)):
                documents.append(RagDocument(
                    id=f"tutorial:{repo_path.as_posix()}:{index}",
                    title=f"GPUMD Tutorial：{repo_path.as_posix()}",
                    section=repo_path.parent.as_posix(), content=chunk,
                    source=TUTORIAL_BLOB_BASE + quote(repo_path.as_posix(), safe="/"),
                    metadata={"kind": "tutorial", "official": True, "source_ref": "main", "file_path": repo_path.as_posix()},
                ))
    return documents


def write_corpus(documents: list[RagDocument], output: Path) -> None:
    ordered = sorted(documents, key=lambda document: document.id)
    if not ordered:
        raise ValueError("拒绝用空语料覆盖现有文件")
    if len({document.id for document in ordered}) != len(ordered):
        raise ValueError("语料存在重复文档 id")
    payload = "".join(json.dumps(document.__dict__, ensure_ascii=False) + "\n" for document in ordered)
    _atomic_write_bytes(output, payload.encode("utf-8"))


def default_output() -> Path:
    return gpumd_corpus_path()


def knowledge_documents() -> list[RagDocument]:
    """Small source-backed usage notes live alongside the synchronizer."""
    documents = []
    for path in sorted((Path(__file__).parent / "gpumd_knowledge").glob("*.md")):
        if path.name.endswith(".entry.md"):
            continue
        content = path.read_text(encoding="utf-8").strip()
        title = next((line.lstrip("# ") for line in content.splitlines() if line.startswith("# ")), path.stem)
        documents.append(RagDocument(
            id=f"knowledge:{SOURCE_REF}:{path.stem}", title=title, content=content,
            source=BLOB_BASE + "src/model/read_xyz.cu", version=GPUMD_VERSION,
            section="knowledge", metadata={
                "kind": "knowledge", "keyword": path.stem,
                "keywords": ["NEP", "nep4_zbl", "元素对应", "元素顺序", "元素映射", "element mapping", "species", "type"],
                "aliases": ["nep_element_mapping"], "source_format": "markdown",
                "source_ref": SOURCE_REF, "source_path": "knowledge/" + path.name,
                "granularity": "page", "official": False, "language": "zh",
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        ))
    return documents


def example_documents(skill_dir: Path) -> list[RagDocument]:
    manifest = skill_dir / "examples" / "manifest.json"
    if not manifest.exists():
        return []
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload.get("examples", []) if isinstance(payload, dict) else payload
    documents = []
    root = skill_dir.resolve()
    for entry in entries:
        relative = str(entry.get("path") or f"{entry['id']}/example.md")
        if not relative.replace("\\", "/").startswith("examples/"):
            relative = "examples/" + relative
        path = (root / relative).resolve()
        if not path.is_relative_to(root / "examples"):
            raise ValueError(f"示例路径越界：{relative}")
        content = path.read_text(encoding="utf-8")
        documents.append(RagDocument(
            id=f"example:{SOURCE_REF}:{entry['id']}", title=entry["title"],
            content=content, source=path.as_uri(), version=GPUMD_VERSION,
            section="example", metadata={
                "kind": "example", "keyword": entry["id"],
                "keywords": entry.get("match_terms", entry.get("tasks", [])),
                "match_terms": entry.get("match_terms", entry.get("tasks", [])),
                "command_dependencies": entry.get("command_dependencies", entry.get("commands", [])),
                "granularity": "page", "source_format": "markdown", "official": False,
                "library_path": Path(relative).as_posix(), "local_path": str(path),
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        ))
    return documents


def install_skill_assets(skill_dir: Path) -> None:
    """Install the maintained example bundle, never Python cache files."""
    bundle = Path(__file__).parent / "gpumd_examples"
    payload = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    names = {"manifest.json", *payload.get("shared_files", [])}
    for entry in payload["examples"]:
        names.update(entry["files"])
    for name in sorted(names):
        source = (bundle / name).resolve()
        if not source.is_relative_to(bundle.resolve()):
            raise ValueError(f"示例资源路径越界：{name}")
        _atomic_write_bytes(skill_dir / "examples" / name, source.read_bytes())
    entry_path = skill_dir / "SKILL.md"
    if not entry_path.exists():
        _atomic_write_bytes(entry_path, (Path(__file__).parent / "gpumd_knowledge" / "SKILL.entry.md").read_bytes())


def sync(output: Path, workers: int = 8, *, refresh: bool = False, skill_dir: Path | None = None) -> tuple[int, int, int]:
    from .library import publish_library

    source_root = output.parent / "sources" / SOURCE_REF
    sources = collect_manual_sources(source_root, workers=workers, refresh=refresh)
    if len(sources) < 100:
        raise RuntimeError(f"发现的 {SOURCE_REF} GPUMD 官方页面过少：{len(sources)}，现有语料未替换")
    documents = [document for path, raw in sorted(sources.items()) for document in manual_page_documents(path, raw)]
    if skill_dir is None:
        skill_dir = gpumd_skill_path() if output.resolve() == default_output().resolve() else output.parent / "library"
    install_skill_assets(skill_dir)
    documents = publish_library([*documents, *knowledge_documents()], skill_dir)
    documents.extend(example_documents(skill_dir))
    write_corpus(documents, output)
    return len(sources), len(documents), 0


def main() -> None:
    parser = argparse.ArgumentParser(description="从官方 v5.5 RST 重建 GPUMD RAG；完整命令页，不混入未版本化教程")
    parser.add_argument("--output", type=Path, default=default_output())
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--refresh", action="store_true", help="重新下载固定版本原文（默认复用本地原文缓存）")
    parser.add_argument("--skill-dir", type=Path, help="结构化文档目录；默认语料使用gpumd-script Skill，临时语料使用旁边的library目录")
    args = parser.parse_args()
    pages, documents, _ = sync(args.output, workers=args.workers, refresh=args.refresh, skill_dir=args.skill_dir)
    print(f"同步完成：GPUMD {GPUMD_VERSION} 官方资料 {pages} 页 / {documents} 个文档（含使用说明与示例）；未版本化教程 0。")
    print(f"语料文件：{args.output}")
    print(f"原文缓存：{args.output.parent / 'sources' / SOURCE_REF}")
    print("来源：brucefan1983/GPUMD 固定 tag v5.5；下载或解析失败时保留原有语料。")


if __name__ == "__main__":
    main()
