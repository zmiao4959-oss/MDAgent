import json

from miniclaw.config import workspace_scope
from miniclaw.tools.file_tools import (
    copy_file_tool,
    make_dir_tool,
    update_json_tool,
    write_json_tool,
)


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
