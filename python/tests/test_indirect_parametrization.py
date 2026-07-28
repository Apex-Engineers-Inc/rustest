"""``indirect=`` normalisation, and the id/plan shape it produces.

Oracle: `_pytest/python.py::Metafunc._resolve_args_directness` (pytest 8.4.2, l. 1417-1454)
for the normalisation, and `_pytest/python.py::Metafunc._get_direct_parametrize_args`
(which filters on ``arg_directness[...] == "direct"``) for what reaches the closure's
``ignore_args``.

The behavioural half — that a routed value arrives as ``request.param`` and the test gets
the fixture's return — is diffed against real pytest in
`tests/test_indirect_parametrization.py`, which runs under both runners.  What lives here
is the half a both-runner file cannot assert: rustest validates ``indirect`` at
**decoration** (an import error, so a collection error, exit 2) while pytest validates it
during collection (also a collection error, also exit 2).  Same outcome, different moment.

Measured on pytest 8.4.2 (`scratchpad/probe/ind`) before any of this was written:

| shape                        | pytest                                              |
| ---------------------------- | --------------------------------------------------- |
| ``indirect=["nope"]``        | ``In test_x: indirect fixture 'nope' doesn't exist`` |
| ``indirect="doubled"``       | ``In test_x: indirect fixture 'd' doesn't exist``    |
| ``indirect=3``               | ``expected Sequence or boolean for indirect, got int`` |
"""

from __future__ import annotations

from typing import Any

import pytest

from rustest import parametrize
from rustest._v2_worker import _indirect_names


def _cases(func: object) -> list[dict[str, Any]]:
    return list(getattr(func, "__rustest_parametrization__", ()))


def test_indirect_true_routes_every_name() -> None:
    @parametrize("a,b", [(1, 2)], indirect=True)
    def test_x(a: int, b: int) -> None:
        pass

    assert _indirect_names(test_x) == frozenset({"a", "b"})


def test_indirect_false_routes_nothing() -> None:
    @parametrize("a,b", [(1, 2)])
    def test_x(a: int, b: int) -> None:
        pass

    assert _indirect_names(test_x) == frozenset()


def test_a_list_routes_only_the_named_ones() -> None:
    @parametrize("a,b", [(1, 2)], indirect=["b"])
    def test_x(a: int, b: int) -> None:
        pass

    assert _indirect_names(test_x) == frozenset({"b"})


def test_stacked_decorators_accumulate_their_indirect_names() -> None:
    @parametrize("b", [2], indirect=True)
    @parametrize("a", [1])
    def test_x(a: int, b: int) -> None:
        pass

    assert _indirect_names(test_x) == frozenset({"b"})


def test_ids_are_generated_from_the_parameter_not_the_fixture() -> None:
    """Making a name indirect must not move a node id — pytest generates the id from the
    parameter in both directions, which is why `parametrize/basic-ids` is unaffected."""

    @parametrize("doubled", [3, 5], indirect=True)
    def test_x(doubled: int) -> None:
        pass

    @parametrize("plain", [3, 5])
    def test_y(plain: int) -> None:
        pass

    assert [case["id"] for case in _cases(test_x)] == [case["id"] for case in _cases(test_y)]


def test_an_unknown_indirect_name_is_refused_with_pytests_wording() -> None:
    with pytest.raises(ValueError) as excinfo:

        @parametrize("x", [1], indirect=["nope"])
        def test_x(x: int) -> None:
            pass

    assert str(excinfo.value) == "In test_x: indirect fixture 'nope' doesn't exist"


def test_a_string_indirect_is_iterated_as_a_sequence() -> None:
    """``str`` is a ``Sequence``, so ``indirect="doubled"`` walks its **characters**.

    rustest used to accept this as a one-name shorthand. pytest never has: measured, it
    reports ``indirect fixture 'd' doesn't exist`` and exits 2, so a suite written that way
    is broken under pytest and the shorthand could only ever hide that.
    """
    with pytest.raises(ValueError) as excinfo:

        @parametrize("doubled", [3], indirect="doubled")
        def test_x(doubled: int) -> None:
            pass

    assert str(excinfo.value) == "In test_x: indirect fixture 'd' doesn't exist"


def test_a_non_sequence_indirect_is_refused() -> None:
    with pytest.raises(ValueError) as excinfo:

        @parametrize("x", [1], indirect=3)  # type: ignore[arg-type]
        def test_x(x: int) -> None:
            pass

    assert str(excinfo.value) == ("In test_x: expected Sequence or boolean for indirect, got int")


