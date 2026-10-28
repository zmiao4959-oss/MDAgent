"""Read-only process probes used to recover interrupted local simulations."""
from __future__ import annotations

import json
import ntpath
import subprocess
from pathlib import Path

import psutil


def worker_is_alive(workdir: Path, pid, birth=None) -> bool | None:
    """Check ownership, not just PID existence (Windows reuses PIDs)."""
    if not pid:
        return None
    try:
        process = psutil.Process(int(pid))
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        if birth is not None and abs(process.create_time() - birth) >= 0.01:
            return False
        argv = process.cmdline()
        return (any(Path(arg).name == "local_worker.py" for arg in argv)
                and any(Path(arg).resolve() == (workdir / "launch.json").resolve() for arg in argv))
    except (psutil.NoSuchProcess, ValueError):
        return False
    except (psutil.AccessDenied, OSError):
        return None


# Run only a read-only /proc scan, never the simulation executable. Arguments
# are passed separately, so spaces and shell characters in paths stay literal.
# An unreadable matching process or failed probe must retain the reservation.
_WSL_SCAN = r'''
target=$(readlink -f -- "$1") || exit 3
[ -n "$target" ] || exit 3
name=${1##*/}
uncertain=0
for proc in /proc/[0-9]*; do
    exe=$(readlink "$proc/exe" 2>/dev/null)
    if [ -z "$exe" ]; then
        [ -d "$proc" ] || continue
        comm=$(cat "$proc/comm" 2>/dev/null) || { uncertain=1; continue; }
        if [ "$comm" = "$name" ]; then
            # Zombies have no executable and cannot occupy a GPU slot.
            grep -q '^State:[[:space:]]*Z' "$proc/status" 2>/dev/null || {
                [ ! -d "$proc" ] || uncertain=1
            }
        fi
        continue
    fi
    case "$exe" in
        "$target"|"$target (deleted)")
            cwd=$(readlink "$proc/cwd" 2>/dev/null) || {
                [ ! -d "$proc" ] || uncertain=1
                continue
            }
            host=$(wslpath -w "$cwd" 2>/dev/null) || { uncertain=1; continue; }
            printf '%s\000' "$host"
            ;;
    esac
done
[ "$uncertain" = 0 ] || exit 3
printf '\000MINICLAW_PROBE_OK\000'
'''


def _wsl_workdirs(argv: list[str], probes: dict) -> set[str] | None:
    try:
        distribution = argv[argv.index("-d") + 1]
        executable = argv[-1]
    except (ValueError, IndexError):
        return None
    if not distribution or not executable.startswith("/"):
        return None
    key = (distribution, executable)
    if key not in probes:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [argv[0], "-d", distribution, "--exec", "/bin/sh", "-c",
                 _WSL_SCAN, "miniclaw-process-probe", executable],
                capture_output=True, timeout=10, creationflags=flags,
            )
            suffix = b"\0MINICLAW_PROBE_OK\0"
            if result.returncode != 0 or not result.stdout.endswith(suffix):
                probes[key] = None
            else:
                paths = result.stdout[:-len(suffix)].decode("utf-8").split("\0")
                probes[key] = {ntpath.normcase(ntpath.normpath(p)) for p in paths if p}
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            probes[key] = None
    return probes[key]


def child_is_alive(workdir: Path, probes: dict | None = None) -> bool | None:
    """True=live child, False=confirmed absent, None=insufficient evidence."""
    try:
        settings = json.loads((workdir / "launch.json").read_text(encoding="utf-8"))
        argv = settings["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            return None
        child_file = workdir / ".miniclaw-child.json"
        if child_file.is_file():
            child = json.loads(child_file.read_text(encoding="utf-8"))
            try:
                process = psutil.Process(int(child["pid"]))
                if child.get("birth") is None:
                    return None
                if (process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                        and abs(process.create_time() - child["birth"]) < 0.01):
                    return True  # Includes a WSL launcher still starting its Linux child.
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                return None
            if ntpath.basename(argv[0]).lower() not in {"wsl", "wsl.exe"}:
                return False
        if ntpath.basename(argv[0]).lower() not in {"wsl", "wsl.exe"}:
            return None  # Pre-upgrade native children have no durable identity.
        # A pre-upgrade WSL launcher can outlive its Python worker even before
        # GPUMD appears in /proc. Match its complete argv before probing Linux.
        if not child_file.is_file():
            for process in psutil.process_iter(["name"]):
                if str(process.info.get("name", "")).lower() != ntpath.basename(argv[0]).lower():
                    continue
                try:
                    command = process.cmdline()
                    if (command and command[1:] == argv[1:]
                            and ntpath.basename(command[0]).lower() == ntpath.basename(argv[0]).lower()
                            and process.status() != psutil.STATUS_ZOMBIE):
                        return True
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    return None
        live = _wsl_workdirs(argv, probes if probes is not None else {})
        if live is None:
            return None
        cwd = settings.get("working_dir") or argv[argv.index("--cd") + 1]
        return ntpath.normcase(ntpath.normpath(str(cwd))) in live
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
