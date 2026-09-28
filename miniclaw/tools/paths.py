"""
tools/paths.py - path resolution and access control shared by filesystem tools.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Tuple

from ..settings import WORKSPACE_DIR, active_workspace_dir

# Single-file read/write limits (bytes)
MAX_READ_BYTES = 512 * 1024
MAX_WRITE_BYTES = 2 * 1024 * 1024
MAX_LIST_ENTRIES = 500

# Default and maximum `read` line limits
DEFAULT_READ_LIMIT = 2000
MAX_READ_LIMIT = 5000

_TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".css", ".scss", ".sql", ".sh",
    ".bat", ".ps1", ".csv", ".log", ".env", ".lmp", ".in", ".data",
})

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})


def workspace_root() -> Path:
    '~/.miniclaw/workspace'
    return active_workspace_dir().resolve()

def skill_roots() -> tuple[Path, ...]:
    '~/.miniclaw/builtin_skills, ~/.miniclaw/skills, <workspace>/skills'
    roots = (
        (Path(__file__).resolve().parent.parent / "builtin_skills").resolve(),
        (WORKSPACE_DIR.parent / "skills").resolve(),
        (WORKSPACE_DIR / "skills").resolve(),
    )
    deduped = []
    seen = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return tuple(deduped)


def readable_roots() -> tuple[Path, ...]:
    '限制只可以读取的根目录'
    roots = [workspace_root(), WORKSPACE_DIR.resolve(), *skill_roots()]
    deduped = []
    seen = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return tuple(deduped)


def display_root(resolved: Path) -> Path:
    """Pick the most appropriate root for displaying a relative path."""
    for root in readable_roots():
        if _is_under_root(resolved, root):
            return root
    return WORKSPACE_DIR.resolve()


def safe_relpath(resolved: Path) -> str:
    """Return a readable path relative to the best matching allowed root."""
    try:
        return resolved.relative_to(display_root(resolved)).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_workspace_path(
    path: str,
    *,
    must_exist: bool = False,
    allow_create: bool = False,
    extra_roots: Optional[Iterable[Path]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Resolve a relative or absolute path against the active workspace.

    Relative paths are rooted in the active project workspace. Absolute paths
    must stay inside one of the allowed roots: the active workspace, the global
    workspace, or any explicitly passed extra roots.
    """
    if not path or not str(path).strip():
        return None, "Error: path is empty"

    raw = Path(path.strip())
    root = workspace_root()
    global_root = WORKSPACE_DIR.resolve()
    allowed_roots = _dedupe_roots([root, global_root, *(extra_roots or ())])

    try:
        if raw.is_absolute():
            resolved = raw.resolve()
        else:
            resolved = (root / raw).resolve()
        if not any(_is_under_root(resolved, allowed) for allowed in allowed_roots):
            raise ValueError
    except ValueError:
        allowed = " or ".join(str(p) for p in allowed_roots)
        return None, (
            f"Error: path must stay inside one of the allowed roots ({allowed}). "
            f"Got: {path}"
        )
    except OSError as e:
        return None, f"Error: invalid path '{path}': {e}"

    if must_exist and not resolved.exists():
        return None, f"Error: path not found: {path}"

    if not allow_create and not must_exist:
        pass

    return resolved, None


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        return False
    return path.suffix == "" or len(path.suffix) <= 5


def is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def _is_under_root(resolved: Path, root: Path) -> bool:
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False


def _dedupe_roots(roots: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen = set()
    for root in roots:
        try:
            resolved = Path(root).resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped
