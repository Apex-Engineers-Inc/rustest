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
    from rustest._v2_worker import ExecutionPlan, collect_module
    import types

    module = types.ModuleType("block_probe")
    module.__file__ = str(tmp_path / "page.md")
    exec(
        "def test_alpha():\n    assert True\n"
        "class TestBox:\n    def test_beta(self):\n        assert True\n",
        module.__dict__,
    )

    entries, plans = collect_module(
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

    # The runtime half: `ExecutionPlan.block_segment` is a separate field from the wire
    # `id` (see the unittest test below for why the id alone does not pin this), so it needs
    # its own assertion or a dropped `block_segment=` kwarg on the `ExecutionPlan(...)`
    # construction ships invisibly -- the wire entries would still look right.
    plans_by_id: dict[str, ExecutionPlan] = {plan.id: plan for plan in plans}
    assert plans_by_id[alpha["id"]].block_segment == "codeblock_0_line_3"
    assert plans_by_id[beta["id"]].block_segment == "codeblock_0_line_3"


def test_unittest_case_in_a_block_carries_the_block_segment_too(tmp_path: Path) -> None:
    """`_collect_unittest_class.record` builds its `CollectedTest`/`ExecutionPlan` directly,
    never going through `_collect_function` -- so it needs `block_segment` threaded to it
    explicitly. Without that, two blocks each defining an identically-named `TestCase`
    subclass and method produce IDENTICAL wire ids, a genuine id collision.
    """
    from rustest._v2_worker import ExecutionPlan, collect_module
    import types

    source = (
        "import unittest\n\n"
        "class Legacy(unittest.TestCase):\n"
        "    def test_it(self):\n"
        "        assert True\n"
    )

    def collect(
        block_segment: str,
    ) -> tuple[dict[str, dict[str, object]], dict[str, ExecutionPlan]]:
        module = types.ModuleType("block_probe_unittest")
        module.__file__ = str(tmp_path / "page.md")
        exec(source, module.__dict__)
        entries, plans = collect_module(
            module,
            tmp_path / "page.md",
            tmp_path,
            naming=_default_naming(),
            block_segment=block_segment,
        )
        by_name = {e["qualname"]: e for e in entries}
        plans_by_id: dict[str, ExecutionPlan] = {plan.id: plan for plan in plans}
        return by_name, plans_by_id

    first_entries, first_plans = collect("codeblock_0_line_3")
    second_entries, second_plans = collect("codeblock_1_line_10")

    first_case = first_entries["codeblock_0_line_3.Legacy.test_it"]
    second_case = second_entries["codeblock_1_line_10.Legacy.test_it"]

    assert first_case["id"].endswith("page.md::codeblock_0_line_3::Legacy::test_it")
    assert second_case["id"].endswith("page.md::codeblock_1_line_10::Legacy::test_it")
    assert first_case["id"] != second_case["id"], (
        "two blocks defining the same-named TestCase and method must not collide on the wire id"
    )

    # The runtime half, pinned separately from the wire id: `record()` constructs
    # `ExecutionPlan` directly (never through `_collect_function`), and `ExecutionPlan.id` is
    # copied from the wire entry's id regardless of whether the `ExecutionPlan(...)` call
    # itself was given `block_segment=`. So a dropped kwarg there leaves `plan.id` correct
    # and only `plan.block_segment` wrong -- silent unless asserted on directly.
    first_plan = first_plans[first_case["id"]]
    second_plan = second_plans[second_case["id"]]
    assert first_plan.block_segment == "codeblock_0_line_3"
    assert second_plan.block_segment == "codeblock_1_line_10"
    assert first_plan.block_segment != second_plan.block_segment, (
        "the two blocks' ExecutionPlans must carry their own distinct segments"
    )


def test_inner_tests_become_their_own_nodes(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\ndef test_one():\n    assert True\ndef test_two():\n    assert False\n```\n",
    )
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "1 failed" in combined and "1 passed" in combined, combined
    assert "::test_one" in combined and "::test_two" in combined


def test_a_block_with_no_test_functions_keeps_one_node(tmp_path: Path) -> None:
    page = _md(tmp_path, "```python\nx = 1\nassert x == 1\n```\n")
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "1 passed" in combined, combined
    assert "codeblock_0_line_1" in combined


def test_an_inner_test_resolves_a_conftest_fixture(tmp_path: Path) -> None:
    """The 'a code block requests no fixtures' limitation is gone."""
    (tmp_path / "conftest.py").write_text(
        "from rustest import fixture\n\n@fixture\ndef supplied():\n    return 7\n",
        encoding="utf-8",
    )
    page = _md(
        tmp_path,
        "```python\ndef test_uses(supplied):\n    assert supplied == 7\n```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_class_scope_is_torn_down_per_test(tmp_path: Path) -> None:
    """The pinning test for the phantom-class hazard.

    Two module-level tests in one block must each get their own class-scoped fixture
    value. If the block segment leaked into class_name they would share one.
    """
    (tmp_path / "conftest.py").write_text(
        "from rustest import fixture\n"
        "_n = [0]\n\n"
        "@fixture(scope='class')\n"
        "def counter():\n"
        "    _n[0] += 1\n"
        "    return _n[0]\n",
        encoding="utf-8",
    )
    page = _md(
        tmp_path,
        "```python\n"
        "def test_first(counter):\n    assert counter == 1\n"
        "def test_second(counter):\n    assert counter == 2\n"
        "```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, (
        "class scope was not torn down per test; the block segment probably reached "
        "class_name\n" + proc.stdout + proc.stderr
    )


def test_parametrize_and_classes_work_inside_a_block(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\n"
        "from rustest import parametrize\n\n"
        "@parametrize('n', [1, 2, 3])\n"
        "def test_p(n):\n    assert n > 0\n\n"
        "class TestBox:\n    def test_m(self):\n        assert True\n"
        "```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "4 passed" in combined, combined


def test_two_blocks_defining_the_same_name_get_distinct_ids(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\ndef test_dup():\n    assert True\n```\n\n"
        "```python\ndef test_dup():\n    assert True\n```\n",
    )
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "2 passed" in combined, combined
    assert "codeblock_0_" in combined and "codeblock_1_" in combined


def test_skip_marked_blocks_are_not_executed(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "<!--rustest.mark.skip-->\n```python\nraise RuntimeError('must not run')\n```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout + proc.stderr


def test_codeblock_mark_reaches_an_inner_test(tmp_path: Path) -> None:
    """The `codeblock` mark must follow tests down to inner-test granularity.

    Design doc `2026-08-04-doc-block-execution-design.md:345`: "The codeblock mark
    continues to be attached to every collected node ... now at inner-test granularity."
    Checked in both directions, since a one-directional test would miss half of a mark
    that is simply absent: `-m codeblock` selecting nothing looks identical to `-m
    codeblock` never having been wired up, and `-m "not codeblock"` deselecting nothing
    looks identical to the same gap from the other side.
    """
    page = _md(
        tmp_path,
        "```python\ndef test_inner():\n    assert True\n```\n",
    )

    excluded = _run(str(page), "-m", "not codeblock", "-q", cwd=tmp_path)
    combined = excluded.stdout + excluded.stderr
    assert "1 deselected" in combined and "1 passed" not in combined, combined

    included = _run(str(page), "-m", "codeblock", "-q", cwd=tmp_path)
    combined = included.stdout + included.stderr
    assert "1 passed" in combined and "deselected" not in combined, combined


def test_codeblock_mark_reaches_the_execution_plan_too(tmp_path: Path) -> None:
    """The runtime half of the previous test, pinned separately.

    `-m` selection reads only the wire manifest, so a test that only runs the CLI and
    checks `-m codeblock` output cannot see whether `ExecutionPlan.marks` -- the copy the
    *execute* half reads, e.g. for `request.node.get_closest_marker` -- was also given the
    mark. A dropped `marks=(*plan.marks, marks[0])` there is invisible to the wire-only
    test above, the same class of gap the plan-half of `block_segment` had.
    """
    from rustest._v2_worker import collect_markdown

    page = tmp_path / "page.md"
    page.write_text("```python\ndef test_inner():\n    assert True\n```\n", encoding="utf-8")

    _entries, plans = collect_markdown(page, tmp_path, _default_naming())

    assert len(plans) == 1
    assert any(mark.name == "codeblock" for mark in plans[0].marks), (
        "the inner test's ExecutionPlan must carry the codeblock mark, not just its wire entry"
    )


def test_codeblock_mark_on_the_fallback_shape_still_works(tmp_path: Path) -> None:
    """The no-test-functions shape must keep the mark too, so the two shapes cannot drift
    apart from each other now that inner tests carry it as well.
    """
    page = _md(tmp_path, "```python\nx = 1\nassert x == 1\n```\n")

    excluded = _run(str(page), "-m", "not codeblock", "-q", cwd=tmp_path)
    combined = excluded.stdout + excluded.stderr
    assert "1 deselected" in combined and "1 passed" not in combined, combined

    included = _run(str(page), "-m", "codeblock", "-q", cwd=tmp_path)
    combined = included.stdout + included.stderr
    assert "1 passed" in combined and "deselected" not in combined, combined
