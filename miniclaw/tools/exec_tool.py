"""
在配置的工作区内执行子进程命令。

设计取向（个人会坚持的几点）：
- 默认不用 shell：用 argv 列表跑 subprocess，减少一层注入与解析歧义。
- cwd 必须落在 WORKSPACE_DIR 解析后的目录树下；相对 working_dir 一律相对 workspace。
- 返回给模型的文本要截断，避免巨量 stdout 撑爆上下文。
- 审计日志只打摘要（避免把密钥、token 打进日志）。
- require_approval 仍由注册表标注；真正「拦截到人工确认」应在 Agent 层实现。
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from ..config import active_workspace_dir, config
from ..logger import get_logger
from .registry import tool_registry

logger = get_logger(__name__)

MIN_TIMEOUT_SEC = 1

def _max_timeout() -> int:
    return config.execution.max_timeout_sec

def _default_timeout() -> int:
    return config.execution.default_timeout_sec
# 单轮工具返回给模型的上限（字符）；再大只保留头尾便于排错
MAX_RETURN_CHARS = 24_000

# 交互式分页/编辑器：在自动化子进程里会卡住或等待按键
_BLOCKED_INTERACTIVE_STEMS = frozenset({"more", "less", "edit"})

# 明显破坏性/系统级命令（子串匹配，降低误拦）
_DANGEROUS_SUBSTRINGS = (
    "format ",
    "shutdown",
    "restart",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # fork bomb
)
_DANGEROUS_REGEXES = (
  re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*\s+)*[/~]", re.I),
  re.compile(r"\bdel\s+/[fq]", re.I),
  re.compile(r"\brmdir\s+/s", re.I),
  re.compile(r"remove-item\s+.*-recurse", re.I),
  re.compile(r"\breg\s+delete\b", re.I),
)
_WINDOWS_SHELL_BUILTINS = frozenset(
    {
        "dir",
        "copy",
        "move",
        "del",
        "erase",
        "ren",
        "rename",
        "type",
        "set",
        "cls",
        "md",
        "mkdir",
        "rd",
        "rmdir",
    }
)


def _interactive_block_message(name: str) -> str:
    return (
        f"Error: Command blocked — `{name}` is an interactive pager/editor and will hang "
        f"or block the agent when run non-interactively. To read part of a file, use the "
        f"`read` tool with `limit` (and optional `offset`), not `{name}` / `type | head`."
    )


def _find_dangerous_command(command: str) -> Optional[str]:
    """检测明显破坏性命令，返回命中原因简述。"""
    lower = (command or "").lower()
    for sub in _DANGEROUS_SUBSTRINGS:
        if sub in lower:
            return f"blocked pattern: {sub!r}"
    for rx in _DANGEROUS_REGEXES:
        if rx.search(command or ""):
            return f"blocked pattern: {rx.pattern}"
    return None


def _find_blocked_interactive(command: str, argv: list[str]) -> Optional[str]:
    """若命令含 more/less/edit（含管道），返回被拦程序名。"""
    if os.name == "nt":
        argv = _normalize_windows_argv_quotes(list(argv))

    lower = (command or "").lower()
    pipe_hit = re.search(r"\|\s*(more|less)\b", lower)
    if pipe_hit:
        return pipe_hit.group(1)

    word_hit = re.search(r"\b(more|less|edit)\b", lower, re.I)
    if word_hit:
        return word_hit.group(1).lower()

    for tok in argv:
        stem = Path(tok).stem.lower()
        if stem in _BLOCKED_INTERACTIVE_STEMS:
            return stem
        if tok.lower() in _BLOCKED_INTERACTIVE_STEMS:
            return tok.lower()

    # cmd /c "more path" 等整段落在单个参数里
    for tok in argv:
        for name in _BLOCKED_INTERACTIVE_STEMS:
            if re.search(rf"(?:^|\s){re.escape(name)}(?:\s|\.exe|\b)", tok, re.I):
                return name
    return None


# shell=False 下不会被解释的操作符；出现在独立 token 时说明用户依赖了 shell 功能
_SHELL_OP_TOKENS = frozenset({"&&", "||", "|"})


def _needs_shell_error(command: str, argv: list[str]) -> Optional[str]:
    """Return an actionable error for shell syntax that shell=False cannot run.

    IMPORTANT: 检查对象是 shlex 解析后的 argv 独立 token，而非 raw command string。
    这样引号内的多命令（如 ssh hpc "cmd1 && cmd2"）会被正确放行，
    因为 shlex 已将其合并为单个 token，不作为本地 shell 操作符处理。
    """
    stripped = (command or "").strip()
    if not stripped:
        return None

    first = Path(argv[0]).stem.lower() if argv else ""
    if first in {"cmd", "powershell", "pwsh", "bash", "sh"} | _PASSTHROUGH_EXECUTABLES:
        return None

    if os.name == "nt" and first in _WINDOWS_SHELL_BUILTINS:
        return (
            f"Error: `{argv[0]}` is a Windows shell built-in. Use `cmd /c {stripped}` "
            "or call a real executable."
        )

    # 检查 argv 中的独立 shell 操作符 token（而非 raw string），
    # 避免误杀引号内的 && / || / |（那些是传给目标程序的参数，无需本地 shell）
    for tok in argv:
        if tok in _SHELL_OP_TOKENS:
            return (
                "Error: shell operators detected, but `execute` runs with shell disabled. "
                "Wrap the command with `cmd /c ...` on Windows, or call a single executable "
                "with explicit arguments."
            )
    return None


def _resolve_cwd(
    workspace_root: Path,
    working_dir: Optional[str],
) -> Tuple[Optional[Path], Optional[str]]:
    """解析并校验 cwd，必须位于 workspace_root 之下。"""
    if working_dir:
        cwd = Path(working_dir)
        if not cwd.is_absolute():
            cwd = workspace_root / cwd
    else:
        cwd = workspace_root
    cwd = cwd.resolve()# 把路径规范化，防止通过".."骗人上二楼
    try:
        cwd.relative_to(workspace_root)# 核心：看看最终目标是不是在安全圈内
    except ValueError:
        return None, (
            f"Error: working_dir must be inside the workspace ({workspace_root}). "
            f"Got: {cwd}"
        )
    return cwd, None


def _truncate_block(label: str, text: str, budget: int) -> str:
    """
    命令执行结果简化，超过界限就只要开头与结尾
    """
    if len(text) <= budget:
        return f"{label}\n{text.rstrip()}"
    head = budget // 2
    tail = budget - head
    omitted = len(text) - head - tail
    return (
        f"{label}\n"
        f"{text[:head].rstrip()}\n"
        f"\n... [{omitted} characters omitted] ...\n\n"
        f"{text[-tail:].lstrip()}"
    )


def _strip_outer_quotes(s: str) -> str:
    """去掉 shlex 在 Windows 上常留下的外层引号，并还原 \\\" / \\' 转义。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        inner = s[1:-1]
        if inner.strip():
            return inner.replace('\\"', '"').replace("\\'", "'")
    return s


