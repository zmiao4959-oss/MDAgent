"""The file library and retrieval corpus must have the same complete body."""
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from miniclaw.rag.library import publish_library
from miniclaw.rag.store import RagDocument


def document(keyword, content, *, kind="input_parameters", source_format="rst"):
    return RagDocument(
        id=f"manual:v5.5:{kind}/{keyword}", title=keyword, content=content,
        source=f"https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/{kind}/{keyword}.rst",
        version="5.5", metadata={"keyword": keyword, "kind": kind, "source_ref": "v5.5", "source_format": source_format, "source_path": f"doc/gpumd/{kind}/{keyword}.rst"},
    )


DEFORM = """.. _kw_deform:
.. index::
   single: deform

:attr:`deform`
==============

Syntax
------

The 6-parameter form::

  deform <A_per_step_x> <A_per_step_y> <A_per_step_z> <deform_x> <deform_y> <deform_z>

The unit is Ångstrom/step.

Example
-------

.. code::

   ensemble npt_scr 300 300 100 0 0 0 100 100 100 1000
   deform 0.00001 0.00001 0 1 1 0
   run 100000

Caveats
-------

* One must control the three pressure components independently.
* Use :doc:`ensemble_standard`.
"""


def test_complete_command_lines_units_caveats_and_same_disk_body(tmp_path):
    original = document("deform", DEFORM)
    docs = publish_library([original, document("ensemble_standard", "Ensemble\n========\n\nensemble nve")], tmp_path)
    result = docs[0]
    assert result.content == (tmp_path / "commands/deform.md").read_text(encoding="utf-8")
    assert "deform <A_per_step_x> <A_per_step_y> <A_per_step_z> <deform_x> <deform_y> <deform_z>" in result.content
    assert "ensemble npt_scr 300 300 100 0 0 0 100 100 100 1000\ndeform 0.00001 0.00001 0 1 1 0\nrun 100000" in result.content
    assert "Ångstrom/step" in result.content
    assert "Caveats" in result.content and "pressure components independently" in result.content
    assert "[ensemble_standard](ensemble_standard.md)" in result.content
    assert result.metadata["source_content_hash"] == hashlib.sha256(original.content.encode()).hexdigest()
    assert result.metadata["content_hash"] == hashlib.sha256(result.content.encode()).hexdigest()
    assert Path(result.metadata["local_path"]).read_text(encoding="utf-8") == result.content


def test_indexes_task_alias_and_input_filename_links_all_resolve(tmp_path):
    docs = [document("deform", DEFORM), document("ensemble_standard", "ensemble npt_scr"), document("model_xyz", "model.xyz", kind="input_files"), document("run_in", "run.in", kind="input_files")]
    publish_library(docs, tmp_path)
    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "双轴拉伸" in index and "commands/deform.md" in index and "commands/ensemble_standard.md" in index
    catalog = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["aliases"]["npt_scr"] == ["commands/ensemble_standard.md"]
    assert catalog["aliases"]["model.xyz"] == ["files/input/model_xyz.md"]
    assert "model.xyz" in (tmp_path / "files/input/index.md").read_text(encoding="utf-8")
    for file in tmp_path.rglob("*.md"):
        for link in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            if "://" not in link:
                assert (file.parent / link).is_file(), (file, link)


def test_examples_are_discoverable_and_original_skill_references_are_untouched(tmp_path):
    example = tmp_path / "examples/biaxial/README.md"
    example.parent.mkdir(parents=True)
    example.write_text("# 双轴拉伸示例", encoding="utf-8")
    (tmp_path / "examples/manifest.json").write_text(json.dumps({"examples": [{"id": "biaxial", "title": "双轴拉伸", "path": "biaxial/README.md", "commands": ["deform", "ensemble_standard"]}]}), encoding="utf-8")
    skill = tmp_path / "SKILL.md"
    skill.write_text("existing skill", encoding="utf-8")
    references = tmp_path / "references/old.md"
    references.parent.mkdir()
    references.write_text("legacy references", encoding="utf-8")
    publish_library([document("deform", DEFORM)], tmp_path)
    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "[双轴拉伸](examples/biaxial/README.md)" in index
    assert skill.read_text(encoding="utf-8") == "existing skill"
    assert references.read_text(encoding="utf-8") == "legacy references"
    assert example.read_text(encoding="utf-8") == "# 双轴拉伸示例"
    assert (tmp_path / "examples/index.md").is_file()


def test_potential_pages_and_markdown_faq_keep_unknown_content(tmp_path):
    faq = "# NEP 元素映射\n\n```text\nnep4 2 Cu Ni\n```\n\n保留中文说明。"
    docs = publish_library([
        document("nep", "NEP\n===\n\n.. math::\n\n   E = sum_i U_i\n", kind="potentials"),
        document("nep_txt", "nep.txt format", kind="potential_files"),
        document("nep_element_mapping", faq, kind="knowledge", source_format="markdown"),
        document("potential", "potential nep.txt"),
    ], tmp_path)
    assert (tmp_path / "potentials/nep.md").is_file()
    assert (tmp_path / "potentials/nep_element_mapping.md").is_file()
    assert (tmp_path / "potentials/nep_txt.md").is_file()
    assert faq in docs[2].content
    assert ".. math::\n\n   E = sum_i U_i" in docs[0].content
    assert "nep_element_mapping.md" in (tmp_path / "commands/index.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["id", "path", "escape"])
def test_invalid_documents_are_rejected_before_writes(tmp_path, change):
    first = document("deform", DEFORM)
    second = document("run", "run 10")
    if change == "id":
        second = replace(second, id=first.id)
    elif change == "path":
        second = replace(second, metadata={**second.metadata, "keyword": "deform"})
    else:
        second = replace(second, metadata={**second.metadata, "library_slug": "../../escape"})
    with pytest.raises(ValueError):
        publish_library([first, second], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_official_section_indexes_do_not_overwrite_generated_directories(tmp_path):
    publish_library([document("index", "Official command overview"), document("deform", DEFORM)], tmp_path)
    assert "Official command overview" in (tmp_path / "commands/overview.md").read_text(encoding="utf-8")
    assert "deform.md" in (tmp_path / "commands/index.md").read_text(encoding="utf-8")


def test_missing_or_escaping_example_is_rejected(tmp_path):
    folder = tmp_path / "examples"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"examples": [{"path": "../../outside.md"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="路径"):
        publish_library([document("deform", DEFORM)], tmp_path)
    assert not (tmp_path / "commands").exists()
