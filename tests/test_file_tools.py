import json

from miniclaw.config import workspace_scope
from miniclaw.tools.file_tools import (
    copy_file_tool,
    edit_tool,
    make_dir_tool,
    update_json_tool,
    write_json_tool,
)


def test_edit_replaces_one_line_without_rewriting_others(tmp_path):
    target = tmp_path / "run.in"
    target.write_text("potential nep.txt\nensemble nvt_nhc 300 300 100\nrun 1000\n", encoding="utf-8")
    with workspace_scope(tmp_path):
        result = edit_tool("run.in", "run 1000", "run 2000")
    assert "Successfully edited" in result
    assert target.read_text(encoding="utf-8") == "potential nep.txt\nensemble nvt_nhc 300 300 100\nrun 2000\n"


def test_edit_rejects_ambiguous_or_partial_line_without_changes(tmp_path):
    target = tmp_path / "run.in"
    original = b"run 1000\nrun 1000\nrun 10000\n"
    target.write_bytes(original)
    with workspace_scope(tmp_path):
        assert "more than once" in edit_tool("run.in", "run 1000", "run 2000")
        assert "not found" in edit_tool("run.in", "run 100", "run 200")
    assert target.read_bytes() == original


def test_edit_multiline_preserves_crlf_and_bom(tmp_path):
    target = tmp_path / "run.in"
    target.write_bytes(b"\xef\xbb\xbfpotential nep.txt\r\nensemble nve\r\nrun 1000\r\n")
    with workspace_scope(tmp_path):
        result = edit_tool("run.in", "ensemble nve\nrun 1000", "ensemble nvt_nhc 300 300 100\nrun 2000")
    assert "Successfully edited" in result
    assert target.read_bytes() == b"\xef\xbb\xbfpotential nep.txt\r\nensemble nvt_nhc 300 300 100\r\nrun 2000\r\n"


def test_edit_rejects_path_outside_workspace_and_binary_file(tmp_path):
    target = tmp_path / "run.in"
    target.write_bytes(b"a\x00b\n")
    with workspace_scope(tmp_path):
        assert "binary" in edit_tool("run.in", "a", "b")
        assert "must stay inside" in edit_tool("../outside.in", "a", "b")
    assert target.read_bytes() == b"a\x00b\n"


def test_copy_file_copies_between_workspace_locations(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("hello", encoding="utf-8")

    with workspace_scope(workspace):
        result = copy_file_tool("source.txt", "nested/copied.txt")

    copied = workspace / "nested" / "copied.txt"

    assert "Successfully copied" in result
    assert copied.read_text(encoding="utf-8") == "hello"


def test_copy_file_rejects_same_source_and_destination(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("hello", encoding="utf-8")

    with workspace_scope(workspace):
        result = copy_file_tool("source.txt", "source.txt")

    assert "same file" in result


def test_write_json_writes_structured_data(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with workspace_scope(workspace):
        result = write_json_tool(
            "configs/demo.json",
            {"name": "demo", "values": [1, 2, 3]},
        )

    target = workspace / "configs" / "demo.json"

    assert "Successfully wrote JSON" in result
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "name": "demo",
        "values": [1, 2, 3],
    }


def test_update_json_patches_selected_fields(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "tensile_config.json"
    target.write_text(
        json.dumps({
            "lattice": {"cells": [10, 10, 10]},
            "tension": {
                "run_steps": 30000,
                "stress_component": "pxx",
                "stress_divisor": 10000,
            },
            "relaxation": {"run_steps": 2000},
        }),
        encoding="utf-8",
    )

    with workspace_scope(workspace):
        result = update_json_tool(
            "tensile_config.json",
            {
                "lattice.cells": [20, 20, 20],
                "tension.run_steps": 40000,
            },
        )

    data = json.loads(target.read_text(encoding="utf-8"))

    assert "Successfully updated JSON fields" in result
    assert data["lattice"]["cells"] == [20, 20, 20]
    assert data["tension"]["run_steps"] == 40000
    assert data["tension"]["stress_component"] == "pxx"
    assert data["tension"]["stress_divisor"] == 10000
    assert data["relaxation"]["run_steps"] == 2000


def test_make_dir_creates_nested_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with workspace_scope(workspace):
        result = make_dir_tool("runs/case_01")

    assert "Successfully created directory" in result
    assert (workspace / "runs" / "case_01").is_dir()
