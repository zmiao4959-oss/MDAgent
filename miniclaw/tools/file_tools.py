"""
tools/file_tools.py — 工作区内文件读写、目录列表与内容搜索
"""
from __future__ import annotations

import re
from pathlib import Path

from .registry import tool_registry
from .paths import (
    resolve_workspace_path,
    is_image,
    is_probably_text,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    MAX_LIST_ENTRIES,
    DEFAULT_READ_LIMIT,
    MAX_READ_LIMIT,
    workspace_root,
)


def _format_read_lines(
    lines: list[str],
    *,
    path_display: str,
    start_line: int,
    total_lines: int,
    end_line: int,
) -> str:
    body = "".join(lines)
    header = f"File: {path_display} (lines {start_line}-{end_line} of {total_lines})\n"
    if end_line < total_lines:
        body += f"\n... ({total_lines - end_line} more lines; use offset/limit to continue)"
    return header + "\n" + body


@tool_registry.register(
    name="read",
    description="Read a text file or preview an image inside the workspace.",
    schema={
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read file contents under the workspace. Text files support offset/limit "
                "(1-based line numbers). Images return a short metadata note (not full base64)."
            ),
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to workspace or absolute under workspace",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Start line (1-based, default 1)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Max lines to read (default {DEFAULT_READ_LIMIT}, max {MAX_READ_LIMIT})",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["filesystem"],
)
def read_tool(path: str, offset: int = 1, limit: int = DEFAULT_READ_LIMIT, **kwargs):
    resolved, err = resolve_workspace_path(path, must_exist=True)
    if err:
        return err

    if resolved.is_dir():
        return f"Error: '{path}' is a directory. Use the `list` tool instead."

    try:
        size = resolved.stat().st_size
    except OSError as e:
        return f"Error: cannot stat {path}: {e}"

    if is_image(resolved):
        return (
            f"[Image: {path}] size={size} bytes, type={resolved.suffix.lower()}. "
            "Use vision-capable models or copy the file path for external viewing."
        )

    if size > MAX_READ_BYTES and not is_probably_text(resolved):
        return (
            f"Error: file too large ({size} bytes) and does not look like text "
            f"(max {MAX_READ_BYTES} bytes). Path: {path}"
        )

    try:
        offset = max(1, int(offset))
    except (TypeError, ValueError):
        offset = 1
    try:
        limit = max(1, min(int(limit), MAX_READ_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_READ_LIMIT

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except UnicodeDecodeError:
        return (
            f"Error: cannot decode '{path}' as UTF-8 (likely binary). "
            f"Size={size} bytes."
        )
    except OSError as e:
        return f"Error reading {path}: {e}"

    total = len(all_lines)
    start = offset - 1
    if start >= total:
        return f"Error: offset {offset} is past end of file ({total} lines)"
    end = min(start + limit, total)
    chunk = all_lines[start:end]
    return _format_read_lines(
        chunk,
        path_display=str(resolved.relative_to(workspace_root())),
        start_line=start + 1,
        total_lines=total,
        end_line=end,
    )


@tool_registry.register(
    name="write",
    description="Write or overwrite a text file inside the workspace.",
    schema={
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write UTF-8 text to a workspace file (creates parent dirs).",
            "parameters": {
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string", "description": "File path under workspace"},
                    "content": {"type": "string", "description": "UTF-8 text content"},
                },
            },
        },
    },
    require_approval=True,
    risk_level="medium",
    tags=["filesystem"],
)
def write_tool(path: str, content: str, **kwargs):
    if content is None:
        return "Error: content is required"

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        return (
            f"Error: content too large ({len(encoded)} bytes, "
            f"max {MAX_WRITE_BYTES} bytes)"
        )

    resolved, err = resolve_workspace_path(path, allow_create=True)
    if err:
        return err

    if resolved.exists() and resolved.is_dir():
        return f"Error: '{path}' is a directory, not a file"

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error writing {path}: {e}"

    rel = resolved.relative_to(workspace_root())
    return f"Successfully wrote {len(encoded)} bytes to {rel}"