def test_bool_is_checked_before_sequence() -> None:
    """``True``/``False`` are not Sequences, but the order still has to be pytest's — a
    reversed check would try to iterate the bool."""

    @parametrize("a", [1], indirect=False)
    def test_x(a: int) -> None:
        pass

    assert _indirect_names(test_x) == frozenset()


def test_an_indirect_fixture_receives_request_param(tmp_path: Any) -> None:
    """End to end through the real worker, not through the metadata."""
    module = tmp_path / "test_indirect_e2e.py"
    _ = module.write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def doubled(request):\n"
        "    return request.param * 2\n\n\n"
        '@pytest.mark.parametrize("doubled", [3, 5], indirect=True)\n'
        "def test_x(doubled):\n"
        "    assert doubled in (6, 10)\n",
        encoding="utf-8",
    )
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "rustest", "-q", str(module)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stderr, proc.stderr


def test_the_manifest_reports_an_indirect_name_as_a_fixture() -> None:
    """`_fixture_names` subtracts only the *direct* parametrized names, because an indirect
    one really is resolved through a fixture — that is pytest's ``fixturenames``."""
    from rustest._v2_worker import _fixture_names

    @parametrize("routed,plain", [(1, 2)], indirect=["routed"])
    def test_x(routed: int, plain: int, tmp_path: Any) -> None:
        pass

    direct = frozenset({"plain"})
    assert _fixture_names(test_x, "test_x", None, direct) == ["routed", "tmp_path"]


def test_class_level_indirect_reaches_the_methods() -> None:
    """A class-level `@parametrize(..., indirect=[...])` writes onto the **class**.

    Methods do not inherit class attributes, so `_indirect_names(method)` sees nothing and
    the routing has to be threaded down from `_collect_class` the way `outer_cases` already
    is. Member Designer's `TestAPIEndpoints` is exactly this shape, and it is why 120 of its
    tests still failed after the function-level implementation landed.
    """

    @parametrize("label,chosen", [("a", "alpha")], indirect=["chosen"])
    class TestBox:
        def test_one(self, label: str, chosen: object) -> None:
            pass

    assert _indirect_names(TestBox) == frozenset({"chosen"})
    assert _indirect_names(TestBox.test_one) == frozenset()


# --------------------------------------------------- `_validate_if_using_arg_names` (I3)


def test_a_parametrized_name_the_function_does_not_take_is_a_collection_error() -> None:
    """Port of `_pytest/python.py::Metafunc._validate_if_using_arg_names` (l. 1455-1483).

    rustest used to accept all four shapes silently — the test ran, **passed**, and the
    parameter was never delivered. Measured on pytest 8.4.2: each is one collection error,
    exit 2, with the message asserted here.
    """
    from rustest._v2_worker import _validate_if_using_arg_names, CollectionRefusal

    def test_x(other: int = 0) -> None:
        pass

    with pytest.raises(CollectionRefusal) as excinfo:
        _validate_if_using_arg_names(
            test_x, "test_x", None, frozenset({"nosuch"}), frozenset(), frozenset()
        )
    assert str(excinfo.value) == "In test_x: function uses no argument 'nosuch'"


def test_the_word_changes_for_an_indirect_name() -> None:
    from rustest._v2_worker import _validate_if_using_arg_names, CollectionRefusal

    def test_x() -> None:
        pass

    with pytest.raises(CollectionRefusal) as excinfo:
        _validate_if_using_arg_names(
            test_x, "test_x", None, frozenset({"nosuch"}), frozenset({"nosuch"}), frozenset()
        )
    assert str(excinfo.value) == "In test_x: function uses no fixture 'nosuch'"


def test_a_parameter_with_a_default_gets_its_own_message() -> None:
    from rustest._v2_worker import _validate_if_using_arg_names, CollectionRefusal

    def test_x(val: int = 7) -> None:
        pass

    with pytest.raises(CollectionRefusal) as excinfo:
        _validate_if_using_arg_names(
            test_x, "test_x", None, frozenset({"val"}), frozenset(), frozenset()
        )
    assert str(excinfo.value) == (
        "In test_x: function already takes an argument 'val' with a default value"
    )


def test_a_name_in_the_closure_is_accepted() -> None:
    from rustest._v2_worker import _validate_if_using_arg_names

    def test_x(used: int) -> None:
        pass

    _validate_if_using_arg_names(
        test_x, "test_x", None, frozenset({"used"}), frozenset(), frozenset({"used"})
    )
