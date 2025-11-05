"""User facing decorators mirroring the most common pytest helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])


def fixture(func: F) -> F:
    """Mark a function as a fixture."""

    setattr(func, "__rustest_fixture__", True)
    return func


def skip(reason: str | None = None) -> Callable[[F], F]:
    """Skip a test or fixture."""

    def decorator(func: F) -> F:
        setattr(func, "__rustest_skip__", reason or "skipped via rustest.skip")
        return func

    return decorator


def parametrize(
    arg_names: str | Sequence[str],
    values: Sequence[Sequence[object] | Mapping[str, object]],
    *,
    ids: Sequence[str] | None = None,
) -> Callable[[F], F]:
    """Parametrise a test function."""

    normalized_names = _normalize_arg_names(arg_names)

    def decorator(func: F) -> F:
        cases = _build_cases(normalized_names, values, ids)
        setattr(func, "__rustest_parametrization__", cases)
        return func

    return decorator


def _normalize_arg_names(arg_names: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(arg_names, str):
        parts = [part.strip() for part in arg_names.split(",") if part.strip()]
        if not parts:
            msg = "parametrize() expected at least one argument name"
            raise ValueError(msg)
        return tuple(parts)
    return tuple(arg_names)


def _build_cases(
    names: tuple[str, ...],
    values: Sequence[Sequence[object] | Mapping[str, object]],
    ids: Sequence[str] | None,
) -> tuple[dict[str, object], ...]:
    case_payloads: list[dict[str, object]] = []
    if ids is not None and len(ids) != len(values):
        msg = "ids must match the number of value sets"
        raise ValueError(msg)

    for index, case in enumerate(values):
        # Mappings are only treated as parameter mappings when there are multiple parameters
        # For single parameters, dicts/mappings are treated as values
        if isinstance(case, Mapping) and len(names) > 1:
            data = {name: case[name] for name in names}
        elif isinstance(case, tuple) and len(case) == len(names):
            # Tuples are unpacked to match parameter names (pytest convention)
            # This handles both single and multiple parameters
            data = {name: case[pos] for pos, name in enumerate(names)}
        else:
            # Everything else is treated as a single value
            # This includes: primitives, lists (even if len==names), dicts (single param), objects
            if len(names) == 1:
                data = {names[0]: case}
            else:
                raise ValueError("Parametrized value does not match argument names")
        case_id = ids[index] if ids is not None else f"case_{index}"
        case_payloads.append({"id": case_id, "values": data})
    return tuple(case_payloads)
