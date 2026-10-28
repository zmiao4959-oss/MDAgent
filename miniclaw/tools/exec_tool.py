"""
在配置的工作区内执行子进程命令。

设计取向（个人会坚持的几点）：
- 模型只提交 command；后端选择 shell，不拆散脚本再重组。
- cwd 必须落在 WORKSPACE_DIR 解析后的目录树下；相对 working_dir 一律相对 workspace。
- 返回给模型的文本要截断，避免巨量 stdout 撑爆上下文。
- 审计日志只打摘要（避免把密钥、token 打进日志）。
- require_approval 仍由注册表标注；真正「拦截到人工确认」应在 Agent 层实现。
"""
from __future__ import annotations

import os
import base64
import ctypes
import re
import subprocess
import signal
import threading
import time
import tempfile
from contextlib import contextmanager
from collections import deque
from pathlib import Path
from typing import Callable, Optional, Tuple

from ..settings import active_workspace_dir, config
from ..logger import get_logger
from .registry import tool_registry
from .lammps_execution import find_lammps_log, is_lammps_command

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

# 仅拦截明显误用；这些检查不是 shell 沙箱。
_DANGEROUS_PROGRAMS = frozenset({"format", "shutdown", "restart", "mkfs"})
_DANGEROUS_REGEXES = (
  re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*\s+)*[/~]", re.I),
  re.compile(r"\bdel\s+/[fq]", re.I),
  re.compile(r"\brmdir\s+/s", re.I),
  re.compile(r"remove-item\s+.*-recurse", re.I),
  re.compile(r"\breg\s+delete\b", re.I),
)
def _interactive_block_message(name: str) -> str:
    return (
        f"Error: Command blocked — `{name}` is an interactive pager/editor and will hang "
        f"or block the agent when run non-interactively. To read part of a file, use the "
        f"`read` tool with `limit` (and optional `offset`), not `{name}` / `type | head`."
    )


def _command_heads(command: str, depth: int = 0):
    """识别未被引号包裹的命令起点，仅用于误用检查，不改变执行内容。"""
    if depth > 4:
        return
    segments = []
    start, quote, index = 0, "", 0
    while index < len(command):
        char = command[index]
        if char == "^" or (char == "\\" and index + 1 < len(command) and command[index + 1] == quote):
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in ('"', "'"):
            quote = char
        elif char in "&|;\r\n":
            segments.append(command[start:index])
            start = index + 1
        index += 1
    segments.append(command[start:])
    for segment in segments:
        match = re.match(r'\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))', segment)
        if not match:
            continue
        executable = next(value for value in match.groups() if value is not None)
        stem = Path(executable.replace("\\", "/")).stem.lower()
        rest = segment[match.end():]
        yield stem, rest
        if stem in _SHELL_EXECUTABLES:
            script = re.search(r'(?:^|\s)(?:/[ck]|-command|-c)\s+(.*)', rest, re.I | re.S)
            if script:
                payload = script.group(1).strip()
                if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in ('"', "'"):
                    payload = payload[1:-1]
                yield from _command_heads(payload, depth + 1)


def _find_dangerous_command(command: str) -> Optional[str]:
    if ":(){ :|:& };:" in command:
        return "fork bomb"
    for stem, rest in _command_heads(command):
        if stem in _DANGEROUS_PROGRAMS:
            return f"blocked executable: {stem}"
        candidate = stem + rest
        if stem == "dd" and re.search(r"\bif=", rest):
            return "blocked executable: dd if="
        for pattern in _DANGEROUS_REGEXES:
            if pattern.search(candidate):
                return f"blocked pattern: {pattern.pattern}"
    return None


def _find_blocked_interactive(command: str) -> Optional[str]:
    for stem, _ in _command_heads(command):
        if stem in _BLOCKED_INTERACTIVE_STEMS:
            return stem
    return None


