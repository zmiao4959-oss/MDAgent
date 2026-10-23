"""Publish one GPUMD knowledge body for both file reading and retrieval.

The official source cache remains the reproducible input. Generated Markdown
pages are the canonical reading and embedding text; the catalog is a compact
locator, not another copy of command documentation.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import tempfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Sequence

from .store import RagDocument


_CATEGORIES = {
    "input_parameters": ("commands", "命令"),
    "input_files": ("files/input", "输入文件"),
    "output_files": ("files/output", "输出文件"),
    "potentials": ("potentials", "势函数"),
    "potential_files": ("potentials", "势文件格式"),
    "knowledge": ("potentials", "势函数知识"),
    "manual": ("manual", "手册概览"),
}
_ENSEMBLE_ALIASES = (
    "ensemble", "nve", "nvt", "npt", "nvt_ber", "nvt_nhc", "nvt_bdp",
    "nvt_lan", "nvt_bao", "npt_ber", "npt_scr",
)
_TASKS = (
    ("双轴拉伸", ("deform", "ensemble_standard"), ("双轴", "biaxial")),
    ("单轴拉伸", ("deform", "ensemble_standard"), ("单轴", "uniaxial")),
    ("NPT 弛豫与平衡", ("ensemble_standard", "minimize", "dump_restart"), ("npt", "弛豫", "equilibration")),
    ("NVT 升温与降温", ("ensemble_standard", "velocity", "time_step"), ("升温", "降温", "heating", "cooling")),
    ("能量最小化", ("minimize",), ("最小化", "minimization")),
    ("扩散与 MSD", ("compute_msd", "compute_vac", "ensemble_standard"), ("扩散", "msd", "diffusion")),
    ("径向分布 RDF", ("compute_rdf",), ("rdf", "径向")),
    ("热导率 HNEMD", ("compute_hnemd", "compute_shc", "ensemble_standard"), ("热导", "hnemd")),
    ("NEP 元素与类型映射", ("nep_element_mapping", "nep", "potential", "model_xyz"), ("元素映射", "元素对应", "element mapping")),
    ("模型与运行输入", ("model_xyz", "run_in", "potential"), ("建模", "input", "模型")),
)


def _plain_title(value: str) -> str:
    return re.sub(r":\w+:`([^`]+)`", r"\1", value).replace("``", "").strip()


def _safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError(f"知识库路径不安全：{relative}")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"知识库路径越界：{relative}")
    return target


def _page_path(document: RagDocument) -> str:
    kind = str(document.metadata.get("kind", "manual"))
    category = _CATEGORIES.get(kind, ("manual", "手册"))[0]
    keyword = str(document.metadata.get("library_slug") or document.metadata.get("keyword") or PurePosixPath(document.id).stem)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", keyword) or keyword in {".", ".."}:
        raise ValueError(f"知识库页面名称无效：{keyword}")
    if keyword == "index":
        keyword = "gpumd" if kind == "manual" else "overview"
    return f"{category}/{keyword}.md"


def _aliases(document: RagDocument) -> list[str]:
    keyword = str(document.metadata.get("keyword", ""))
    values = [keyword, *document.metadata.get("aliases", [])]
    values.extend(re.findall(r"^\s*\.\. _([^:]+):\s*$", document.content, flags=re.M))
    if keyword == "ensemble_standard":
        values.extend(_ENSEMBLE_ALIASES)
    if document.metadata.get("kind") in {"input_files", "output_files", "potential_files"}:
        values.append(re.sub(r"_(in|out|xyz|txt)$", r".\1", keyword))
    if keyword == "nep":
        values.extend(("nep_formalism", "NEP 势函数"))
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _relative_link(current: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(current) or ".")


def _rst_body(document: RagDocument, current: str, sources: dict[str, str], labels: dict[str, str]) -> str:
    """Convert only headings, literal blocks and known inline roles.

    Unknown RST directives remain verbatim so conversion cannot silently discard
    a table, mathematical definition, citation or an unfamiliar warning.
    """
    lines = document.content.splitlines()
    output: list[str] = []
    levels: dict[str, int] = {}
    source_dir = posixpath.dirname(str(document.metadata.get("source_path", "")))

    def inline(text: str) -> str:
        def reference(match: re.Match) -> str:
            role, value = match.group(1), match.group(2)
            explicit = re.fullmatch(r"(.+?)\s*<([^>]+)>", value)
            label, target = (explicit.group(1).strip(), explicit.group(2).strip()) if explicit else (value, value)
            if role == "doc":
                relative = target if target.endswith(".rst") else target + ".rst"
                resolved = posixpath.normpath(posixpath.join(source_dir, relative))
                destination = sources.get(resolved)
            else:
                destination = labels.get(target)
            if destination:
                return f"[{label}]({_relative_link(current, destination)})"
            return match.group(0)

        text = re.sub(r":(doc|ref):`([^`]+)`", reference, text)
        text = re.sub(r":(?:attr|file|code|class|func|term):`([^`]+)`", r"`\1`", text)
        return re.sub(r":math:`([^`]+)`", r"$\1$", text)

    def literal(start: int, directive_indent: int) -> tuple[list[str], int] | None:
        first = start
        while first < len(lines) and not lines[first].strip():
            first += 1
        if first >= len(lines) or len(lines[first]) - len(lines[first].lstrip()) <= directive_indent:
            return None
        end = first
        while end < len(lines):
            if lines[end].strip() and len(lines[end]) - len(lines[end].lstrip()) <= directive_indent:
                break
            end += 1
        nonblank = [line for line in lines[first:end] if line.strip()]
        indent = min(len(line) - len(line.lstrip()) for line in nonblank)
        return [line[indent:] if line.strip() else "" for line in lines[first:end]], end

    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r"\s*\.\. _[^:]+:\s*", line):
            index += 1
            continue
        if re.fullmatch(r"\s*\.\. index::\s*", line):
            indent = len(line) - len(line.lstrip())
            block = literal(index + 1, indent)
            index = block[1] if block else index + 1
            continue
        if index + 1 < len(lines) and line.strip() and re.fullmatch(r"([=~^*+\-])\1{2,}\s*", lines[index + 1].strip()):
            marker = lines[index + 1].strip()[0]
            level = levels.setdefault(marker, min(len(levels) + 2, 6))
            output.extend(("#" * level + " " + inline(line.strip()), ""))
            index += 2
            continue
        directive = re.fullmatch(r"(\s*)\.\. code(?:(?:-block)?::)(?:\s+(\S+))?\s*", line)
        if directive:
            block = literal(index + 1, len(directive.group(1)))
            if block:
                code, index = block
                language = directive.group(2) or "text"
                fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", "\n".join(code))), default=0) + 1)
                output.extend(("", fence + language, *code, fence, ""))
                continue
        if line.rstrip().endswith("::") and not line.lstrip().startswith(".. "):
            block = literal(index + 1, len(line) - len(line.lstrip()))
            if block:
                code, index = block
                lead = line.rstrip()[:-1]
                if lead.strip() != ":":
                    output.append(inline(lead))
                fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", "\n".join(code))), default=0) + 1)
                output.extend(("", fence + "text", *code, fence, ""))
                continue
        output.append(inline(line))
        index += 1
    return "\n".join(output).strip()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _examples(root: Path) -> list[dict]:
    manifest = _safe_path(root, "examples/manifest.json")
    if not manifest.exists():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("examples", [])
    results: list[dict] = []
    for entry in entries:
        value = dict(entry)
        relative = str(value.get("path", ""))
        if not relative:
            raise ValueError("示例 manifest 缺少 path")
        relative = relative if relative.startswith("examples/") else "examples/" + relative
        path = _safe_path(root, relative)
        if not path.is_file():
            raise ValueError(f"示例 manifest 指向不存在的文件：{relative}")
        value["path"] = relative
        value["title"] = str(value.get("title") or value.get("id") or path.stem)
        results.append(value)
    return results


def _link(page: dict, current: str) -> str:
    return f"[{page['keyword'] or page['title']}]({_relative_link(current, page['path'])})"


def _index_content(pages: list[dict], examples: list[dict]) -> dict[str, str]:
    by_keyword = {page["keyword"]: page for page in pages}
    groups: dict[str, list[dict]] = defaultdict(list)
    for page in pages:
        groups[posixpath.dirname(page["path"])].append(page)
    result: dict[str, str] = {}
    for category, category_pages in groups.items():
        current = category + "/index.md"
        title = next((title for directory, title in _CATEGORIES.values() if directory == category), "知识页面")
        lines = [f"# GPUMD 5.5 {title}目录", "", "[总目录](" + _relative_link(current, "index.md") + ")", "", "| 页面 | 名称与别名 |", "| --- | --- |"]
        for page in sorted(category_pages, key=lambda item: item["keyword"].casefold()):
            aliases = ", ".join(value for value in page["aliases"] if value != page["keyword"])
            lines.append(f"| {_link(page, current)} | {page['title']}" + (f"；{aliases}" if aliases else "") + " |")
        if category == "commands":
            related = [page for key in ("nep_element_mapping", "nep", "model_xyz", "run_in") if (page := by_keyword.get(key))]
            if related:
                lines.extend(("", "## 势函数与输入文件", "", ", ".join(_link(page, current) for page in related)))
        if category == "potentials":
            related = [page for key in ("potential", "model_xyz") if (page := by_keyword.get(key))]
            if related:
                lines.extend(("", "## 运行时使用", "", ", ".join(_link(page, current) for page in related)))
        result[current] = "\n".join(lines) + "\n"

    lines = ["# GPUMD 5.5 知识目录", "", "先按任务选相似示例；已知名称直接按下方目录定位。示例中的数值、势与模型需要按任务填写。完整参考用于核对变更、适用条件和疑问；条件一致且原样沿用的命令不需要逐条重读。", "", "## 按任务查找", "", "| 任务 | 相似示例 | 完整参考 |", "| --- | --- | --- |"]
    for title, keywords, tags in _TASKS:
        references = [by_keyword[keyword] for keyword in keywords if keyword in by_keyword]
        matches = []
        for example in examples:
            text = " ".join(str(example.get(key, "")) for key in ("id", "title", "description", "tasks", "tags")).casefold()
            if any(tag.casefold() in text for tag in tags):
                matches.append(f"[{example['title']}]({example['path']})")
        if references or matches:
            lines.append(f"| {title} | " + (", ".join(matches) if matches else "—") + " | " + ", ".join(_link(page, "index.md") for page in references) + " |")
    lines.extend(("", "## 按名称查找", ""))
    for category in sorted(groups):
        title = next((title for directory, title in _CATEGORIES.values() if directory == category), category)
        lines.append(f"- [{title}]({category}/index.md)")
    lines.extend(("", "完整名称、别名与文件路径对应关系见 [catalog.json](catalog.json)。", ""))
    if examples:
        lines.extend(("## 常见脚本示例", ""))
        example_lines = ["# GPUMD 5.5 常见脚本示例", "", "[任务与资料目录](../index.md)", "", "选择最相似的一例，读取说明与其中链接的脚本。数值为示范参数，模型与势需由任务提供。", ""]
        for example in examples:
            lines.append(f"- [{example['title']}]({example['path']})" + (f"：{example['description']}" if example.get("description") else ""))
            example_lines.append(f"- [{example['title']}]({_relative_link('examples/index.md', example['path'])})" + (f"：{example['description']}" if example.get("description") else ""))
        result["examples/index.md"] = "\n".join(example_lines) + "\n"
    result["index.md"] = "\n".join(lines).rstrip() + "\n"
    return result


def publish_library(documents: Sequence[RagDocument], skill_dir: Path) -> list[RagDocument]:
    """Publish complete pages and return precisely the same text for RAG.

    This owns generated pages, indexes and catalog.json only. It never changes
    SKILL.md, examples, or legacy references, and performs all path/ID checks
    before writing. Each output file is replaced atomically.
    """
    root = Path(skill_dir).expanduser().resolve()
    ids: set[str] = set()
    paths: set[str] = set()
    locations: dict[str, str] = {}
    sources: dict[str, str] = {}
    labels: dict[str, str] = {}
    for document in documents:
        if not document.id or document.id in ids:
            raise ValueError(f"知识库文档 ID 重复或为空：{document.id}")
        ids.add(document.id)
        relative = _page_path(document)
        _safe_path(root, relative)
        if relative.casefold() in paths:
            raise ValueError(f"知识库页面路径重复：{relative}")
        paths.add(relative.casefold())
        locations[document.id] = relative
        if source := document.metadata.get("source_path"):
            sources[str(source)] = relative
        for label in re.findall(r"^\s*\.\. _([^:]+):\s*$", document.content, flags=re.M):
            labels[label] = relative
    examples = _examples(root)
    published: list[RagDocument] = []
    pages: list[dict] = []
    writes: dict[str, str] = {}
    for document in documents:
        relative = locations[document.id]
        metadata = dict(document.metadata)
        keyword = str(metadata.get("keyword", ""))
        aliases = _aliases(document)
        body = document.content.strip() if metadata.get("source_format") == "markdown" else _rst_body(document, relative, sources, labels)
        title = _plain_title(document.title)
        lines = [f"# {title}", "", f"- GPUMD：{document.version or '5.5'}", f"- 名称：`{keyword}`"]
        if aliases:
            lines.append("- 名称与别名：" + ", ".join(f"`{alias}`" for alias in aliases))
        if document.source:
            source_label = "资料依据" if metadata.get("official") is False else "官方文档"
            lines.append(f"- 来源：[{metadata.get('source_ref', 'v5.5')} {source_label}]({document.source})")
        lines.extend(("- [知识总目录](" + _relative_link(relative, "index.md") + ")", "", "## 完整说明", "", body))
        content = "\n".join(lines).rstrip() + "\n"
        source_hash = metadata.get("source_content_hash") or metadata.get("content_hash") or hashlib.sha256(document.content.encode()).hexdigest()
        metadata.update(library_path=relative, local_path=str(_safe_path(root, relative)), source_content_hash=source_hash, content_hash=hashlib.sha256(content.encode()).hexdigest(), aliases=aliases, source_format="markdown", library_generated=True)
        published.append(replace(document, title=title, content=content, metadata=metadata))
        writes[relative] = content
        pages.append({"id": document.id, "title": title, "keyword": keyword, "kind": metadata.get("kind", "manual"), "aliases": aliases, "path": relative, "source": document.source, "version": document.version or "5.5", "content_hash": metadata["content_hash"], "source_content_hash": source_hash})
    writes.update(_index_content(pages, examples))
    aliases: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        for alias in page["aliases"]:
            aliases[alias].append(page["path"])
    catalog = {"schema_version": 1, "version": "5.5", "pages": pages, "aliases": dict(aliases), "examples": examples}
    writes["catalog.json"] = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    for relative in writes:
        _safe_path(root, relative)
    for relative, content in writes.items():
        _atomic_write(_safe_path(root, relative), content)
    return published