def _normalize_windows_script_argv(argv: list[str]) -> list[str]:
    """
    Windows + shlex.split(posix=False) 时，`-c` / `-Command` 后的脚本常仍带外层双引号。
    对 python.exe：会把 `"print(1)"` 当成字面脚本，进程 exit 0 但无任何 STDOUT/STDERR，
    工具侧就会显示 (command produced no output)，模型误以为命令成功却无信息。

    对 powershell：外层引号会导致只打印脚本文本而不执行。
    """
    if os.name != "nt" or len(argv) < 3:
        return argv

    stem = Path(argv[0]).stem.lower()
    script_hosts = {
        "python",
        "pythonw",
        "py",
        "powershell",
        "pwsh",
    }
    if stem not in script_hosts:
        return argv

    out = list(argv)
    script_flags = {"-c", "-command"}
    for i in range(len(out) - 1):
        if out[i].lower() not in script_flags:
            continue
        out[i + 1] = _strip_outer_quotes(out[i + 1])
        break
    return out


def _normalize_windows_argv_quotes(argv: list[str]) -> list[str]:
    """Windows 上 shlex 常把整段路径留在带字面量引号的 argv 里，统一剥离。"""
    if os.name != "nt":
        return argv
    return [_strip_outer_quotes(a) for a in argv]


