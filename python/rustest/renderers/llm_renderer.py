"""JSONL renderer for LLM tool consumption.

Buffers test results during execution and emits one JSON object per line at
``finalize``: a ``meta`` header, ``error``/``fail`` lines, optional ``skip``
lines (``-v``), and a ``summary`` sentinel last. See
docs/superpowers/specs/2026-07-23-llm-jsonl-output-design.md.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import IO, Any

from . import _llm_extract as ex
from ._llm_schema import SCHEMA_VERSION

_CAPTURE_MAX_LINES = 50


class LlmRenderer:
    """Event consumer that emits deterministic JSONL at finalize."""

    def __init__(
        self,
        *,
        verbosity: int = 0,
        full: bool = False,
        root: str | None = None,
        output: IO[str] | None = None,
    ) -> None:
        super().__init__()
        self._verbosity = verbosity
        self._full = full
        self._root = root
        self._output: IO[str] = output if output is not None else sys.stdout

        # (test_id, file_path, test_name, message)
        self._failures: list[tuple[str, str, str, str]] = []
        # (test_id, reason)
        self._skips: list[tuple[str, str]] = []
        # (path, message)
        self._collection_errors: list[tuple[str, str]] = []

    # -- event intake -------------------------------------------------------

    def handle(self, event: Any) -> None:  # noqa: ANN401
        name = type(event).__name__
        if name.endswith("TestCompletedEvent"):
            if event.status == "failed":
                self._failures.append(
                    (event.test_id, event.file_path, event.test_name, event.message or "")
                )
            elif event.status == "skipped":
                self._skips.append((event.test_id, event.message or ""))
        elif name.endswith("CollectionErrorEvent"):
            self._collection_errors.append((event.path, event.message))

    # -- emission -----------------------------------------------------------

    def finalize(self, report: Any) -> None:  # noqa: ANN401
        self._emit({"t": "meta", "v": SCHEMA_VERSION, "tool": "rustest", "version": _pkg_version()})

        for obj in self._error_objects():
            self._emit(obj)
        for obj in self._fail_objects(report):
            self._emit(obj)
        if self._verbosity >= 1:
            for obj in self._skip_objects():
                self._emit(obj)

        self._emit(self._summary_object(report))

    def _emit(self, obj: dict[str, object]) -> None:
        self._output.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n")

    # -- object builders ----------------------------------------------------

    def _error_objects(self) -> list[dict[str, object]]:
        objs: list[dict[str, object]] = []
        for path, message in self._collection_errors:
            error, msg = ex.extract_error_and_msg(message)
            objs.append(
                {
                    "t": "error",
                    "path": ex.normalize_path(path, root=self._root),
                    "error": error or "Error",
                    "msg": msg,
                }
            )
        objs.sort(key=lambda o: o["path"])  # type: ignore[arg-type,return-value]
        return objs

    def _fail_objects(self, report: Any) -> list[dict[str, object]]:  # noqa: ANN401
        capture = {(r.name, r.path): r for r in report.results}
        objs: list[dict[str, object]] = []
        for test_id, file_path, test_name, message in self._failures:
            nid = ex.node_id(test_id, root=self._root)
            error, msg = ex.extract_error_and_msg(message)
            obj: dict[str, object] = {
                "t": "fail",
                "id": nid,
                "line": ex.extract_line(message) or 0,
                "error": error or "Error",
                "msg": msg,
            }
            pair = ex.extract_expected_actual(message)
            if pair is not None:
                obj["expected"], obj["actual"] = pair
            self._attach_capture(obj, capture.get((test_name, file_path)))
            self._attach_verbose(obj, message)
            objs.append(obj)
        objs.sort(key=lambda o: (ex.file_of(o["id"]), o["line"]))  # type: ignore[index,arg-type]
        return objs

    def _skip_objects(self) -> list[dict[str, object]]:
        objs: list[dict[str, object]] = [
            {"t": "skip", "id": ex.node_id(tid, root=self._root), "reason": reason}
            for tid, reason in self._skips
        ]
        objs.sort(key=lambda o: o["id"])  # type: ignore[arg-type,return-value]
        return objs

    def _summary_object(self, report: Any) -> dict[str, object]:  # noqa: ANN401
        summary: dict[str, object] = {
            "t": "summary",
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "errors": len(report.collection_errors),
            "duration": round(report.duration, 3),
        }
        rerun = [ex.node_id(tid, root=self._root) for tid, _, _, _ in self._failures]
        rerun += [ex.normalize_path(p, root=self._root) for p, _ in self._collection_errors]
        if rerun:
            summary["rerun"] = rerun
        return summary

    # -- helpers filled in by later tasks -----------------------------------

    def _attach_capture(self, obj: dict[str, object], result: Any) -> None:  # noqa: ANN401
        if result is None:
            return
        max_lines = 10**9 if self._full else _CAPTURE_MAX_LINES
        for stream in ("stdout", "stderr"):
            raw = getattr(result, stream, None)
            if not raw:
                continue
            kept, dropped = ex.truncate_tail(raw.strip(), max_lines)
            obj[stream] = kept
            if dropped:
                obj[f"{stream}_omitted"] = dropped

    def _attach_verbose(self, obj: dict[str, object], message: str) -> None:
        if self._verbosity >= 1:
            code = ex.extract_code(message)
            if code is not None:
                obj["code"] = code
        if self._verbosity >= 2:
            frames = ex.extract_frames(message, root=self._root)
            if frames:
                obj["frames"] = frames


def _pkg_version() -> str:
    try:
        return version("rustest")
    except PackageNotFoundError:  # pragma: no cover - dev fallback
        return "0.0.0"
