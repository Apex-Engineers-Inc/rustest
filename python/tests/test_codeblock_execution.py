"""Doc block execution: node shapes, fixtures, and the failure model."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rustest._v2_worker import DEFAULT_NAMING, Naming


def _default_naming() -> Naming:
    return DEFAULT_NAMING


def _md(tmp_path: Path, body: str, *, enable: bool = True) -> Path:
    if enable:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.rustest]\ncodeblocks = true\n", encoding="utf-8"
        )
    page = tmp_path / "page.md"
    page.write_text(body, encoding="utf-8")
    return page


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # rustest writes its summary to stderr so stdout stays clean for --llm JSONL.
    return subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_block_segment_is_in_the_id_but_not_the_class_name(tmp_path: Path) -> None:
    """The wire shape, pinned directly.

    A module-level test inside a block must carry NO class_name. If it acquires one,
    class-scope teardown breaks silently; see test_class_scope_is_torn_down_per_test.
    """
    from rustest._v2_worker import collect_module
    import types

    module = types.ModuleType("block_probe")
    module.__file__ = str(tmp_path / "page.md")
    exec(
        "def test_alpha():\n    assert True\n"
        "class TestBox:\n    def test_beta(self):\n        assert True\n",
        module.__dict__,
    )

    entries, _plans = collect_module(
        module,
        tmp_path / "page.md",
        tmp_path,
        naming=_default_naming(),
        block_segment="codeblock_0_line_3",
    )

    by_name = {e["qualname"]: e for e in entries}
    alpha = by_name["codeblock_0_line_3.test_alpha"]
    assert alpha["id"].endswith("page.md::codeblock_0_line_3::test_alpha")
    assert "class_name" not in alpha, (
        "a module-level block test must have no class_name; a phantom class breaks "
        "class-scope teardown"
    )

    beta = by_name["codeblock_0_line_3.TestBox.test_beta"]
    assert beta["class_name"] == "TestBox", (
        "a real class keeps its own name, with no block segment mixed in"
    )


def test_unittest_case_in_a_block_carries_the_block_segment_too(tmp_path: Path) -> None:
    """`_collect_unittest_class.record` builds its `CollectedTest`/`ExecutionPlan` directly,
    never going through `_collect_function` -- so it needs `block_segment` threaded to it
    explicitly. Without that, two blocks each defining an identically-named `TestCase`
    subclass and method produce IDENTICAL wire ids, a genuine id collision.
    """
    from rustest._v2_worker import collect_module
    import types

    source = (
        "import unittest\n\n"
        "class Legacy(unittest.TestCase):\n"
        "    def test_it(self):\n"
        "        assert True\n"
    )

    def collect(block_segment: str) -> dict[str, dict[str, object]]:
        module = types.ModuleType("block_probe_unittest")
        module.__file__ = str(tmp_path / "page.md")
        exec(source, module.__dict__)
        entries, _plans = collect_module(
            module,
            tmp_path / "page.md",
            tmp_path,
            naming=_default_naming(),
            block_segment=block_segment,
        )
        return {e["qualname"]: e for e in entries}

    first = collect("codeblock_0_line_3")
    second = collect("codeblock_1_line_10")

    first_case = first["codeblock_0_line_3.Legacy.test_it"]
    second_case = second["codeblock_1_line_10.Legacy.test_it"]

    assert first_case["id"].endswith("page.md::codeblock_0_line_3::Legacy::test_it")
    assert second_case["id"].endswith("page.md::codeblock_1_line_10::Legacy::test_it")
    assert first_case["id"] != second_case["id"], (
        "two blocks defining the same-named TestCase and method must not collide on the wire id"
    )