def _normalize_cmd_c_argv(argv: list[str]) -> list[str]:
    """
    Windows 上 shlex 会把引号路径拆成带字面量 `"` 的 argv 段，例如
    `'"C:\\Users\\...\\file.lmp"'`，cmd 会报「文件名、目录名或卷标语法不正确」。

    另：路径以 `\\` 结尾再跟引号（`"...\\test\\"`）在 cmd 里会转义闭合引号，也应去掉尾部 `\\`。
    """
    if os.name != "nt" or len(argv) < 4:
        return argv
    if argv[0].lower() != "cmd" or argv[1].lower() != "/c":
        return argv

    out = list(argv)
    for i in range(3, len(out)):
        s = _strip_outer_quotes(out[i])
        # 仅对形如盘符路径去掉尾部单个反斜杠（避免 copy 目标目录 `\"` 问题）
        if (
            len(s) > 3
            and s[1] == ":"
            and s.endswith("\\")
            and not s.endswith("\\\\")
        ):
            s = s.rstrip("\\")
        out[i] = s
    return out


def _drop_shell_only_tail_argv(argv: list[str]) -> list[str]:
    """
    shell=False 时 `2>&1`、`| more` 等不会生效，只会被当成多余 argv（python 常忽略）。
    去掉尾部常见的 shell 重定向 token，避免模型以为 stderr 已合并。
    """
    if os.name != "nt":
        return argv
    tail_markers = ("2>&1", "2>&2", ">&2", "1>&2")
    out = list(argv)
    while len(out) > 1 and out[-1].strip() in tail_markers:
        out.pop()
    return out


# ssh / scp 等把尾部的远程命令原样传递，内部可以出现 && / || / | / 嵌套引号，
# 这些字符不需要本地 shell 解释。Windows 的 shlex(posix=False) 不认识 \",
# 碰到 POSIX 风格的嵌套双引号会把 argv 拆碎。对这类命令，直接用原始字符串提取
# 远程命令部分，绕开 shlex 的引号解析。
_PASSTHROUGH_EXECUTABLES = frozenset({"ssh", "scp"})