@tool_registry.register(
    name="list",
    description="List files and directories inside the workspace.",
    schema={
        "type": "function",
        "function": {
            "name": "list",
            "description": "List directory entries under the workspace (non-recursive by default).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: workspace root)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern for names, e.g. '*.py' (default '*')",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, list recursively (capped)",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["filesystem"],
)
def list_tool(
    path: str = ".",
    pattern: str = "*",
    recursive: bool = False,
    **kwargs,
):
    resolved, err = resolve_workspace_path(path, must_exist=True)
    if err:
        return err

    if not resolved.is_dir():
        return f"Error: '{path}' is not a directory"

    glob_pattern = pattern or "*"
    try:
        if recursive:
            entries = sorted(resolved.rglob(glob_pattern))
        else:
            entries = sorted(resolved.glob(glob_pattern))
    except OSError as e:
        return f"Error listing {path}: {e}"

    if not entries:
        return f"Directory: {resolved.relative_to(workspace_root())}\n(empty or no matches for {glob_pattern!r})"

    lines = [f"Directory: {resolved.relative_to(workspace_root())}  pattern={glob_pattern!r}"]
    shown = 0
    for entry in entries:
        if shown >= MAX_LIST_ENTRIES:
            lines.append(f"... ({len(entries) - shown} more entries omitted)")
            break
        try:
            rel = entry.relative_to(workspace_root())
        except ValueError:
            continue
        if entry.is_dir():
            kind, size = "dir", ""
        elif entry.is_file():
            kind = "file"
            try:
                size = f"  {entry.stat().st_size} bytes"
            except OSError:
                size = ""
        else:
            kind, size = "other", ""
        lines.append(f"  [{kind}] {rel}{size}")
        shown += 1

    return "\n".join(lines)


_MAX_GREP_MATCHES = 80
_MAX_GREP_FILES = 200
_SKIP_GREP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv"})


@tool_registry.register(
    name="grep",
    description="Search for a regex pattern in workspace text files.",
    schema={
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents under the workspace for a regex pattern. "
                "Prefer this over `execute` with findstr/rg."
            ),
            "parameters": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory under workspace (default: workspace root)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Filter files by glob, e.g. '*.py' (default: all text files)",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "Case-insensitive search (default true)",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["filesystem"],
    timeout_sec=30,
)
def grep_tool(
    pattern: str,
    path: str = ".",
    glob: str = "",
    ignore_case: bool = True,
    **kwargs,
):
    if not pattern or not str(pattern).strip():
        return "Error: pattern is required"

    try:
        flags = re.IGNORECASE if ignore_case else 0
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: invalid regex pattern: {e}"

    resolved, err = resolve_workspace_path(path, must_exist=True)
    if err:
        return err

    root = workspace_root()
    files: list[Path] = []
    if resolved.is_file():
        files = [resolved]
    else:
        glob_pat = glob or "*"
        try:
            candidates = resolved.rglob(glob_pat) if glob else resolved.rglob("*")
            for fp in candidates:
                if len(files) >= _MAX_GREP_FILES:
                    break
                if not fp.is_file():
                    continue
                if any(part in _SKIP_GREP_DIRS for part in fp.parts):
                    continue
                if fp.stat().st_size > MAX_READ_BYTES:
                    continue
                if is_image(fp) and not is_probably_text(fp):
                    continue
                files.append(fp)
        except OSError as e:
            return f"Error scanning {path}: {e}"

    matches: list[str] = []
    files_scanned = 0
    for fp in files:
        files_scanned += 1
        try:
            text = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = fp.relative_to(root)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                matches.append(f"{rel}:{line_no}: {line.rstrip()}")
                if len(matches) >= _MAX_GREP_MATCHES:
                    break
        if len(matches) >= _MAX_GREP_MATCHES:
            break

    header = (
        f"grep pattern={pattern!r} path={path!r} "
        f"files_scanned={files_scanned} matches={len(matches)}"
    )
    if not matches:
        return f"{header}\n(no matches)"
    body = "\n".join(matches)
    if len(matches) >= _MAX_GREP_MATCHES:
        body += f"\n... (stopped at {_MAX_GREP_MATCHES} matches)"
    return f"{header}\n{body}"
