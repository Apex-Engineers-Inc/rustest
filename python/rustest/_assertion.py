r"""The runtime half of assertion rewriting: everything the rewritten bytecode calls.

This module is a **port of pytest's assertion message format**, from three files of
pytest 8.4.2:

* ``_pytest/assertion/util.py`` — ``format_explanation`` and its mini-language
  (``_split_explanation`` / ``_format_lines``), ``assertrepr_compare``, and the
  ``_compare_eq_*`` / ``_diff_text`` / ``_set_one_sided_diff`` / ``_notin_text`` family that
  turns a failed ``==``/``in`` into the multi-line diff a reader actually navigates by;
* ``_pytest/assertion/rewrite.py`` — the handful of functions the *generated* code calls by
  name (``_saferepr``, ``_format_assertmsg``, ``_format_explanation``, ``_format_boolop``,
  ``_call_reprcompare``, ``_should_repr_global_name``);
* ``_pytest/_io/saferepr.py`` — ``SafeRepr``, because every operand in a message goes
  through it and its truncation rules are part of the format.

**Why port rather than import.** A v2 worker must never pull real pytest into its process —
it installs rustest's own ``pytest`` and ``_pytest`` shims before importing a single test
module (``_v2_worker.py::install_pytest_shim``), so ``from _pytest.assertion import util``
inside a worker would either fail or, worse, resolve to the stub. The format is therefore
copied, with the provenance of each function named in its own docstring so a future reader
can diff it against the version of pytest they have.

**Deliberate divergences**, each because rustest has no equivalent knob rather than because
the port is unfinished:

* **verbosity is fixed at 0.** pytest reads ``config.get_verbosity(VERBOSITY_ASSERTIONS)``,
  which ``-v``/``-vv`` raise, and the raised levels print full diffs and un-truncated reprs.
  rustest's ``-v`` is the per-test progress column and does not reach here, so
  :data:`VERBOSITY` is a module constant. Every ``verbose`` parameter is kept in the ported
  signatures — dropping them would silently change which branch runs, and they are how the
  level gets wired when rustest grows the flag.
* **no syntax highlighting.** pytest passes ``config.get_terminal_writer()._highlight``;
  this passes :func:`dummy_highlighter`, which is pytest's own no-op used for exactly this
  purpose. rustest's failure sections are not coloured yet, so a highlighter would emit
  escape codes into a plain-text report.

(There used to be a third: *"no ``ApproxBase`` branch — ``rustest.approx`` has no
``_repr_compare``, so the branch would be dead code pretending to be a feature."* That was
true when it was written and stopped being true one commit later: Phase 4 Task 1's M9 fix
replaced the ``approx`` lookalike with a **port** of ``_pytest/python_api.py``, and every
``Approx*`` class in :mod:`rustest.approx` has carried ``_repr_compare`` since. The comment
outlived the reason for it, and with it the wiring: a failing ``assert x == approx(y)``
printed the generic explanation while the table pytest prints sat unreachable in the port.
:func:`_compare_eq_any` now calls it, exactly where pytest does.)

``running_on_ci`` is ported **including** its environment sniff, because it changes the
message (``Use -v to get more diff`` versus a full diff), and a differential test that
passed locally and failed in CI would be worse than either behaviour.
"""

from __future__ import annotations

import collections.abc
import os
import pprint
import reprlib
import types
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any, Final, TypeGuard, cast
from unicodedata import normalize

__all__ = [
    "assertrepr_compare",
    "format_explanation",
    "saferepr",
    # The names the **rewritten bytecode** calls, by string, through the `@rustest_ar` alias
    # (`_assertion_rewrite.py`). They are exported rather than left private because a type
    # checker cannot see a reference that only exists inside a generated AST, and would
    # otherwise report every one of them as dead code — burying a real dead helper among five
    # false ones. `test_every_helper_the_rewriter_emits_exists` pins the set from the other
    # side, by walking the generated AST.
    "_call_reprcompare",
    "_format_assertmsg",
    "_format_boolop",
    "_format_explanation",
    "_saferepr",
    "_should_repr_global_name",
]

#: pytest's assertion verbosity, pinned at its default. See the module docstring.
VERBOSITY: Final = 0

