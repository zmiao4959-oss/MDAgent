from miniclaw.tools.exec_tool import execute_tool


def test_execute_rejects_windows_shell_builtins_without_cmd():
    result = execute_tool("dir C:\\")

    assert "Windows shell built-in" in result
    assert "cmd /c dir C:\\" in result


def test_execute_rejects_shell_operators_without_shell_wrapper():
    result = execute_tool("python -c \"print(1)\" && python -c \"print(2)\"")

    assert "shell operators detected" in result
    assert "cmd /c" in result


def test_execute_allows_shell_operators_inside_quoted_args():
    """引号内的 && / || / | 是传给目标程序的参数，不应拦截。"""
    result = execute_tool("ssh hpc \"bash -lc 'mkdir -p dir && cp file dir/ && ls dir/'\"")
    # 不应报 shell operators 错误（可能报 ssh 找不到等，但不是拦截）
    assert "shell operators detected" not in result


def test_execute_allows_nested_posix_quotes_in_ssh_remote_command():
    """ssh 远程命令中的 POSIX 嵌套双引号（\"）不应被拦截。"""
    result = execute_tool(
        "ssh hpc \"bash -lc 'bhosts | awk \\\"NR>1 && \\$2==\\\\\\\"ok\\\\\\\"\\\" | sort -k4,4nr'\""
    )
    assert "shell operators detected" not in result
