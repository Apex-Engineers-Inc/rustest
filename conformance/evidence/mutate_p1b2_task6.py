"""Per-test mutation verification for P1b.2 Task 6 (the bare-mark discriminator).

Each row applies one textual mutation to the **runner** -- ``decorators.py``'s port of
pytest's ``MarkDecorator.__call__`` rule, or the compat shim's use of it -- and runs ONLY
the tests named for that row. A non-zero pytest exit is a KILL. A zero exit is a SURVIVOR.
A timeout (300s) is a SURVIVOR, never a kill. A row whose anchor does not appear exactly
once is reported as BAD ANCHOR rather than silently skipped, so a reflow that moves an
anchor cannot quietly shrink the table.

Unlike the Task 5 table (which mutated the *harness*, asking "would the gate notice?"),
every row here mutates the **fix itself**, asking the more direct question: is each clause
of the ported discrimination load-bearing, and is there a test that fails when it is wrong?
Clause by clause, the rule under test is::

    if args and not kwargs:                     # rows 1, 2
        func = args[0]
        is_class = inspect.isclass(func)        # row 6
        unwrapped_func = func
        if isinstance(func, (staticmethod, classmethod)):
            unwrapped_func = func.__func__      # row 7
        if len(args) == 1 and (istestfunc(unwrapped_func) or is_class):
            store_mark(unwrapped_func, ...)     # rows 3, 4, 8, 9, 10
            return func                         # row 5
    return self.with_args(*args, **kwargs)

Run: `uv run python conformance/evidence/mutate_p1b2_task6.py [row ids...]`

WARNING: this edits tracked sources in place and restores them afterwards. Do not run it
with unsaved work in the files it touches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
TIMEOUT = 300

DEC = "python/rustest/decorators.py"
CMP = "python/rustest/compat/pytest.py"

T = "python/tests/test_bare_marks.py"
T_CLI = "python/tests/test_run_cli.py"


@dataclass
class Row:
    id: int
    area: str
    file: str
    old: str
    new: str
    tests: list[str]
    note: str = ""
    extra: list[tuple[str, str, str]] = field(default_factory=list)


ROWS: list[Row] = [
    # ------------------------------------------------------- the guard: args, not kwargs
    Row(
        1,
        "guard",
        DEC,
        "    if not args or kwargs or len(args) != 1:",
        "    if not args or len(args) != 1:",
        [f"{T}::test_any_keyword_argument_forces_the_factory_branch"],
        "kwargs no longer veto a decoration: mark.slow(f, why=1) eats the function",
    ),
    Row(
        2,
        "guard",
        DEC,
        "    if not args or kwargs or len(args) != 1:",
        "    if not args or kwargs:",
        [f"{T}::test_two_positionals_force_the_factory_branch"],
        "the len(args) == 1 clause dropped: a two-arg factory call decorates arg 0",
    ),
    # ------------------------------------------------------------------ istestfunc itself
    Row(
        3,
        "istestfunc",
        DEC,
        '    is_testfunc = callable(unwrapped) and getattr(unwrapped, "__name__", "<lambda>") '
        + '!= "<lambda>"',
        "    is_testfunc = callable(unwrapped)",
        [f"{T}::test_a_lambda_positional_is_a_factory_call_not_a_decoration"],
        "lambda rule dropped: mark.slow(lambda: 1) decorates the lambda (the OLD bug)",
    ),
    Row(
        4,
        "istestfunc",
        DEC,
        '    is_testfunc = callable(unwrapped) and getattr(unwrapped, "__name__", "<lambda>") '
        + '!= "<lambda>"',
        '    is_testfunc = callable(unwrapped) and getattr(unwrapped, "__name__", None) is None',
        [
            f"{T}::test_bare_xfail_returns_the_function_and_records_an_unconditional_xfail",
            f"{T}::test_bare_compat_skip_returns_the_test_function_itself",
        ],
        "istestfunc inverted: no ordinary function is ever a decoration target (#137)",
    ),
    # --------------------------------------------------------------- what is returned
    Row(
        5,
        "return",
        DEC,
        "        unwrapped, original = target\n        _ = self._bare(unwrapped)\n"
        + "        return original",
        "        unwrapped, original = target\n        _ = self._bare(unwrapped)\n"
        + "        return unwrapped",
        [f"{T}::test_a_staticmethod_is_unwrapped_but_the_descriptor_is_returned"],
        "the unwrapped function is returned instead of the descriptor",
    ),
    # -------------------------------------------------------------------- the class branch
    Row(
        6,
        "class branch",
        DEC,
        "    if is_testfunc or is_class:",
        "    if is_testfunc:",
        [
            f"{T}::test_a_class_positional_is_a_decoration",
            f"{T}::test_the_is_class_branch_is_load_bearing_for_a_class_named_lambda",
        ],
        "the is_class branch dropped -- only a class named '<lambda>' can tell",
    ),
    Row(
        7,
        "unwrap",
        DEC,
        "    if isinstance(original, (staticmethod, classmethod)):\n"
        + "        unwrapped = cast(Any, original).__func__",
        "    if isinstance(original, (staticmethod, classmethod)):\n        unwrapped = original",
        [
            f"{T}::test_a_staticmethod_is_unwrapped_but_the_descriptor_is_returned",
            f"{T}::test_a_classmethod_is_unwrapped_but_the_descriptor_is_returned",
        ],
        "descriptor not unwrapped: the mark lands where nothing reads it",
    ),
    # ------------------------------------------------------- what the bare form stores
    Row(
        8,
        "bare mark",
        DEC,
        "bare if bare is not None else MarkDecorator(name, (), {})",
        "bare if bare is not None else MarkDecorator(name, (None,), {})",
        [
            f"{T}::test_bare_skipif_is_an_unconditional_skip_like_pytests",
            f"{T_CLI}::test_bare_marks_match_pytest_bucket_for_bucket",
        ],
        "bare mark carries a condition: bare skipif/xfail stop being unconditional",
    ),
    Row(
        9,
        "bare mark",
        DEC,
        "        target = _mark_decoration_target(args, kwargs)\n        if target is None:",
        "        target = None\n        if target is None:",
        [
            f"{T}::test_bare_xfail_returns_the_function_and_records_an_unconditional_xfail",
            f"{T_CLI}::test_bare_marks_match_pytest_bucket_for_bucket",
        ],
        "discrimination disabled entirely -- the pre-fix #137 behaviour, restored",
    ),
    # -------------------------------------------------------------- the compat skip route
    Row(
        10,
        "compat skip",
        CMP,
        "            bare=_rustest_skip_decorator(reason=None),",
        "            bare=lambda func: func,",
        [f"{T}::test_bare_compat_skip_records_skip_metadata_not_a_function_reason"],
        "bare skip records nothing: v1's only skip source is never set (#136)",
    ),
    Row(
        11,
        "compat skip",
        CMP,
        '        self._skip = _BareOrFactoryMark(\n            "skip",\n'
        + "            _rustest_skip_decorator,",
        '        self._skip = _BareOrFactoryMark(\n            "skip",\n'
        + "            lambda *a, **k: _rustest_skip_decorator(),",
        [f"{T}::test_called_compat_skip_is_still_a_factory"],
        "called skip loses its reason -- the control for the bare fix",
    ),
]


def run_tests(tests: list[str]) -> tuple[int, str]:
    cmd = ["uv", "run", "pytest", "-q", "--no-header", *tests]
    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def main() -> int:
    only = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    killed: list[int] = []
    survived: list[tuple[int, str]] = []
    bad: list[tuple[int, str]] = []

    for row in ROWS:
        if only is not None and row.id not in only:
            continue
        edits = [(row.file, row.old, row.new), *row.extra]
        originals: dict[str, str] = {}
        ok = True
        for path, old, new in edits:
            target = REPO / path
            if path not in originals:
                originals[path] = target.read_text(encoding="utf-8")
            current = target.read_text(encoding="utf-8")
            hits = current.count(old)
            if hits != 1:
                bad.append((row.id, f"anchor appears {hits}x in {path}"))
                ok = False
                break
            target.write_text(current.replace(old, new), encoding="utf-8")
        if not ok:
            for path, text in originals.items():
                (REPO / path).write_text(text, encoding="utf-8")
            continue

        started = time.time()
        code, tail = run_tests(row.tests)
        elapsed = time.time() - started
        for path, text in originals.items():
            (REPO / path).write_text(text, encoding="utf-8")

        if code == -1:
            survived.append((row.id, f"TIMEOUT after {TIMEOUT}s"))
            verdict = "SURVIVED (timeout)"
        elif code != 0:
            killed.append(row.id)
            verdict = "killed"
        else:
            survived.append((row.id, tail))
            verdict = "SURVIVED"
        print(f"[{row.id:>3}] {row.area:<12} {verdict:<18} {elapsed:5.1f}s  {row.note}", flush=True)

    total = len(killed) + len(survived)
    print(f"\n{len(killed)}/{total} killed")
    if survived:
        print("\nSURVIVORS:")
        for rid, tail in survived:
            print(f"  row {rid}: {tail[:400]}")
    if bad:
        print("\nBAD ANCHORS:")
        for rid, why in bad:
            print(f"  row {rid}: {why}")
    return 0 if not survived and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