#: ``_pytest/_io/saferepr.py`` l. 96.
DEFAULT_REPR_MAX_SIZE: Final = 240


# ---------------------------------------------------------------------------
# saferepr — port of `_pytest/_io/saferepr.py`
# ---------------------------------------------------------------------------


def _try_repr_or_str(obj: object) -> str:
    try:
        return repr(obj)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return f'{type(obj).__name__}("{obj}")'


def _format_repr_exception(exc: BaseException, obj: object) -> str:
    try:
        exc_info = _try_repr_or_str(exc)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as inner_exc:
        exc_info = f"unpresentable exception ({_try_repr_or_str(inner_exc)})"
    return f"<[{exc_info} raised in repr()] {type(obj).__name__} object at 0x{id(obj):x}>"


def _ellipsize(s: str, maxsize: int) -> str:
    if len(s) > maxsize:
        i = max(0, (maxsize - 3) // 2)
        j = max(0, maxsize - 3 - i)
        return s[:i] + "..." + s[len(s) - j :]
    return s


class SafeRepr(reprlib.Repr):
    """``reprlib.Repr`` that truncates and never raises.

    Port of ``_pytest/_io/saferepr.py::SafeRepr``. The "never raises" half is the point: an
    object whose ``__repr__`` is broken must not turn a *test failure* into a second,
    confusing exception raised while formatting the first one.
    """

    def __init__(self, maxsize: int | None, use_ascii: bool = False) -> None:
        super().__init__()
        # `maxstring` is used by the superclass and must be an int; a very large number
        # stands in for "no limit" when maxsize is None.
        self.maxstring: int = maxsize if maxsize is not None else 1_000_000_000
        self.maxsize: int | None = maxsize
        self.use_ascii: bool = use_ascii

    def repr(self, x: object) -> str:
        try:
            s = ascii(x) if self.use_ascii else super().repr(x)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            s = _format_repr_exception(exc, x)
        if self.maxsize is not None:
            s = _ellipsize(s, self.maxsize)
        return s

    def repr_instance(self, x: object, level: int) -> str:
        try:
            s = repr(x)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            s = _format_repr_exception(exc, x)
        if self.maxsize is not None:
            s = _ellipsize(s, self.maxsize)
        return s


def saferepr(
    obj: object, maxsize: int | None = DEFAULT_REPR_MAX_SIZE, use_ascii: bool = False
) -> str:
    """``repr()`` that cannot raise and is bounded in length."""
    return SafeRepr(maxsize, use_ascii).repr(obj)


def saferepr_unlimited(obj: object, use_ascii: bool = True) -> str:
    """``saferepr`` with no truncation — pytest's ``-vv`` path, kept for completeness."""
    try:
        if use_ascii:
            return ascii(obj)
        return repr(obj)
    except Exception as exc:
        return _format_repr_exception(exc, obj)


# ---------------------------------------------------------------------------
# the explanation mini-language — port of `_pytest/assertion/util.py`
# ---------------------------------------------------------------------------


def dummy_highlighter(source: str, lexer: str = "python") -> str:
    """No-op highlighter. pytest's own, used here on every call — see the module docstring."""
    return source


def format_explanation(explanation: str) -> str:
    r"""Format an explanation.

    Port of ``_pytest/assertion/util.py::format_explanation``.

    Normally all embedded newlines are escaped; there are three exceptions — ``\n{``,
    ``\n}`` and ``\n~``. The first two carry *nested* explanations (the ``where``/``and``
    clauses the rewriter emits for calls and attributes), the third lets one explanation
    span several lines, which is how a diff survives.
    """
    return "\n".join(_format_lines(_split_explanation(explanation)))


def _split_explanation(explanation: str) -> list[str]:
    r"""Split on ``\n{``, ``\n}``, ``\n~`` and ``\n>``; escape every other newline."""
    raw_lines = (explanation or "").split("\n")
    lines = [raw_lines[0]]
    for values in raw_lines[1:]:
        if values and values[0] in ["{", "}", "~", ">"]:
            lines.append(values)
        else:
            lines[-1] += "\\n" + values
    return lines


def _format_lines(lines: Sequence[str]) -> list[str]:
    """Turn the ``{``/``}``/``~`` markers into ``where ...`` / ``and ...`` / indentation.

    The two magic strings are pytest's and the alignment is load-bearing: ``"and   "`` has
    three trailing spaces so it lines up under ``"where "``.
    """
    result = list(lines[:1])
    stack = [0]
    stackcnt = [0]
    for line in lines[1:]:
        if line.startswith("{"):
            s = "and   " if stackcnt[-1] else "where "
            stack.append(len(result))
            stackcnt[-1] += 1
            stackcnt.append(0)
            result.append(" +" + "  " * (len(stack) - 1) + s + line[1:])
        elif line.startswith("}"):
            _ = stack.pop()
            _ = stackcnt.pop()
            result[stack[-1]] += line[1:]
        else:
            assert line[0] in ["~", ">"]
            stack[-1] += 1
            indent = len(stack) if line.startswith("~") else len(stack) - 1
            result.append("  " * indent + line[1:])
    assert len(stack) == 1
    return result


# ---------------------------------------------------------------------------
# type probes — port of `_pytest/assertion/util.py`
# ---------------------------------------------------------------------------


def issequence(x: Any) -> bool:
    return isinstance(x, collections.abc.Sequence) and not isinstance(x, str)


def istext(x: Any) -> bool:
    return isinstance(x, str)


def isdict(x: Any) -> TypeGuard[dict[Any, Any]]:
    return isinstance(x, dict)


def isset(x: Any) -> TypeGuard[AbstractSet[Any]]:
    return isinstance(x, (set, frozenset))


def isnamedtuple(obj: Any) -> bool:
    # The narrowing from `Any` to `tuple[Unknown, ...]` is what basedpyright objects to, not
    # anything about the call; ignored rather than cast so the line stays byte-identical to
    # pytest's, which is the property that keeps this module diffable against upstream. Same
    # convention as `_assertion_rewrite.py` l. 574/578.
    return isinstance(obj, tuple) and getattr(obj, "_fields", None) is not None  # pyright: ignore[reportUnknownArgumentType]


def isdatacls(obj: Any) -> bool:
    return getattr(obj, "__dataclass_fields__", None) is not None


def isattrs(obj: Any) -> bool:
    return getattr(obj, "__attrs_attrs__", None) is not None


def isiterable(obj: Any) -> bool:
    """Duck-typed, and the ``try`` is pytest's: an object may raise from ``__iter__``.

    A broken ``__iter__`` must not turn a *test failure* into a second exception raised while
    formatting the first one — the same rule :class:`SafeRepr` follows for ``__repr__``.
    """
    try:
        _ = iter(obj)
        return not istext(obj)
    except Exception:
        return False


def has_default_eq(obj: object) -> bool:
    """Does *obj* use a generated ``__eq__`` (dataclass/attrs) or its own?

    Port of ``_pytest/assertion/util.py::has_default_eq``. A hand-written ``__eq__`` means
    the field-by-field drill-down would be describing a comparison the class does not
    actually perform, so pytest skips it — and so does this.
    """
    eq = getattr(type(obj), "__eq__", None)
    code = getattr(eq, "__code__", None)
    filename = getattr(code, "co_filename", None)
    if filename is not None:
        if isattrs(obj):
            return "attrs generated " in filename
        return filename == "<string>"
    return True


def running_on_ci() -> bool:
    """Ported verbatim: it selects between a full diff and ``Use -v to get more diff``."""
    return any(var in os.environ for var in ("CI", "BUILD_NUMBER"))


# ---------------------------------------------------------------------------
# assertrepr_compare and friends — port of `_pytest/assertion/util.py`
# ---------------------------------------------------------------------------


def assertrepr_compare(
    op: str, left: Any, right: Any, verbose: int = VERBOSITY
) -> list[str] | None:
    """The specialised explanation for a failed comparison, or ``None``.

    Port of ``_pytest/assertion/util.py::assertrepr_compare`` with ``config`` removed (see
    the module docstring). The return value is ``[summary, "", *detail_lines]``, and the
    blank second line is pytest's — it is what puts the empty ``E`` line between the
    ``assert ...`` summary and the diff.
    """
    # Strings which normalise equal are hard to tell apart when printed; `ascii()` makes
    # the difference visible (pytest issue #3246).
    use_ascii = (
        isinstance(left, str)
        and isinstance(right, str)
        and normalize("NFD", left) == normalize("NFD", right)
    )

    if verbose > 1:
        left_repr = saferepr_unlimited(left, use_ascii=use_ascii)
        right_repr = saferepr_unlimited(right, use_ascii=use_ascii)
    else:
        # pytest's own comment calls the "15 chars indentation" wrong; it is kept because
        # changing it would change every truncated message.
        maxsize = (80 - 15 - len(op) - 2) // 2
        left_repr = saferepr(left, maxsize=maxsize, use_ascii=use_ascii)
        right_repr = saferepr(right, maxsize=maxsize, use_ascii=use_ascii)

    summary = f"{left_repr} {op} {right_repr}"

    explanation: list[str] | None = None
    try:
        if op == "==":
            explanation = _compare_eq_any(left, right, verbose)
        elif op == "not in":
            if istext(left) and istext(right):
                explanation = _notin_text(left, right, verbose)
        elif op == "!=":
            if isset(left) and isset(right):
                explanation = ["Both sets are equal"]
        elif op == ">=":
            if isset(left) and isset(right):
                explanation = _compare_gte_set(left, right)
        elif op == "<=":
            if isset(left) and isset(right):
                explanation = _compare_lte_set(left, right)
        elif op == ">":
            if isset(left) and isset(right):
                explanation = _compare_gt_set(left, right)
        elif op == "<":
            if isset(left) and isset(right):
                explanation = _compare_lt_set(left, right)
    except Exception as exc:
        # pytest re-derives a crash repr through its own ExceptionInfo machinery, which is
        # not ported; the wording below keeps the same shape and says the same thing — a
        # faulty `__repr__` must never replace the user's failure with ours.
        explanation = [
            f"(rustest assertion plugin: representation of details failed: {exc!r}.",
            " Probably an object has a faulty __repr__.)",
        ]

    if not explanation:
        return None

    if explanation[0] != "":
        explanation = ["", *explanation]
    return [summary, *explanation]


def _compare_eq_any(left: Any, right: Any, verbose: int = 0) -> list[str]:
    explanation: list[str] = []
    if istext(left) and istext(right):
        explanation = _diff_text(left, right, verbose)
    else:
        # `_pytest/assertion/util.py::_compare_eq_any` l. 255-262. Imported inside the
        # function, as pytest imports `ApproxBase` inside its own, because the assertion
        # runtime is on the hot path of every rewritten module and `rustest.approx` is not.
        from rustest.approx import ApproxBase

        if isinstance(left, ApproxBase) or isinstance(right, ApproxBase):
            # "Although the common order should be obtained == expected, this ensures both
            # ways" -- pytest's own comment. Either operand may be the approx.
            approx_side = left if isinstance(left, ApproxBase) else right
            other_side = right if isinstance(left, ApproxBase) else left
            # `_repr_compare` is pytest's own call site for a deliberately private hook:
            # `ApproxBase` publishes it *for* the assertion layer and nothing else.
            explanation = approx_side._repr_compare(other_side)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        elif type(left) is type(right) and (isdatacls(left) or isattrs(left) or isnamedtuple(left)):
            # Unlike dataclasses/attrs, namedtuples compare only field values; this branch
            # handles the same-type case only, exactly as pytest's does.
            explanation = _compare_eq_cls(left, right, verbose)
        elif issequence(left) and issequence(right):
            explanation = _compare_eq_sequence(left, right, verbose)
        elif isset(left) and isset(right):
            explanation = _compare_eq_set(left, right)
        elif isdict(left) and isdict(right):
            explanation = _compare_eq_dict(left, right, verbose)

        if isiterable(left) and isiterable(right):
            explanation.extend(
                _compare_eq_iterable(
                    cast("Iterable[Any]", left), cast("Iterable[Any]", right), verbose
                )
            )

    return explanation


def _diff_text(left: str, right: str, verbose: int = 0) -> list[str]:
    """The ndiff between two strings, trimmed of shared head and tail unless ``-v``."""
    from difflib import ndiff

    explanation: list[str] = []

    if verbose < 1:
        i = 0  # in case either side has zero length
        for i in range(min(len(left), len(right))):
            if left[i] != right[i]:
                break
        if i > 42:
            i -= 10  # provide some context
            explanation = [f"Skipping {i} identical leading characters in diff, use -v to show"]
            left = left[i:]
            right = right[i:]
        if len(left) == len(right):
            for i in range(len(left)):
                if left[-i] != right[-i]:
                    break
            if i > 42:
                i -= 10
                explanation += [
                    f"Skipping {i} identical trailing characters in diff, use -v to show"
                ]
                left = left[:-i]
                right = right[:-i]
    keepends = True
    if left.isspace() or right.isspace():
        left = repr(str(left))
        right = repr(str(right))
        explanation += ["Strings contain only whitespace, escaping them using repr()"]
    # `right` is the expected base against which `left` is compared (pytest issue #3333).
    explanation.extend(
        dummy_highlighter(
            "\n".join(
                line.strip("\n")
                for line in ndiff(right.splitlines(keepends), left.splitlines(keepends))
            ),
            lexer="diff",
        ).splitlines()
    )
    return explanation


def _compare_eq_iterable(left: Iterable[Any], right: Iterable[Any], verbose: int = 0) -> list[str]:
    if verbose <= 0 and not running_on_ci():
        return ["Use -v to get more diff"]
    import difflib

    # pytest uses its own vendored `PrettyPrinter` here; `pprint.pformat` is the stdlib one
    # pytest's is derived from. The two differ only in how they wrap very wide containers,
    # and this branch is unreachable at the pinned verbosity except on CI — recorded in the
    # parity table rather than hidden.
    left_formatting = pprint.pformat(left).splitlines()
    right_formatting = pprint.pformat(right).splitlines()

    explanation = ["", "Full diff:"]
    explanation.extend(
        dummy_highlighter(
            "\n".join(line.rstrip() for line in difflib.ndiff(right_formatting, left_formatting)),
            lexer="diff",
        ).splitlines()
    )
    return explanation


def _compare_eq_sequence(left: Sequence[Any], right: Sequence[Any], verbose: int = 0) -> list[str]:
    comparing_bytes = isinstance(left, bytes) and isinstance(right, bytes)
    explanation: list[str] = []
    len_left = len(left)
    len_right = len(right)
    for i in range(min(len_left, len_right)):
        if left[i] != right[i]:
            if comparing_bytes:
                # A bytes *index* is an int; a one-element slice keeps the ascii form
                # (pytest issue #5260).
                left_value = left[i : i + 1]
                right_value = right[i : i + 1]
            else:
                left_value = left[i]
                right_value = right[i]
            explanation.append(
                f"At index {i} diff: {right_repr_of(left_value)} != {right_repr_of(right_value)}"
            )
            break

    if comparing_bytes:
        return explanation

    len_diff = len_left - len_right
    if len_diff:
        if len_diff > 0:
            dir_with_more = "Left"
            extra = saferepr(left[len_right])
        else:
            len_diff = 0 - len_diff
            dir_with_more = "Right"
            extra = saferepr(right[len_left])

        if len_diff == 1:
            explanation += [f"{dir_with_more} contains one more item: {extra}"]
        else:
            explanation += [
                f"{dir_with_more} contains {len_diff} more items, first extra item: {extra}"
            ]
    return explanation


def right_repr_of(value: object) -> str:
    """``repr`` as ``_compare_eq_sequence`` calls it (pytest wraps it in the highlighter)."""
    return dummy_highlighter(repr(value))


def _compare_eq_set(left: AbstractSet[Any], right: AbstractSet[Any]) -> list[str]:
    return [*_set_one_sided_diff("left", left, right), *_set_one_sided_diff("right", right, left)]


def _compare_gt_set(left: AbstractSet[Any], right: AbstractSet[Any]) -> list[str]:
    return _compare_gte_set(left, right) or ["Both sets are equal"]


def _compare_lt_set(left: AbstractSet[Any], right: AbstractSet[Any]) -> list[str]:
    return _compare_lte_set(left, right) or ["Both sets are equal"]


def _compare_gte_set(left: AbstractSet[Any], right: AbstractSet[Any]) -> list[str]:
    return _set_one_sided_diff("right", right, left)


def _compare_lte_set(left: AbstractSet[Any], right: AbstractSet[Any]) -> list[str]:
    return _set_one_sided_diff("left", left, right)


def _set_one_sided_diff(posn: str, set1: AbstractSet[Any], set2: AbstractSet[Any]) -> list[str]:
    explanation: list[str] = []
    diff = set1 - set2
    if diff:
        explanation.append(f"Extra items in the {posn} set:")
        for item in diff:
            explanation.append(dummy_highlighter(saferepr(item)))
    return explanation


def _compare_eq_dict(
    left: Mapping[Any, Any], right: Mapping[Any, Any], verbose: int = 0
) -> list[str]:
    explanation: list[str] = []
    set_left = set(left)
    set_right = set(right)
    common = set_left.intersection(set_right)
    same = {k: left[k] for k in common if left[k] == right[k]}
    if same and verbose < 2:
        explanation += [f"Omitting {len(same)} identical items, use -vv to show"]
    elif same:
        explanation += ["Common items:"]
        explanation += pprint.pformat(same).splitlines()
    diff = {k for k in common if left[k] != right[k]}
    if diff:
        explanation += ["Differing items:"]
        for k in diff:
            explanation += [saferepr({k: left[k]}) + " != " + saferepr({k: right[k]})]
    extra_left = set_left - set_right
    len_extra_left = len(extra_left)
    if len_extra_left:
        explanation.append(
            f"Left contains {len_extra_left} more item{'' if len_extra_left == 1 else 's'}:"
        )
        explanation.extend(pprint.pformat({k: left[k] for k in extra_left}).splitlines())
    extra_right = set_right - set_left
    len_extra_right = len(extra_right)
    if len_extra_right:
        explanation.append(
            f"Right contains {len_extra_right} more item{'' if len_extra_right == 1 else 's'}:"
        )
        explanation.extend(pprint.pformat({k: right[k] for k in extra_right}).splitlines())
    return explanation


def _compare_eq_cls(left: Any, right: Any, verbose: int) -> list[str]:
    if not has_default_eq(left):
        return []
    if isdatacls(left):
        import dataclasses

        fields_to_check = [info.name for info in dataclasses.fields(left) if info.compare]
    elif isattrs(left):
        fields_to_check = [field.name for field in left.__attrs_attrs__ if getattr(field, "eq")]
    elif isnamedtuple(left):
        fields_to_check = list(left._fields)
    else:  # pragma: no cover - `_compare_eq_any` only routes the three shapes above here
        raise AssertionError("_compare_eq_cls called on an unsupported type")

    indent = "  "
    same: list[str] = []
    diff: list[str] = []
    for field in fields_to_check:
        if getattr(left, field) == getattr(right, field):
            same.append(field)
        else:
            diff.append(field)

    explanation: list[str] = []
    if same or diff:
        explanation += [""]
    if same and verbose < 2:
        explanation.append(f"Omitting {len(same)} identical items, use -vv to show")
    elif same:
        explanation += ["Matching attributes:"]
        explanation += pprint.pformat(same).splitlines()
    if diff:
        explanation += ["Differing attributes:"]
        explanation += pprint.pformat(diff).splitlines()
        for field in diff:
            field_left = getattr(left, field)
            field_right = getattr(right, field)
            explanation += [
                "",
                f"Drill down into differing attribute {field}:",
                f"{indent}{field}: {field_left!r} != {field_right!r}",
            ]
            explanation += [
                indent + line for line in _compare_eq_any(field_left, field_right, verbose)
            ]
    return explanation


def _notin_text(term: str, text: str, verbose: int = 0) -> list[str]:
    index = text.find(term)
    head = text[:index]
    tail = text[index + len(term) :]
    correct_text = head + tail
    diff = _diff_text(text, correct_text, verbose)
    newdiff = [f"{saferepr(term, maxsize=42)} is contained here:"]
    for line in diff:
        if line.startswith("Skipping"):
            continue
        if line.startswith("- "):
            continue
        if line.startswith("+ "):
            newdiff.append("  " + line[2:])
        else:
            newdiff.append(line)
    return newdiff


# ---------------------------------------------------------------------------
# what the rewritten bytecode calls — port of `_pytest/assertion/rewrite.py`
# ---------------------------------------------------------------------------
#
# These names are referenced *by string* from generated code (`_assertion_rewrite.py`
# emits `@rustest_ar.<name>(...)`), so renaming one is a silent break that only a
# rewritten module discovers at failure time.  `test_generated_code_only_calls_exported
# _helpers` pins the set both ways.


#: The generated code calls ``_format_explanation``; pytest's ``rewrite.py`` binds the same
#: alias (``from _pytest.assertion.util import format_explanation as _format_explanation``).
#: Kept as an alias rather than renaming the public function, because the leading underscore
#: means "emitted by the rewriter" here, not "private".
_format_explanation = format_explanation


def _saferepr(obj: object) -> str:
    r"""``saferepr`` with newlines escaped, because ``\n{`` is format-explanation syntax.

    A custom ``__repr__`` containing ``\n{`` or ``\n}`` — a JSON-ish repr, say — would
    otherwise be read as a nested ``where`` clause and shred the message.
    """
    if isinstance(obj, types.MethodType):
        # For bound methods, skip the redundant `<bound method ...>` noise.
        return obj.__name__
    maxsize = _get_maxsize_for_saferepr()
    if not maxsize:
        return saferepr_unlimited(obj).replace("\n", "\\n")
    return saferepr(obj, maxsize=maxsize).replace("\n", "\\n")


def _get_maxsize_for_saferepr() -> int | None:
    """pytest reads verbosity from config; :data:`VERBOSITY` stands in. See the module docs."""
    if VERBOSITY >= 2:
        return None
    if VERBOSITY >= 1:
        return DEFAULT_REPR_MAX_SIZE * 10
    return DEFAULT_REPR_MAX_SIZE


def _format_assertmsg(obj: object) -> str:
    r"""Format the user's own ``assert cond, msg`` message.

    For a string this replaces newlines with ``\n~`` so :func:`format_explanation` keeps
    them; anything else goes through ``saferepr`` first. ``%`` is doubled because the
    explanation is later ``%``-formatted against the operand dict.
    """
    replaces = [("\n", "\n~"), ("%", "%%")]
    if not isinstance(obj, str):
        obj = saferepr(obj, _get_maxsize_for_saferepr())
        replaces.append(("\\n", "\n~"))
    text = obj
    for r1, r2 in replaces:
        text = text.replace(r1, r2)
    return text


def _should_repr_global_name(obj: object) -> bool:
    """Is *obj* worth showing by value rather than by its source name?

    pytest additionally special-cases its own ``FixtureFunctionDefinition`` here; rustest
    has no equivalent object at module scope, so a callable is always shown by name.
    """
    if callable(obj):
        return False
    try:
        return not hasattr(obj, "__name__")
    except Exception:
        return True


def _format_boolop(explanations: Iterable[object], is_or: int) -> str:
    explanation = (
        "(" + ((is_or and " or ") or " and ").join(str(item) for item in explanations) + ")"
    )
    return explanation.replace("%", "%%")


def _call_reprcompare(
    ops: Sequence[str],
    results: Sequence[bool],
    expls: Sequence[str],
    each_obj: Sequence[object],
) -> str:
    """Pick the first failing link of a comparison chain and explain it.

    ``a < b < c`` produces one entry per ``op``; the first falsy result is the one the
    message is about, which is why ``assert 1 < x < 10`` with ``x = 20`` reports
    ``assert 20 < 10`` and not the whole chain.

    Where pytest then consults the ``pytest_assertrepr_compare`` hook (via
    ``util._reprcompare``, installed per-test by its plugin), this calls
    :func:`assertrepr_compare` directly: rustest has no plugin hooks, so the indirection
    would have exactly one implementation and one possible answer.
    """
    i = 0
    expl = expls[0] if expls else ""
    for i, res, expl in zip(range(len(ops)), results, expls):  # noqa: B007
        try:
            done = not res
        except Exception:
            done = True
        if done:
            break
    custom = _reprcompare(ops[i], each_obj[i], each_obj[i + 1])
    if custom is not None:
        return custom
    return expl


def _reprcompare(op: str, left: object, right: object) -> str | None:
    r"""``assertrepr_compare``, truncated and joined — a port of ``callbinrepr``.

    ``_pytest/assertion/__init__.py::pytest_runtest_protocol`` installs a local
    ``callbinrepr`` as ``util._reprcompare``, and its four steps are all load-bearing, in
    this order:

    1. **truncate** to 8 lines / 640 characters (:func:`_truncate_explanation`), unless
       running on CI. Skipping this would make every large diff diverge from pytest at
       exactly the point where the diff is worth reading;
    2. escape embedded newlines **within** each line (``"\n"`` -> ``"\\n"``), so a repr that
       itself spans lines cannot be mistaken for the line structure of the explanation;
    3. join with ``"\n~"``, which :func:`format_explanation` re-splits into continuation
       lines — this is what makes a dict or string diff render as separate lines instead of
       one line full of literal ``\n``;
    4. double every ``%``, because the result is ``%``-formatted one step later against the
       operand dict; an operand repr containing a percent sign would otherwise raise
       ``ValueError: unsupported format character``. pytest guards this on
       ``assertmode == "rewrite"``; rustest has only the rewrite mode, so it is
       unconditional.

    Where pytest reaches this through its ``pytest_assertrepr_compare`` hook chain, this
    calls :func:`assertrepr_compare` directly: rustest has no plugin hooks, so the
    indirection would have exactly one implementation and one possible answer.
    """
    lines = assertrepr_compare(op, left, right)
    if not lines:
        return None
    lines = _truncate_explanation(lines)
    return "\n~".join(line.replace("\n", "\\n") for line in lines).replace("%", "%%")


#: ``_pytest/assertion/truncate.py`` l. 14-16.
_TRUNCATION_MAX_LINES: Final = 8
_TRUNCATION_MAX_CHARS: Final = _TRUNCATION_MAX_LINES * 80
_TRUNCATION_USAGE_MSG: Final = "use '-vv' to show"


def _truncate_explanation(
    input_lines: list[str],
    max_lines: int = _TRUNCATION_MAX_LINES,
    max_chars: int = _TRUNCATION_MAX_CHARS,
) -> list[str]:
    """Port of ``_pytest/assertion/truncate.py::_truncate_explanation``.

    pytest skips truncation entirely at assertion verbosity >= 2 **or on CI**; the
    verbosity half is pinned off here (see the module docstring) and the CI half is
    reproduced, so a message is the same length in both places pytest would make it so.
    """
    if running_on_ci():
        return input_lines

    input_char_count = len("".join(input_lines))
    # pytest's own arithmetic and its comment: the truncation notice is itself at least 64
    # characters, +1 for a plural, +2 for a two-digit count, +3 for the "..." it appends.
    tolerable_max_chars = max_chars + 70
    tolerable_max_lines = max_lines + 2
    if len(input_lines) <= tolerable_max_lines and input_char_count <= tolerable_max_chars:
        return input_lines

    truncated_explanation = input_lines[:max_lines] if max_lines > 0 else list(input_lines)
    truncated_char = True
    if len("".join(truncated_explanation)) > tolerable_max_chars and max_chars > 0:
        truncated_explanation = _truncate_by_char_count(truncated_explanation, max_chars)
    else:
        truncated_char = False

    if truncated_explanation == input_lines:
        return truncated_explanation

    truncated_line_count = len(input_lines) - len(truncated_explanation)
    if truncated_explanation[-1]:
        truncated_explanation[-1] = truncated_explanation[-1] + "..."
        if truncated_char:
            truncated_line_count += 1
    else:
        truncated_explanation[-1] = "..."
    return [
        *truncated_explanation,
        "",
        "...Full output truncated ({} line{} hidden), {}".format(
            truncated_line_count,
            "" if truncated_line_count == 1 else "s",
            _TRUNCATION_USAGE_MSG,
        ),
    ]


def _truncate_by_char_count(input_lines: list[str], max_chars: int) -> list[str]:
    iterated_char_count = 0
    iterated_index = 0
    for iterated_index, input_line in enumerate(input_lines):
        if iterated_char_count + len(input_line) > max_chars:
            break
        iterated_char_count += len(input_line)

    truncated_result = input_lines[:iterated_index]
    final_line = input_lines[iterated_index]
    if final_line:
        final_line = final_line[: max_chars - iterated_char_count]
    truncated_result.append(final_line)
    return truncated_result