def _build_argv(command: str) -> list[str]:
    """把用户输入拆成 argv；Windows 使用非 POSIX 规则以兼容常见引号写法。"""
    command = (command or "").strip()
    if not command:
        raise ValueError("command is empty")

    posix = subprocess.os.name != "nt"
    argv = shlex.split(command, posix=posix)

    # ssh / scp：检测 argv 是否被嵌套引号拆碎了，如果是就从原始字符串重建
    first = Path(argv[0]).stem.lower() if argv else ""
    if first in _PASSTHROUGH_EXECUTABLES and len(argv) >= 3:
        # 找 host 在 argv 中的位置（跳过可执行文件名和 flags）
        i = 1
        while i < len(argv):
            if argv[i].startswith("-"):
                i += 1
                # 跳过 flag 的值（如下一个 token 不以 - 开头）
                if i < len(argv) and not argv[i].startswith("-"):
                    i += 1
            else:
                break

        if i < len(argv) - 1:
            # 从原始 command 字符串中定位 host，提取尾部的远程命令
            host_str = argv[i]
            host_pos = command.find(host_str)
            if host_pos >= 0:
                rest_start = host_pos + len(host_str)
                while rest_start < len(command) and command[rest_start].isspace():
                    rest_start += 1
                remote = command[rest_start:].strip()
                remote = _strip_outer_quotes(remote)
                argv = argv[: i + 1] + [remote]

    return argv


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """超时后终止子进程（Windows 上 cmd 会拉起 atomsk 等孙进程，需杀进程树）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            proc.kill()
    except Exception as e:
        logger.warning("Failed to kill process tree pid=%s: %s", proc.pid, e)
        try:
            proc.kill()
        except Exception:
            pass


def _run_subprocess(
    argv: list[str],
    *,
    cwd: str,
    timeout_sec: int,
    env: dict,
    on_line: Optional[Callable[[str], None]] = None,
    log_path: Optional[str] = None,
) -> Tuple[int, str, str]:
    """运行子进程并捕获输出。

    on_line 不为 None 时，用独立线程逐行读取 stdout，每行立即回调。
    log_path 不为 None 时，额外启动一个 daemon 线程轮询日志文件末尾，
    适用于 LAMMPS -l logfile 场景（thermo 写入文件而非 stdout）。
    """
    popen_kwargs: dict = {
        "args": argv,
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    proc = subprocess.Popen(**popen_kwargs)

    stdout_lines: list[str] = []
    _reader_error: Optional[Exception] = None

    def _read_stdout() -> None:
        nonlocal _reader_error
        try:
            for line in proc.stdout:
                line = line.rstrip("\n").rstrip("\r")
                stdout_lines.append(line)
                if on_line:
                    try:
                        on_line(line)
                    except Exception:
                        pass  # 回调异常不中断读取
        except Exception as exc:
            _reader_error = exc

    # 日志文件轮询线程（LAMMPS -l 场景）
    _log_thread: Optional[threading.Thread] = None
    if log_path and on_line:
        _log_file = Path(log_path)
        if not _log_file.is_absolute():
            _log_file = Path(cwd) / log_path
        _log_file = _log_file.resolve()

        def _poll_log() -> None:
            last_step = -1
            last_size = 0
            while proc.poll() is None:
                try:
                    if _log_file.exists():
                        size = _log_file.stat().st_size
                        if size > last_size:
                            # 只读新增部分（文件尾部）
                            with open(_log_file, "r", encoding="utf-8", errors="replace") as fh:
                                if last_size > 0:
                                    fh.seek(last_size)
                                for line in fh:
                                    line = line.rstrip("\n").rstrip("\r")
                                    if line and on_line:
                                        try:
                                            on_line(line)
                                        except Exception:
                                            pass
                            last_size = size
                except Exception:
                    pass
                time.sleep(1.5)  # LAMMPS thermo 通常每 100 步输出一次

        _log_thread = threading.Thread(target=_poll_log, daemon=True)
        _log_thread.start()

    if on_line:
        reader_thread = threading.Thread(target=_read_stdout, daemon=True)
        reader_thread.start()
        try:
            reader_thread.join(timeout=timeout_sec)
            if reader_thread.is_alive():
                _kill_process_tree(proc)
                raise subprocess.TimeoutExpired(argv, timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                reader_thread.join(timeout=5)
            except Exception:
                pass
            raise
        if _reader_error:
            logger.warning("stdout reader error: %s", _reader_error)
        # 等日志轮询线程自然结束（进程已退出，proc.poll() 返回非 None）
        if _log_thread is not None:
            _log_thread.join(timeout=3)
        stderr_data = ""
        try:
            _, stderr_data = proc.communicate(timeout=5)
        except Exception:
            try:
                stderr_data = proc.stderr.read() or ""
            except Exception:
                pass
        returncode = proc.returncode or 0
        return returncode, "\n".join(stdout_lines), stderr_data or ""
    else:
        # 无回调时沿用原来的 communicate 路径
        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stdout, stderr = "", ""
            raise
        return proc.returncode or 0, stdout or "", stderr or ""


def _format_run_result(
    returncode: int,
    stdout: str,
    stderr: str,
    max_chars: int = MAX_RETURN_CHARS,
) -> str:
    chunks: list[str] = []
    # 修饰命令执行输出
    if returncode != 0:
        chunks.append(f"Exit code: {returncode}")

    # stdout / stderr 各分一半预算，避免一边占满
    half = max(512, max_chars // 2 - 32)
    if stdout.strip():
        chunks.append(_truncate_block("STDOUT:", stdout, half))
    if stderr.strip():
        chunks.append(_truncate_block("STDERR:", stderr, half))
    if not chunks or (not stdout.strip() and not stderr.strip() and returncode == 0):
        chunks.append(
            "(command succeeded with no stdout/stderr — normal for silent steps like "
            "shutil.copy or file writes; run `cmd /c dir <path>` to verify.)"
        )
    return "\n\n".join(chunks).strip()


@tool_registry.register(
    name="execute",
    description=(
        "Run a subprocess in the workspace (parsed to argv, no shell). "
        "See AGENTS.md / USER.md for Windows rules and LAMMPS paths."
    ),
    schema={
        "type": "function",
        "function": {
            "name": "execute",
            "description": (
                "Run a command from the workspace (optional working_dir). "
                "NO shell: the string is split into argv[0]+args only. "
                "Windows rules: "
                "(1) NEVER `cd dir && program` — use `cmd /c cd /d dir && C:\\full\\path\\program.exe ...` "
                "or call program.exe with absolute -in/-l paths. "
                "(2) NEVER rely on PATH for mpiexec/lmp/copy/dir — use absolute .exe paths from USER.md. "
                "(3) NO more/less/edit or `type | head` — use `read` with limit. "
                "(4) LAMMPS/MPI runs: set timeout>=600. "
                "(5) Built-ins: `cmd /c dir`, `cmd /c copy`, not bare `dir`/`copy`."
            ),
            "parameters": {
                "type": "object",
                "required": ["command"],
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Single command line (parsed to argv). Examples: "
                            "'cmd /c dir C:\\path', "
                            "'C:\\...\\mpiexec.exe -np 16 \"C:\\...\\lmp.exe\" -in C:\\...\\in.test -l C:\\...\\log.lammps'. "
                            "Forbidden: 'cd X && mpiexec ...' without leading cmd /c."
                        ),
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Directory relative to workspace root, or absolute path "
                            "that still lies under the workspace."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            f"Timeout in seconds (default {_default_timeout()}, "
                            f"max {_max_timeout()})."
                        ),
                    },
                },
            },
        },
    },
    require_approval=True,
    risk_level="high",
    tags=["execution", "shell"],
)
def execute_tool(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = _default_timeout(),
    **kwargs,
) -> str:
    on_line: Optional[Callable[[str], None]] = kwargs.pop("_on_line", None)
    if kwargs:
        logger.debug("Ignoring unexpected execute_tool kwargs: %s", sorted(kwargs.keys()))

    # 从命令中解析 LAMMPS -l logfile 路径（用于轮询日志进度）
    log_path: Optional[str] = None
    if on_line:
        m = re.search(r'(?:^|\s)-l\s+(?:"([^"]+)"|(\S+))', command)
        if m:
            log_path = (m.group(1) or m.group(2))

    workspace_root = active_workspace_dir()
    cwd, err = _resolve_cwd(workspace_root, working_dir)
    if err:
        return err

    try:
        timeout_sec = max(MIN_TIMEOUT_SEC, min(int(timeout), _max_timeout()))
    except (TypeError, ValueError):
        timeout_sec = _default_timeout()

    preview = (command or "").replace("\n", " ")[:120]
    logger.info("execute: cwd=%s cmd_preview=%r", cwd, preview)

    try:
        argv = _build_argv(command)
        argv = _normalize_windows_argv_quotes(argv)
        argv = _normalize_cmd_c_argv(argv)
        argv = _normalize_windows_script_argv(argv)
        argv = _drop_shell_only_tail_argv(argv)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: could not parse command: {e}"

    dangerous = _find_dangerous_command(command)
    if dangerous:
        logger.warning("execute blocked dangerous command: %s", dangerous)
        return (
            f"Error: Command blocked for safety ({dangerous}). "
            "Destructive or system-wide operations are not allowed."
        )

    blocked = _find_blocked_interactive(command, argv)
    if blocked:
        logger.warning("execute blocked interactive command: %s", blocked)
        return _interactive_block_message(blocked)

    shell_needed = _needs_shell_error(command, argv)
    if shell_needed:
        logger.warning("execute rejected shell-only syntax: %s", shell_needed)
        return shell_needed

    try:
        run_env = os.environ.copy()
        # 子进程内 Python 写管道时默认用系统编码（如 GBK），含 ® 等字符会 UnicodeEncodeError；
        # 父进程 subprocess 的 encoding= 只影响「读」字节，不能修复子进程写 stdout 时的编码。
        if os.name == "nt":
            run_env.setdefault("PYTHONIOENCODING", "utf-8")

        returncode, stdout, stderr = _run_subprocess(
            argv,
            cwd=str(cwd),
            timeout_sec=timeout_sec,
            env=run_env,
            on_line=on_line,
            log_path=log_path,
        )
    except subprocess.TimeoutExpired:
        return (
            f"Error: Command timed out after {timeout_sec} seconds. "
            "(If the command works in PowerShell but hangs here, it may need a longer "
            f"timeout parameter, up to {_max_timeout()}s.)"
        )
    except FileNotFoundError:
        return "Error: Executable not found. Check PATH or use an explicit path to the program."
    except Exception as e:
        return f"Error executing command: {e}"

    return _format_run_result(returncode, stdout, stderr)