_SHELL_EXECUTABLES = frozenset({"cmd", "powershell", "pwsh", "bash", "sh"})


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
    cwd = cwd.resolve()
    try:
        cwd.relative_to(workspace_root.resolve())
    except ValueError:
        return None, (
            f"Error: working_dir must be inside the workspace ({workspace_root}). "
            f"Got: {cwd}"
        )
    if not cwd.is_dir():
        return None, f"Error: working_dir must be an existing directory. Got: {cwd}"
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


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """超时后终止子进程（Windows 上 cmd 会拉起 atomsk 等孙进程，需杀进程树）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0 and proc.poll() is None:
                proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception as e:
        logger.warning("Failed to kill process tree pid=%s: %s", proc.pid, e)
        try:
            proc.kill()
        except Exception:
            pass


def _cmd_multiline_arguments(command: str) -> str:
    """将 Python -c 的多行参数编码为单行；cmd 不能原样传递换行参数。"""
    pattern = re.compile(
        r'^\s*(?P<exe>"[^"]+"|\S+)\s+(?P<flags>(?:-\w+\s+)*?)-c\s+'
        r'"(?P<script>(?:\\.|[^"\\])*)"', re.S,
    )
    match = pattern.match(command)
    if match and Path(match.group("exe").strip('"')).stem.lower() in {"python", "pythonw", "py"}:
        if "\n" in match.group("script") or "\r" in match.group("script"):
            # 使用 Windows 参数规则读取 -c 的实际内容，不手动剥离或替换转义。
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            count = ctypes.c_int()
            pointer = shell32.CommandLineToArgvW(match.group(0), ctypes.byref(count))
            if not pointer:
                raise ValueError("Could not parse multiline Python script")
            try:
                arguments = [pointer[index] for index in range(count.value)]
            finally:
                kernel32.LocalFree(pointer)
            script = arguments[arguments.index("-c") + 1]
            encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
            replacement = f"exec(__import__('base64').b64decode('{encoded}'))"
            command = command[:match.start("script")] + replacement + command[match.end("script"):]
    # 其他程序的换行参数不作不可靠的改写，也不能悄悄只执行第一行。
    quoted = False
    for index, char in enumerate(command):
        if char == '"' and (index == 0 or command[index - 1] != "\\"):
            quoted = not quoted
        if quoted and char in "\r\n":
            raise ValueError("cmd cannot pass multiline quoted arguments to this program; write the script to a file first")
    return command


def _prepare_execution(command: str) -> tuple[list[str], list[str] | str]:
    """选择后端解释器，并处理 cmd 无法直接传递的 Python 多行参数。"""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if "\0" in command:
        raise ValueError("command cannot contain NUL characters")
    interpreter = config.execution.shell
    if interpreter == "auto":
        interpreter = "cmd" if os.name == "nt" else "sh"
    if interpreter not in _SHELL_EXECUTABLES:
        raise ValueError("Unsupported configured shell")
    if interpreter == "cmd":
        if os.name != "nt":
            raise ValueError("cmd shell requires Windows")
        executable = os.environ.get("COMSPEC", "cmd.exe")
        command = _cmd_multiline_arguments(command)
        argv = [executable, "/d", "/s", "/c", command]
        # cmd 不使用 CRT 参数转义；只加 /s /c 要求的外层引号。
        launch = f'"{executable}" /d /s /c "{command}"'
    elif interpreter in {"powershell", "pwsh"}:
        argv = [interpreter, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
        launch = argv
    else:
        argv = [interpreter, "-c", command]
        launch = argv
    return argv, launch


@contextmanager
def _shell_script(argv: list[str], launch: list[str] | str):
    """cmd 的多行命令必须作为批处理脚本运行，命令行会截断换行。"""
    script = argv[-1]
    if Path(argv[0]).stem.lower() != "cmd" or not any(char in script for char in "\r\n"):
        yield launch
        return
    with tempfile.TemporaryDirectory(prefix="miniclaw-exec-") as directory:
        path = Path(directory) / "command.cmd"
        text = "@echo off\n@chcp 65001 >nul\n" + script.replace("\r\n", "\n").replace("\r", "\n")
        path.write_bytes((text.rstrip("\n") + "\n").replace("\n", "\r\n").encode("utf-8"))
        yield f'"{argv[0]}" /d /s /c ""{path}""'


class _OutputBuffer:
    """边读取边保留头尾，避免长任务输出无限占用内存。"""

    def __init__(self, limit: int = MAX_RETURN_CHARS):
        self.half = limit // 2
        self.head = ""
        self.tail = ""
        self.total = 0

    def append(self, text: str) -> None:
        self.total += len(text)
        take = min(len(text), self.half - len(self.head))
        self.head += text[:take]
        self.tail = (self.tail + text[take:])[-self.half:]

    def value(self) -> str:
        omitted = self.total - len(self.head) - len(self.tail)
        if omitted > 0:
            return f"{self.head}\n... [{omitted} characters omitted] ...\n{self.tail}"
        return self.head + self.tail


def _run_subprocess(
    argv: list[str] | str,
    *,
    cwd: str,
    timeout_sec: int,
    env: dict,
    on_line: Optional[Callable[[str], None]] = None,
    log_path: Optional[str] = None,
) -> Tuple[int, str, str]:
    """同时消费两个输出流，以进程和管道整体生命周期控制超时。"""
    popen_kwargs = dict(
        args=argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
    )
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(**popen_kwargs)
    deadline = time.monotonic() + timeout_sec
    stop = threading.Event()
    callbacks_done = threading.Event()
    stdout, stderr = _OutputBuffer(), _OutputBuffer()
    # 回调独立于管道读取，慢回调不能堵住子进程；只保留最近的进度。
    events = deque(maxlen=1024)
    callback_ready = threading.Event()

    def notify(line: str) -> None:
        if on_line:
            events.append(line)
            callback_ready.set()

    def dispatch() -> None:
        while not callbacks_done.is_set() or events:
            callback_ready.wait(0.1)
            callback_ready.clear()
            while events:
                try:
                    on_line(events.popleft())
                except Exception:
                    pass

    def read_stream(stream, output: _OutputBuffer, progress: bool) -> None:
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        raw_pending = b""
        # cmd 内置命令向管道写系统 OEM 编码，Python 等程序可以写 UTF-8。
        # 按行判断，兼容同一管道中两者混用，并限制无换行输出的缓冲大小。
        native_encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}" if os.name == "nt" else "utf-8"

        def decode_windows(data: bytes, final: bool) -> str:
            nonlocal raw_pending
            raw_pending += data
            decoded = []
            while raw_pending:
                newline = raw_pending.find(b"\n")
                if newline >= 0:
                    size, complete = newline + 1, True
                elif final:
                    size, complete = len(raw_pending), True
                elif len(raw_pending) >= 65536:
                    size, complete = 65536, False
                else:
                    break
                block = raw_pending[:size]
                try:
                    block_decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
                    value = block_decoder.decode(block, final=complete)
                except UnicodeDecodeError:
                    block_decoder = codecs.getincrementaldecoder(native_encoding)(errors="replace")
                    value = block_decoder.decode(block, final=complete)
                remaining = block_decoder.getstate()[0]
                raw_pending = raw_pending[size - len(remaining):]
                decoded.append(value)
            return "".join(decoded)

        try:
            while True:
                data = stream.read1(4096)
                text = decode_windows(data, final=not data) if os.name == "nt" else decoder.decode(data, final=not data)
                output.append(text)
                if progress and on_line:
                    pending += text
                    lines = pending.split("\n")
                    pending = lines.pop()
                    for line in lines:
                        notify(line.rstrip("\r"))
                    # 无换行的大块输出也不能无限积累。
                    if len(pending) > 4096:
                        notify(pending[:4096])
                        pending = pending[4096:]
                if not data:
                    if pending:
                        notify(pending)
                    break
        except (OSError, ValueError) as exc:
            logger.debug("Output reader stopped: %s", exc)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=read_stream, args=(proc.stdout, stdout, True), daemon=True),
        threading.Thread(target=read_stream, args=(proc.stderr, stderr, False), daemon=True),
    ]
    callback_thread = None
    if on_line:
        callback_thread = threading.Thread(target=dispatch, daemon=True)
        callback_thread.start()
    for reader in readers:
        reader.start()

    log_thread = None
    if log_path and on_line:
        log_file = Path(log_path)
        if not log_file.is_absolute():
            log_file = Path(cwd) / log_file

        def poll_log() -> None:
            offset = 0
            pending = b""
            while True:
                try:
                    if log_file.exists():
                        if log_file.stat().st_size < offset:
                            offset, pending = 0, b""
                        with log_file.open("rb") as fh:
                            fh.seek(offset)
                            # 每次有限读取，文件增长时不能饿死停止检查。
                            data = fh.read(65536)
                            offset = fh.tell()
                        pending += data
                        lines = pending.split(b"\n")
                        pending = lines.pop()
                        for line in lines:
                            notify(line.decode("utf-8", errors="replace").rstrip("\r"))
                        if len(pending) > 65536:
                            pending = pending[-65536:]
                        if stop.is_set() and data:
                            continue
                except OSError:
                    pass
                if stop.wait(0.2):
                    # 最后一次读取进程退出前写入的数据。
                    try:
                        with log_file.open("rb") as fh:
                            fh.seek(offset)
                            data = fh.read(65536)
                        for line in (pending + data).splitlines():
                            notify(line.decode("utf-8", errors="replace"))
                    except OSError:
                        pass
                    break

        log_thread = threading.Thread(target=poll_log, daemon=True)
        log_thread.start()

    try:
        returncode = proc.wait(timeout=max(0, deadline - time.monotonic()))
        for reader in readers:
            reader.join(timeout=max(0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            raise subprocess.TimeoutExpired(argv, timeout_sec)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        for reader in readers:
            reader.join(timeout=1)
        raise subprocess.TimeoutExpired(
            argv, timeout_sec, output=stdout.value(), stderr=stderr.value(),
        ) from None
    finally:
        stop.set()
        callback_ready.set()
        if log_thread:
            log_thread.join(timeout=1)
        callbacks_done.set()
        callback_ready.set()
        if callback_thread:
            callback_thread.join(timeout=1)
    return returncode, stdout.value(), stderr.value()


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
            "file writes; inspect the target file to verify.)"
        )
    return "\n\n".join(chunks).strip()


@tool_registry.register(
    name="execute",
    schema={
        "type": "function",
        "function": {
            "name": "execute",
            "description": (
                "Run a shell command in the workspace using the configured interpreter "
                "(cmd by default on Windows, sh elsewhere). With cmd, "
                "built-ins, pipes, redirection and && work directly. "
                "Use working_dir to choose the directory and read for file content."
            ),
            "parameters": {
                "type": "object",
                "required": ["command"],
                "additionalProperties": False,
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Complete shell command; no cmd /c wrapper is needed."
                        ),
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Directory relative to workspace(the current project's"
                            " directory when a project is active), or absolute path "
                            "that still lies under the workspace."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            f"Timeout in seconds (default {_default_timeout()}, "
                            f"max {_max_timeout()}). Usually omit; recognized LAMMPS runs "
                            "get at least 600s by default, within the configured maximum."
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
    timeout: Optional[int] = None,
    **kwargs,
) -> str:
    on_line: Optional[Callable[[str], None]] = kwargs.pop("_on_line", None)
    # 注册表传递上下文，runtime 注入进度回调；二者不是工具业务参数。
    execution_context = kwargs.pop("_context", None) or {}
    if kwargs:
        return "Error: Unexpected execute argument(s): " + ", ".join(sorted(kwargs))
    if on_line is not None and not callable(on_line):
        return "Error: _on_line must be callable"
    if working_dir is not None and not isinstance(working_dir, str):
        return "Error: working_dir must be a string"

    workspace_root = active_workspace_dir()
    cwd, err = _resolve_cwd(workspace_root, working_dir)
    if err:
        return err

    try:
        argv, launch = _prepare_execution(command)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: could not parse command: {e}"

    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1):
        return "Error: timeout must be a positive integer in seconds"
    if timeout is None:
        timeout = _default_timeout()
        if is_lammps_command(command):
            timeout = max(timeout, 600)
    timeout_sec = max(MIN_TIMEOUT_SEC, min(timeout, _max_timeout()))

    logger.info("execute: cwd=%s executable=%s", cwd, Path(argv[0]).name)
    dangerous = _find_dangerous_command(command)
    if dangerous:
        logger.warning("execute blocked dangerous command: %s", dangerous)
        return (
            f"Error: Command blocked for safety ({dangerous}). "
            "Destructive or system-wide operations are not allowed."
        )

    blocked = _find_blocked_interactive(command)
    if blocked:
        logger.warning("execute blocked interactive command: %s", blocked)
        return _interactive_block_message(blocked)

    try:
        log_path = find_lammps_log(command) if on_line else None
        run_env = os.environ.copy()
        if execution_context.get("python_no_bytecode"):
            run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        # 子进程内 Python 写管道时默认用系统编码（如 GBK），含 ® 等字符会 UnicodeEncodeError；
        # 父进程 subprocess 的 encoding= 只影响「读」字节，不能修复子进程写 stdout 时的编码。
        if os.name == "nt":
            run_env.setdefault("PYTHONIOENCODING", "utf-8")

        with _shell_script(argv, launch) as executable_command:
            returncode, stdout, stderr = _run_subprocess(
                executable_command,
                cwd=str(cwd),
                timeout_sec=timeout_sec,
                env=run_env,
                on_line=on_line,
                log_path=log_path,
            )
    except subprocess.TimeoutExpired as exc:
        message = (
            f"Error: Command timed out after {timeout_sec} seconds. "
            "(If the command works in PowerShell but hangs here, it may need a longer "
            f"timeout parameter, up to {_max_timeout()}s.)"
        )
        if exc.output or exc.stderr:
            message += "\n\n" + _format_run_result(0, exc.output or "", exc.stderr or "")
        return message
    except FileNotFoundError:
        return "Error: Executable not found. Check PATH or use an explicit path to the program."
    except Exception as e:
        return f"Error executing command: {e}"

    return _format_run_result(returncode, stdout, stderr)
