"""只识别直接执行或 MPI 启动的 LAMMPS 命令，不推断任意脚本。"""
from pathlib import PureWindowsPath
import re
import shlex


def _lammps_arguments(command: str) -> list[str] | None:
    try:
        raw_tokens = shlex.split(command, posix=False)
    except (ValueError, TypeError):
        return None
    # 组合脚本可能改变 cwd；无法确定路径时不猜测日志或输入文件。
    if not raw_tokens or any(
        re.search(r"[&|;<>\r\n]", token) and not (token[0] == token[-1] and token[0] in ('"', "'"))
        for token in raw_tokens
    ):
        return None
    tokens = [token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'") else token for token in raw_tokens]
    if PureWindowsPath(tokens[0]).stem.lower() == "cmd":
        try:
            tokens = tokens[tokens.index("/c") + 1:]
        except ValueError:
            return None
    if not tokens:
        return None
    first = PureWindowsPath(tokens[0]).stem.lower()
    if re.fullmatch(r"(?:lmp(?:[_-].*)?|lammps)", first):
        return tokens[1:]
    if first in {"mpiexec", "mpirun"}:
        for index, token in enumerate(tokens[1:], 1):
            if re.fullmatch(r"(?:lmp(?:[_-].*)?|lammps)", PureWindowsPath(token).stem.lower()):
                return tokens[index + 1:]
    return None


def is_lammps_command(command: str) -> bool:
    return _lammps_arguments(command) is not None


def find_lammps_input(command: str) -> str | None:
    args = _lammps_arguments(command)
    if args is not None:
        for index, token in enumerate(args[:-1]):
            if token == "-in":
                return args[index + 1]
    return None


def find_lammps_log(command: str) -> str | None:
    args = _lammps_arguments(command)
    if args is None:
        return None
    for index, token in enumerate(args[:-1]):
        if token == "-l":
            return None if args[index + 1] == "none" else args[index + 1]
    return "log.lammps"
