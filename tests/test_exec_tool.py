import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from miniclaw.tools import exec_tool
from miniclaw.tools.exec_tool import execute_tool, _run_subprocess
from miniclaw.tools.lammps_execution import find_lammps_log
from miniclaw.tools.registry import tool_registry


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(exec_tool, "active_workspace_dir", lambda: tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_execute_runs_windows_shell_builtins_without_wrapper():
    result = execute_tool("dir C:\\")

    assert "Error:" not in result
    assert "STDOUT:" in result


def test_execute_runs_shell_operators_without_wrapper():
    result = execute_tool("echo first && echo second")

    assert "first" in result and "second" in result
    assert "Error:" not in result


def test_execute_allows_shell_operators_inside_quoted_args(monkeypatch):
    """引号内的 && / || / | 是传给目标程序的参数，不应拦截。"""
    monkeypatch.setattr(exec_tool, "_run_subprocess", lambda *a, **k: (0, "ok", ""))
    result = execute_tool("ssh hpc \"bash -lc 'mkdir -p dir && cp file dir/ && ls dir/'\"")
    # 不应报 shell operators 错误（可能报 ssh 找不到等，但不是拦截）
    assert "shell operators detected" not in result


def test_execute_allows_nested_posix_quotes_in_ssh_remote_command(monkeypatch):
    """ssh 远程命令中的 POSIX 嵌套双引号（\"）不应被拦截。"""
    monkeypatch.setattr(exec_tool, "_run_subprocess", lambda *a, **k: (0, "ok", ""))
    result = execute_tool(
        "ssh hpc \"bash -lc 'bhosts | awk \\\"NR>1 && \\$2==\\\\\\\"ok\\\\\\\"\\\" | sort -k4,4nr'\""
    )
    assert "shell operators detected" not in result


def test_public_command_preserves_empty_and_space_arguments(tmp_path):
    import json
    script = tmp_path / "arguments.py"
    script.write_text("import sys,json;print(json.dumps(sys.argv[1:]))", encoding="utf-8")
    result = execute_tool(f'"{sys.executable}" "{script}" "" "argument with spaces"')
    assert json.loads(result.split("STDOUT:\n", 1)[1]) == ["", "argument with spaces"]


def test_script_filename_is_not_blocked(tmp_path):
    script = tmp_path / "edit.py"
    script.write_text("print('script executed')", encoding="utf-8")
    assert "script executed" in execute_tool(f'"{sys.executable}" "{script}"')


@pytest.mark.parametrize("command", [None, "", "   ", 123, "echo\0bad"])
def test_invalid_commands_return_errors(command):
    assert execute_tool(command).startswith("Error:")


@pytest.mark.parametrize("name,value", [
    ("program", "echo"), ("args", []), ("shell", "cmd"), ("log_path", "progress.log"),
])
def test_removed_parameters_are_rejected_directly_and_by_registry(name, value):
    assert "Unexpected" in execute_tool("echo hi", **{name: value})
    result = asyncio.run(tool_registry.execute("execute", {"command": "echo hi", name: value}))
    assert "Unexpected" in result


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
@pytest.mark.parametrize("legacy", [True, False])
def test_cmd_redirection_and_quoted_paths_are_preserved(tmp_path, legacy):
    target = tmp_path / "output with spaces.txt"
    script = f'echo hello > "{target}" 2>&1'
    result = execute_tool("cmd /c " + script) if legacy else execute_tool(script)
    assert "Error:" not in result
    assert target.read_text().strip() == "hello"


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_cmd_runs_quoted_executable_with_quoted_arguments(tmp_path):
    folder = tmp_path / "folder with spaces"
    folder.mkdir()
    script = folder / "test.py"
    script.write_text("import sys;print(sys.argv[1])", encoding="utf-8")
    payload = f'"{sys.executable}" "{script}" "argument with spaces"'
    assert "argument with spaces" in execute_tool(payload)


def test_redirection_without_shell_wrapper_is_supported():
    result = execute_tool(f'"{sys.executable}" -c "print(1)" 2>&1')
    assert "STDOUT:\n1" in result


@pytest.mark.parametrize("streaming", [True, False])
def test_large_stderr_does_not_block_stdout(tmp_path, streaming):
    events = []
    rc, out, err = _run_subprocess(
        [sys.executable, "-c", "import sys;sys.stderr.write('x'*200000);print('finished')"],
        cwd=str(tmp_path), timeout_sec=5, env=os.environ.copy(),
        on_line=events.append if streaming else None,
    )
    assert rc == 0 and "finished" in out
    assert "characters omitted" in err and len(err) < 25000
    if streaming:
        assert "finished" in events


def test_timeout_covers_process_after_stdout_closes(tmp_path):
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_subprocess(
            [sys.executable, "-c", "import os,time;os.close(1);time.sleep(30)"],
            cwd=str(tmp_path), timeout_sec=1, env=os.environ.copy(), on_line=lambda line: None,
        )
    assert time.monotonic() - start < 8


def test_timeout_preserves_partial_output():
    result = execute_tool(f'"{sys.executable}" -u -c "import time;print(\'started\');time.sleep(30)"', timeout=1)
    assert "timed out" in result and "started" in result


def test_nonzero_exit_is_preserved(tmp_path):
    rc, out, err = _run_subprocess(
        [sys.executable, "-c", "import sys;print('failed',file=sys.stderr);sys.exit(7)"],
        cwd=str(tmp_path), timeout_sec=5, env=os.environ.copy(), on_line=lambda line: None,
    )
    assert rc == 7 and "failed" in err


def test_callback_exception_does_not_abort_execution():
    def broken(line):
        raise RuntimeError("callback failure")
    result = execute_tool(f'"{sys.executable}" -c "print(\'ok\')"', _on_line=broken)
    assert "ok" in result


def test_progress_log_reads_final_unicode_line(tmp_path):
    # 低层进程测试验证日志读取；工具入口没有可指定的日志参数。
    events = []
    script = tmp_path / "write_log.py"
    script.write_text("from pathlib import Path;Path('progress.log').write_text('最终进度\\n',encoding='utf-8')", encoding="utf-8")
    rc, _, _ = _run_subprocess(
        [sys.executable, str(script)], cwd=str(tmp_path), timeout_sec=5,
        env=os.environ.copy(), log_path="progress.log", on_line=events.append,
    )
    assert rc == 0 and "最终进度" in events


def test_lammps_adapter_does_not_infer_other_programs_log():
    assert find_lammps_log("ssh -l user host") is None
    assert find_lammps_log('mpiexec.exe -np 2 lmp.exe -l "log file"') == "log file"
    assert find_lammps_log("lmp.exe -l none") is None


def test_scp_keeps_separate_source_and_destination():
    command = 'scp "source with spaces" host:destination'
    argv, _ = exec_tool._prepare_execution(command)
    assert argv[-1] == command


def test_ssh_boolean_option_keeps_host_and_remote_command():
    command = 'ssh -T host "echo first && echo second"'
    argv, _ = exec_tool._prepare_execution(command)
    assert argv[-1] == command


def test_registry_exposes_only_simple_command_interface():
    parameters = tool_registry.get("execute").schema["function"]["parameters"]
    assert set(parameters["properties"]) == {"command", "working_dir", "timeout"}
    assert parameters["required"] == ["command"]
    result = asyncio.run(tool_registry.execute("execute", {"command": "echo registry ok"}))
    assert "registry ok" in result


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell")
def test_powershell_preserves_script():
    script = "Write-Output 'text with spaces'"
    result = execute_tool("powershell -NoProfile -NonInteractive -Command " + script)
    assert "text with spaces" in result


def test_legacy_python_command_with_quoted_script():
    assert "legacy ok" in execute_tool(f'"{sys.executable}" -c "print(\'legacy ok\')"')


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
@pytest.mark.parametrize("command", ["more file", "cmd /c echo hi | more", "less file"])
def test_interactive_commands_are_still_blocked(command):
    assert "Command blocked" in execute_tool(command)


def test_relative_working_dir_and_workspace_escape(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    result = execute_tool(f'"{sys.executable}" -c "import os;print(os.getcwd())"', working_dir="nested")
    assert str(nested) in result
    assert "must be inside the workspace" in execute_tool("echo hi", working_dir="..")


def test_runtime_progress_total_for_command(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from miniclaw.runtime.tool_executor import ToolExecutor
    from miniclaw.runtime import tool_executor
    input_file = tmp_path / "input with spaces.lmp"
    input_file.write_text("run 10\nrun 20\n", encoding="utf-8")
    monkeypatch.setattr(tool_executor, "active_workspace_dir", lambda: tmp_path)
    events = []

    async def progress(data):
        events.append(data)

    async def exercise():
        arguments = {"command": f'lmp.exe -in "{input_file.name}"'}
        context = SimpleNamespace(metadata={"_on_progress": progress})
        ToolExecutor._inject_progress_callback("execute", arguments, context)
        arguments["_on_line"]("10 300 100")
        await asyncio.sleep(0.01)

    asyncio.run(exercise())
    assert events == [{"current": 10, "total": 30}]


def test_other_program_l_flag_does_not_start_log_polling(monkeypatch):
    seen = []
    def capture(*a, **kwargs):
        seen.append(kwargs["log_path"])
        return 0, "ok", ""
    monkeypatch.setattr(exec_tool, "_run_subprocess", capture)
    execute_tool("ssh -l user host", _on_line=lambda line: None)
    assert seen == [None]


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_unwrapped_copy_and_pipeline(tmp_path):
    source = tmp_path / "source file.txt"
    target = tmp_path / "target file.txt"
    source.write_text("line one\nline two\n", encoding="utf-8")
    result = execute_tool(f'copy "{source}" "{target}" && type "{target}" | findstr "two"')
    assert "Error:" not in result and "line two" in result
    assert target.read_text() == source.read_text()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_public_command_quoted_executable_and_arguments(tmp_path):
    script = tmp_path / "script with spaces.py"
    script.write_text("import sys;print(sys.argv[1])", encoding="utf-8")
    result = execute_tool(f'"{sys.executable}" "{script}" "argument with spaces"')
    assert "argument with spaces" in result and "Error:" not in result


def test_lammps_default_timeout_is_chosen_by_backend(monkeypatch):
    seen = []
    monkeypatch.setattr(exec_tool, "_default_timeout", lambda: 60)
    monkeypatch.setattr(exec_tool, "_max_timeout", lambda: 900)
    def capture(*a, **kwargs):
        seen.append((kwargs["timeout_sec"], kwargs["log_path"]))
        return 0, "ok", ""
    monkeypatch.setattr(exec_tool, "_run_subprocess", capture)
    execute_tool('mpiexec.exe -np 2 "lmp.exe" -in in.test -l log.lammps', _on_line=lambda line: None)
    execute_tool('lmp.exe -in in.test', timeout=12)
    execute_tool('echo ordinary task')
    assert seen == [(600, "log.lammps"), (12, None), (60, None)]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell")
def test_backend_shell_configuration(monkeypatch):
    monkeypatch.setattr(exec_tool.config.execution, "shell", "powershell")
    result = execute_tool("Write-Output 'configured shell'")
    assert "configured shell" in result and "Error:" not in result


def test_function_signature_matches_the_public_schema():
    import inspect
    signature = inspect.signature(execute_tool)
    parameters = signature.parameters
    assert set(parameters) == {"command", "working_dir", "timeout", "kwargs"}
    assert parameters["command"].default is inspect.Parameter.empty
    assert parameters["kwargs"].kind == inspect.Parameter.VAR_KEYWORD


@pytest.mark.parametrize("command", ['echo "literal | more"', 'echo "lmp.exe -l fake.log"', 'echo restart', 'echo in.restart'])
def test_literal_text_is_not_mistaken_for_executed_programs(command):
    assert "Error:" not in execute_tool(command)
    assert find_lammps_log(command) is None


@pytest.mark.parametrize("command", ["shutdown /s", "cmd /c shutdown /s", 'powershell -Command "shutdown /s"', "echo hi && shutdown /s"])
def test_obvious_system_commands_are_still_blocked(command):
    assert "Command blocked" in execute_tool(command)


@pytest.mark.parametrize("command", ["echo lmp.exe -in file", "python lmp.exe -l fake.log", "lmp.exe -l log&&echo done", "cd subdir && lmp.exe -in in.test"])
def test_lammps_adapter_does_not_guess_ambiguous_commands(command):
    from miniclaw.tools.lammps_execution import is_lammps_command, find_lammps_input
    assert not is_lammps_command(command)
    assert find_lammps_input(command) is None
    assert find_lammps_log(command) is None


@pytest.mark.parametrize("timeout", [0, -1, "5", True, 1.5, []])
def test_invalid_timeouts_are_rejected(timeout):
    assert "timeout must be a positive integer" in execute_tool("echo hi", timeout=timeout)


def test_internal_context_and_progress_callback_remain_supported():
    events = []
    result = execute_tool("echo framework callback", _context={"chat_id": "test"}, _on_line=events.append)
    assert "framework callback" in result
    assert any("framework callback" in line for line in events)


def test_missing_working_directory_is_not_reported_as_missing_executable():
    result = execute_tool("echo hi", working_dir="missing")
    assert "working_dir must be an existing directory" in result


def test_lammps_restart_input_name_is_not_a_system_command(monkeypatch):
    monkeypatch.setattr(exec_tool, "_run_subprocess", lambda *a, **k: (0, "accepted", ""))
    assert "accepted" in execute_tool("lmp.exe -in in.restart")


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_multiline_python_argument_executes_both_lines():
    result = execute_tool(f'"{sys.executable}" -c "print(\'a\')\nprint(\'b\')"')
    assert result.split("STDOUT:\n", 1)[1].splitlines() == ["a", "b"]


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_multiline_parenthesized_file_creation(tmp_path):
    result = execute_tool('(\necho first\necho second\n) > lines.txt\ntype lines.txt')
    assert "Error:" not in result
    assert (tmp_path / "lines.txt").read_text().splitlines() == ["first", "second"]
    assert "first" in result and "second" in result


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_unicode_builtin_output_is_decoded_correctly():
    assert "很难的挑战" in execute_tool("echo 很难的挑战")


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_multiline_python_preserves_escaped_quotes_and_unicode():
    code = 'print("中文")\nprint("second")'
    escaped = code.replace('"', '\\"')
    command = f'"{sys.executable}" -c "{escaped}"'
    result = execute_tool(command)
    assert "中文" in result and "second" in result


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_mixed_builtin_and_python_unicode_output():
    result = execute_tool(f'echo 很难的挑战 & "{sys.executable}" -c "print(\'中文®\')"')
    assert "很难的挑战" in result and "中文®" in result


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd")
def test_unsupported_multiline_argument_fails_before_execution():
    result = execute_tool('echo "first\nsecond"')
    assert result.startswith("Error:") and "multiline quoted arguments" in result
