"""User facing decorators mirroring the most common pytest helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Sequence, Tuple, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


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
    values: Sequence[Sequence[Any] | Mapping[str, Any]],
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


def _normalize_arg_names(arg_names: str | Sequence[str]) -> Tuple[str, ...]:
    if isinstance(arg_names, str):
        parts = [part.strip() for part in arg_names.split(",") if part.strip()]
        if not parts:
            msg = "parametrize() expected at least one argument name"
            raise ValueError(msg)
        return tuple(parts)
    return tuple(arg_names)


def _build_cases(
    names: Tuple[str, ...],
    values: Sequence[Sequence[Any] | Mapping[str, Any]],
    ids: Sequence[str] | None,
) -> Tuple[Dict[str, Any], ...]:
    case_payloads: list[Dict[str, Any]] = []
    if ids is not None and len(ids) != len(values):
        msg = "ids must match the number of value sets"
        raise ValueError(msg)

    for index, case in enumerate(values):
        if isinstance(case, Mapping):
            data = {name: case[name] for name in names}
        else:
            if len(case) != len(names):
                raise ValueError("Parametrized value does not match argument names")
            data = {name: case[pos] for pos, name in enumerate(names)}
        case_id = ids[index] if ids is not None else f"case_{index}"
        case_payloads.append({"id": case_id, "values": data})
    return tuple(case_payloads)
