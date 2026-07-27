"""The ``--real`` gate: run a real-world suite under pytest, then under rustest, and diff.

The corpus gates ask "does rustest reproduce pytest on a case somebody wrote to probe one
rule?". This one asks the question the rewrite was actually for: **does it reproduce pytest
on a suite nobody wrote for it.** A target is declared in ``conformance/real/<name>.toml``
(where the repo is, how to build an isolated environment for it, which paths to run, what
to do about its ``addopts``, and a per-repo ledger of known divergences) and graded on

* the **per-test-id status map** -- not just counts, because a suite where rustest passes a
  test pytest skips and skips a test pytest passes has matching counts and is broken;
* the **tally** derived from that map on both sides;
* the **process exit code**;

with wall-clock recorded for both runners. Every id-level or global disagreement must be
matched by a ledger entry carrying its *mechanism*; an unmatched one fails the gate, and a
ledger entry that matches nothing fails it too (the STALE rule the corpus ledgers use --
an inert waiver must not survive a fix unnoticed).

**Nothing here modifies a target repository.** OSS targets are shallow-cloned into a
gitignored work directory at a pinned revision, verified by SHA on every run. The local
target (Apex Member Designer) is used in place: pytest runs with ``-p no:cacheprovider`` so
no ``.pytest_cache`` appears, the reporting plugin is loaded from a temp directory via
``PYTHONPATH`` rather than dropped in the tree, and any ``.rustest_cache`` rustest creates
is removed afterwards unless it was already there.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from ._real_report_plugin import REPORT_ENV_VAR

#: Where the ``--real`` configs live, and where clones/venvs/wheels are staged.
REAL_DIR: Final = Path(__file__).resolve().parent.parent / "real"
WORK_DIR: Final = REAL_DIR / "_work"

#: The name the reporting plugin is written under inside the throwaway ``PYTHONPATH``
#: directory. Deliberately not importable from the harness package: putting
#: ``conformance/harness`` itself on a target's ``PYTHONPATH`` would expose ``ids``,
#: ``grade`` and ``runners`` as top-level modules and could shadow the target's own.
_PLUGIN_MODULE: Final = "_conformance_real_report"

#: Ledger keys that describe something other than a single test id.
_GLOBAL_KEYS: Final = ("exit_code", "tally", "deselected", "collection_errors", "ids")

#: How ids are compared. ``exact`` is the default and the only one that proves node-id
#: parity. ``strip_params`` is for a target whose **parametrize ids** diverge
#: systematically: the raw difference is then raised as its own graded, ledgerable ``ids``
#: problem (so it can never be silently absorbed, and goes stale the moment it is fixed),
#: and the outcome comparison continues at *function* granularity -- the multiset of
#: statuses under each ``file::func``. That still catches a test rustest fails and pytest
#: passes, a lost or extra parameter case, and every count change; what it cannot do is say
#: **which** parameter case moved, and it is blind to two cases of one function trading
#: statuses. Nothing else in the gate is relaxed.
_ID_POLICIES: Final = ("exact", "strip_params")


#: Decoding for every subprocess this module runs, passed explicitly at each call site.
#: `text=True` alone decodes with the *locale* encoding, which on Windows is cp1252 -- and a
#: real suite's output is not cp1252. jinja2's tests print bytes that raised
#: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x90` inside subprocess's reader
#: THREAD, which leaves `proc.stdout` as None and made the gate report
#: `TypeError: 'NoneType' object is not subscriptable` instead of a result. None of these
#: streams is graded -- they are only ever quoted back inside an error message -- so
#: replacing undecodable bytes loses nothing and cannot take the harness down.
_ENCODING: Final = "utf-8"
_ERRORS: Final = "replace"


class ConfigError(Exception):
    """A ``conformance/real/<name>.toml`` that cannot be read as a target declaration."""


def _table(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return {str(k): v for k, v in cast("dict[object, object]", value).items()}


def _string(data: Mapping[str, object], key: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key!r} must be a string")
    return value


def _int(data: Mapping[str, object], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int):
        raise ConfigError(f"{key!r} must be an integer")
    return value


def _strings(data: Mapping[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{key!r} must be an array of strings")
    out: list[str] = []
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, str):
            raise ConfigError(f"{key!r} must be an array of strings")
        out.append(item)
    return out


def _commands(data: Mapping[str, object], key: str) -> list[list[str]]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{key!r} must be an array of command arrays")
    out: list[list[str]] = []
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, list):
            raise ConfigError(f"{key!r} must be an array of command arrays")
        argv: list[str] = []
        for word in item:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(word, str):
                raise ConfigError(f"{key!r} commands must be arrays of strings")
            argv.append(word)
        out.append(argv)
    return out


def _ledger(data: Mapping[str, object], key: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, reason in _table(data, key).items():
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"[{key}] {name!r} needs a non-empty mechanism string")
        out[name] = reason
    return out


@dataclass(frozen=True)
class RealTarget:
    """One declared real-world target, fully resolved against the work directory."""

    name: str
    kind: str
    url: str
    tag: str
    rev: str
    repo: Path
    test_paths: list[str]
    venv: Path
    setup: list[list[str]]
    pythonpath: list[str]
    addopts_configured: list[str]
    addopts_replay: list[str]
    addopts_dropped: list[str]
    pytest_args: list[str]
    rustest_args: list[str]
    timeout_s: int
    id_policy: str = "exact"
    ids_ledger: dict[str, str] = field(default_factory=dict[str, str])
    global_ledger: dict[str, str] = field(default_factory=dict[str, str])
    #: Divergences the harness deliberately **compensates for** so the rest of the suite can
    #: be compared at all (a missing ini option papered over with an environment variable, a
    #: config value replayed on the command line). They are kept out of the graded ledgers on
    #: purpose: nothing observable remains for a ledger entry to describe, so a graded entry
    #: would simply go stale. They are printed with every verdict instead, because a
    #: compensation nobody can see is indistinguishable from a bug nobody found.
    notes: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def python(self) -> Path:
        return self.venv / "Scripts" / "python.exe" if os.name == "nt" else self.venv / "bin/python"


def load_target(name: str) -> RealTarget:
    """Parse ``conformance/real/<name>.toml`` into a :class:`RealTarget`."""
    path = REAL_DIR / f"{name}.toml"
    if not path.is_file():
        raise ConfigError(f"no such target config: {path}")
    try:
        data: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed target config {path}: {exc}") from exc

    repo_tbl = _table(data, "repo")
    env_tbl = _table(data, "env")
    addopts_tbl = _table(data, "addopts")
    run_tbl = _table(data, "run")
    div_tbl = _table(data, "divergence")

    kind = _string(repo_tbl, "kind")
    if kind not in ("oss", "local"):
        raise ConfigError(f"[repo] kind must be 'oss' or 'local', got {kind!r}")
    if kind == "local":
        repo = Path(_string(repo_tbl, "path")).resolve()
    else:
        repo = (WORK_DIR / name).resolve()

    for key in _table(div_tbl, "global"):
        if key not in _GLOBAL_KEYS:
            raise ConfigError(f"[divergence.global] unknown key {key!r}; expected {_GLOBAL_KEYS}")
    id_policy = _string(run_tbl, "id_policy", "exact")
    if id_policy not in _ID_POLICIES:
        raise ConfigError(f"[run] id_policy must be one of {_ID_POLICIES}, got {id_policy!r}")

    return RealTarget(
        name=name,
        kind=kind,
        url=_string(repo_tbl, "url", ""),
        tag=_string(repo_tbl, "tag", ""),
        rev=_string(repo_tbl, "rev", ""),
        repo=repo,
        test_paths=_strings(repo_tbl, "test_paths"),
        venv=(WORK_DIR / "_venvs" / name).resolve(),
        setup=_commands(env_tbl, "setup"),
        pythonpath=_strings(env_tbl, "pythonpath"),
        addopts_configured=_strings(addopts_tbl, "configured"),
        addopts_replay=_strings(addopts_tbl, "replay"),
        addopts_dropped=_strings(addopts_tbl, "dropped"),
        pytest_args=_strings(run_tbl, "pytest_args"),
        rustest_args=_strings(run_tbl, "rustest_args"),
        timeout_s=_int(run_tbl, "timeout_s", 3600),
        id_policy=id_policy,
        ids_ledger=_ledger(div_tbl, "ids"),
        global_ledger=_ledger(div_tbl, "global"),
        notes=_ledger(data, "notes"),
    )


# --------------------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------------------


def _run_command(argv: Sequence[str], cwd: Path, timeout_s: int = 1800) -> None:
    proc = subprocess.run(  # noqa: S603 - argv comes from a tracked config file
        list(argv),
        cwd=cwd,
        capture_output=True,
        timeout=timeout_s,
        check=False,
        text=True,
        encoding=_ENCODING,
        errors=_ERRORS,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"setup command failed (exit {proc.returncode}): {' '.join(argv)}\n"
            + f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )


def _git(args: Sequence[str], cwd: Path) -> str:
    proc = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=600,
        check=False,
        text=True,
        encoding=_ENCODING,
        errors=_ERRORS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr[-500:]}")
    return proc.stdout.strip()


def ensure_repo(target: RealTarget) -> None:
    """Make *target*'s source tree present and pinned, or raise.

    A ``local`` target is never touched: it must already exist, and the harness only
    checks that it does. An ``oss`` target is shallow-cloned at its declared tag on first
    use and its ``HEAD`` is verified against the declared commit on **every** run -- a
    stale or drifted work tree would silently change what the ledger describes.
    """
    if target.kind == "local":
        if not target.repo.is_dir():
            raise RuntimeError(f"local target {target.name} is missing: {target.repo}")
        return
    if not (target.repo / ".git").exists():
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        _run_command(
            ["git", "clone", "--depth", "1", "--branch", target.tag, target.url, target.name],
            cwd=WORK_DIR,
        )
    head = _git(["rev-parse", "HEAD"], cwd=target.repo)
    if head != target.rev:
        raise RuntimeError(
            f"{target.name}: work tree is at {head}, config pins {target.rev} "
            + f"(tag {target.tag}) -- delete {target.repo} and re-run to re-clone"
        )


def ensure_env(target: RealTarget, *, force: bool = False) -> None:
    """Create the target's isolated venv and install into it, if it isn't there already.

    Every command comes from the config's ``[env] setup``, with ``{py}``/``{venv}``/
    ``{repo}``/``{work}``/``{wheels}`` substituted. Nothing is ever installed into the
    rustest development venv or into a target repository's own environment.
    """
    if target.python.exists() and not force:
        return
    if force and target.venv.exists():
        shutil.rmtree(target.venv)
    subs = {
        "py": str(target.python),
        "venv": str(target.venv),
        "repo": str(target.repo),
        "work": str(WORK_DIR),
        "wheels": str(WORK_DIR / "_wheels"),
    }
    for argv in target.setup:
        _run_command([word.format(**subs) for word in argv], cwd=WORK_DIR)
    if not target.python.exists():
        raise RuntimeError(f"{target.name}: setup finished but {target.python} does not exist")


# --------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """One runner's view of one suite: id -> status, plus the process facts."""

    runner: str
    exit_code: int
    seconds: float
    statuses: dict[str, str]
    deselected: int
    collection_errors: list[str]

    def tally(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0,
            "error": 0,
        }
        for status in self.statuses.values():
            counts[status] = counts.get(status, 0) + 1
        counts["deselected"] = self.deselected
        counts["collection_errors"] = len(self.collection_errors)
        return counts


def _plugin_dir(tmp: Path) -> Path:
    """Copy the reporting plugin into an otherwise empty directory for ``PYTHONPATH``."""
    dest = tmp / "plugin"
    dest.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("_real_report_plugin.py")
    shutil.copyfile(source, dest / f"{_PLUGIN_MODULE}.py")
    return dest


def _env_for(target: RealTarget, extra_paths: Iterable[Path]) -> dict[str, str]:
    env = dict(os.environ)
    parts = [str(p) for p in extra_paths]
    parts += [str((target.repo / rel).resolve()) for rel in target.pythonpath]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _timed(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: int
) -> tuple[int, float, str]:
    start = time.perf_counter()
    proc = subprocess.run(  # noqa: S603
        list(argv),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        timeout=timeout_s,
        check=False,
        text=True,
        encoding=_ENCODING,
        errors=_ERRORS,
    )
    return proc.returncode, time.perf_counter() - start, proc.stdout[-4000:] + proc.stderr[-4000:]


def run_pytest_real(target: RealTarget, tmp: Path) -> RunOutcome:
    """Run the target's suite under real pytest, reading statuses from the plugin.

    ``-o addopts=<replay>`` is passed unconditionally. The target's own ``addopts`` are
    **not** silently honoured, because rustest does not honour ``addopts`` at all
    (``src/v2/config.rs`` parses the key; nothing applies it) -- leaving pytest to apply
    them would ask the two runners different questions. What the config declares as
    ``replay`` is handed to *both* runners on the command line instead, so the suite still
    runs the way its authors intended; what it declares as ``dropped`` is recorded in the
    report as a deliberate, per-flag documented omission.
    """
    report = tmp / "pytest-report.json"
    env = _env_for(target, [_plugin_dir(tmp)])
    env[REPORT_ENV_VAR] = str(report)
    argv = [
        str(target.python),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-p",
        _PLUGIN_MODULE,
        # Neutralized, not replayed, so the *only* place addopts can enter the comparison
        # is the command line both runners get. Replaying here as well would double-apply
        # every flag on the pytest side alone.
        "-o",
        "addopts=",
        *target.addopts_replay,
        *target.pytest_args,
        *target.test_paths,
    ]
    exit_code, seconds, tail = _timed(argv, target.repo, env, target.timeout_s)
    if not report.exists():
        raise RuntimeError(f"pytest wrote no report (exit {exit_code}):\n{tail[-2000:]}")
    data: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    statuses: dict[str, str] = {str(k): str(v) for k, v in dict(data["statuses"]).items()}
    return RunOutcome(
        runner="pytest",
        exit_code=exit_code,
        seconds=seconds,
        statuses=statuses,
        deselected=len(list(data["deselected"])),
        collection_errors=[str(x) for x in data["collection_errors"]],
    )


def run_rustest_real(target: RealTarget, tmp: Path) -> RunOutcome:
    """Run the target's suite under flagless ``rustest`` (the default v2 engine).

    ``--report-json`` is the entire read surface, exactly as in the corpus run gate: the
    terminal prose is worded differently by design, and worker stderr legitimately carries
    boundary teardown output on a green run.
    """
    report = tmp / "rustest-report.json"
    env = _env_for(target, [])
    cache = target.repo / ".rustest_cache"
    cache_preexisting = cache.exists()
    argv = [
        str(target.python),
        "-m",
        "rustest",
        "--report-json",
        str(report),
        *target.addopts_replay,
        *target.rustest_args,
        *target.test_paths,
    ]
    try:
        exit_code, seconds, tail = _timed(argv, target.repo, env, target.timeout_s)
    finally:
        if not cache_preexisting and cache.exists():
            # The target tree is read-only to this harness; rustest's cache directory is
            # the one thing a run leaves behind, so it is removed again unless the repo
            # already had one before we started.
            shutil.rmtree(cache, ignore_errors=True)
    if not report.exists():
        raise RuntimeError(f"rustest wrote no report (exit {exit_code}):\n{tail[-2000:]}")
    data: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    tests: list[dict[str, Any]] = list(data["tests"])
    summary: dict[str, Any] = dict(data["summary"])
    collection_errors: list[dict[str, Any]] = list(data["collection_errors"])
    return RunOutcome(
        runner="rustest",
        exit_code=exit_code,
        seconds=seconds,
        statuses={str(t["id"]): str(t["status"]) for t in tests},
        deselected=int(summary.get("deselected", 0)),
        collection_errors=[str(err.get("path", err)) for err in collection_errors],
    )


# --------------------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RealVerdict:
    name: str
    status: str  # MATCH | EXPLAINED | DIVERGE | STALE-LEDGER | HARNESS-ERROR
    pytest_run: RunOutcome | None
    rustest_run: RunOutcome | None
    unexplained: list[str]
    explained: list[str]
    stale: list[str]
    detail: str = ""
    notes: dict[str, str] = field(default_factory=dict[str, str])


def _match_ledger(key: str, ledger: Mapping[str, str], used: set[str]) -> str | None:
    """The mechanism recorded for *key*, matching literally first, then as a glob."""
    if key in ledger:
        used.add(key)
        return ledger[key]
    for pattern, reason in ledger.items():
        if fnmatch.fnmatchcase(key, pattern):
            used.add(pattern)
            return reason
    return None


def _function_of(node_id: str) -> str:
    """``file.py::Class::test_f[case]`` -> ``file.py::Class::test_f``.

    The bracket is located after the last ``::`` so a path or class name containing one
    cannot be mistaken for a parametrize suffix, and only a *trailing* ``]`` counts.
    """
    if not node_id.endswith("]"):
        return node_id
    tail_start = node_id.rfind("::")
    bracket = node_id.find("[", tail_start + 1 if tail_start >= 0 else 0)
    return node_id[:bracket] if bracket > 0 else node_id


def _exact_problems(pytest_run: RunOutcome, rustest_run: RunOutcome) -> list[tuple[str, str]]:
    pytest_ids, rustest_ids = set(pytest_run.statuses), set(rustest_run.statuses)
    problems: list[tuple[str, str]] = []
    for node_id in sorted(pytest_ids - rustest_ids):
        problems.append((node_id, f"missing from rustest (pytest={pytest_run.statuses[node_id]})"))
    for node_id in sorted(rustest_ids - pytest_ids):
        problems.append((node_id, f"extra in rustest (rustest={rustest_run.statuses[node_id]})"))
    for node_id in sorted(pytest_ids & rustest_ids):
        left, right = pytest_run.statuses[node_id], rustest_run.statuses[node_id]
        if left != right:
            problems.append((node_id, f"status pytest={left} rustest={right}"))
    return problems


def _by_function(statuses: Mapping[str, str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for node_id, status in statuses.items():
        bucket = out.setdefault(_function_of(node_id), {})
        bucket[status] = bucket.get(status, 0) + 1
    return out


def _function_problems(pytest_run: RunOutcome, rustest_run: RunOutcome) -> list[tuple[str, str]]:
    left_map, right_map = _by_function(pytest_run.statuses), _by_function(rustest_run.statuses)
    problems: list[tuple[str, str]] = []
    for func in sorted(set(left_map) | set(right_map)):
        left, right = left_map.get(func, {}), right_map.get(func, {})
        if left != right:
            problems.append((func, f"status multiset pytest={left} rustest={right}"))
    return problems


def grade_real(target: RealTarget, pytest_run: RunOutcome, rustest_run: RunOutcome) -> RealVerdict:
    """Diff the two runs id by id, then globally, adjudicating against the repo's ledger."""
    used: set[str] = set()
    unexplained: list[str] = []
    explained: list[str] = []

    if not pytest_run.statuses and not rustest_run.statuses:
        # A suite where NEITHER runner executed anything agrees about nothing. This is not
        # hypothetical: jinja2's `tests/test_async*.py` import `trio`, `trio` imports `ssl`,
        # and on a uv-managed CPython 3.14.2 install missing `libcrypto-3-x64.dll` both
        # runners hit the same ImportError, both exited 2 with 2 collection errors, and the
        # gate printed a green [ok]. An environment fault must read as an environment fault.
        return RealVerdict(
            target.name,
            "HARNESS-ERROR",
            pytest_run,
            rustest_run,
            [],
            [],
            [],
            detail=(
                "vacuous run: neither runner executed a single test "
                + f"(pytest exit={pytest_run.exit_code} with "
                + f"{len(pytest_run.collection_errors)} collection errors, rustest exit="
                + f"{rustest_run.exit_code} with {len(rustest_run.collection_errors)})"
            ),
            notes=dict(target.notes),
        )

    raw_ids_differ = set(pytest_run.statuses) != set(rustest_run.statuses)
    if target.id_policy == "exact":
        problems = _exact_problems(pytest_run, rustest_run)
    else:
        problems = _function_problems(pytest_run, rustest_run)

    for node_id, description in problems:
        reason = _match_ledger(node_id, target.ids_ledger, used)
        line = f"{node_id}: {description}"
        if reason is None:
            unexplained.append(line)
        else:
            explained.append(f"{line} [{reason}]")

    global_used: set[str] = set()
    globals_seen: list[tuple[str, str]] = []
    if target.id_policy != "exact" and raw_ids_differ:
        only_pytest = sorted(set(pytest_run.statuses) - set(rustest_run.statuses))
        only_rustest = sorted(set(rustest_run.statuses) - set(pytest_run.statuses))
        globals_seen.append(
            (
                "ids",
                f"raw node ids differ under id_policy={target.id_policy!r}: "
                + f"{len(only_pytest)} only in pytest (e.g. {only_pytest[:2]}), "
                + f"{len(only_rustest)} only in rustest (e.g. {only_rustest[:2]})",
            )
        )
    if pytest_run.exit_code != rustest_run.exit_code:
        globals_seen.append(
            ("exit_code", f"exit pytest={pytest_run.exit_code} rustest={rustest_run.exit_code}")
        )
    left_tally, right_tally = pytest_run.tally(), rustest_run.tally()
    if left_tally != right_tally:
        globals_seen.append(("tally", f"tally pytest={left_tally} rustest={right_tally}"))
    for key, description in globals_seen:
        reason = _match_ledger(key, target.global_ledger, global_used)
        if reason is None:
            unexplained.append(description)
        else:
            explained.append(f"{description} [{reason}]")

    stale = sorted((set(target.ids_ledger) - used) | (set(target.global_ledger) - global_used))
    if unexplained:
        status = "DIVERGE"
    elif stale:
        status = "STALE-LEDGER"
    elif explained:
        status = "EXPLAINED"
    else:
        status = "MATCH"
    return RealVerdict(
        name=target.name,
        status=status,
        pytest_run=pytest_run,
        rustest_run=rustest_run,
        unexplained=unexplained,
        explained=explained,
        stale=stale,
        notes=dict(target.notes),
    )


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------

#: The two-character flag printed per target, deliberately the corpus gate's vocabulary.
FLAGS: Final[Mapping[str, str]] = {
    "MATCH": "ok",
    "EXPLAINED": "~~",
    "DIVERGE": "XX",
    "STALE-LEDGER": "!!",
    "HARNESS-ERROR": "EE",
}


def available_targets() -> list[str]:
    return sorted(p.stem for p in REAL_DIR.glob("*.toml"))


def run_target(name: str, *, setup_only: bool = False, rebuild_env: bool = False) -> RealVerdict:
    """Provision, run both runners sequentially, and grade -- containing any harness fault.

    The two runners are run **one after the other, never concurrently**: wall-clock is part
    of what this gate records, and a suite that competes with another process for cores
    reports a time that means nothing.
    """
    try:
        target = load_target(name)
        ensure_repo(target)
        ensure_env(target, force=rebuild_env)
        if setup_only:
            return RealVerdict(name, "MATCH", None, None, [], [], [], detail="setup only")
        tmp = WORK_DIR / "_runs" / name
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        pytest_run = run_pytest_real(target, tmp)
        rustest_run = run_rustest_real(target, tmp)
        return grade_real(target, pytest_run, rustest_run)
    except Exception as exc:  # noqa: BLE001 - one target's fault must not abort the sweep
        return RealVerdict(name, "HARNESS-ERROR", None, None, [], [], [], detail=repr(exc))


def _print_verdict(verdict: RealVerdict) -> None:
    print(f"[{FLAGS[verdict.status]}] {verdict.name}")
    if verdict.detail:
        print(f"       {verdict.detail}")
    left, right = verdict.pytest_run, verdict.rustest_run
    if left is not None and right is not None:
        print(f"       pytest : exit={left.exit_code} {left.seconds:.2f}s tally={left.tally()}")
        print(f"       rustest: exit={right.exit_code} {right.seconds:.2f}s tally={right.tally()}")
    for key, note in sorted(verdict.notes.items()):
        print(f"       ++ compensated: {key}: {note}")
    for line in verdict.explained[:40]:
        print(f"       ~~ {line}")
    if len(verdict.explained) > 40:
        print(f"       ~~ ... and {len(verdict.explained) - 40} more explained")
    for line in verdict.unexplained[:40]:
        print(f"       XX {line}")
    if len(verdict.unexplained) > 40:
        print(f"       XX ... and {len(verdict.unexplained) - 40} more unexplained")
    for line in verdict.stale:
        print(f"       !! stale ledger entry, nothing matched it: {line}")


def main_real(selection: str, *, setup_only: bool = False, rebuild_env: bool = False) -> int:
    names = available_targets() if selection == "all" else [selection]
    if selection != "all" and selection not in available_targets():
        print(
            f"conformance: no such --real target {selection!r} "
            + f"(have: {', '.join(available_targets())}, or 'all')",
            file=sys.stderr,
        )
        return 1
    verdicts = [run_target(name, setup_only=setup_only, rebuild_env=rebuild_env) for name in names]
    for verdict in verdicts:
        _print_verdict(verdict)
    bad = [v for v in verdicts if v.status in ("DIVERGE", "STALE-LEDGER", "HARNESS-ERROR")]
    matched = sum(v.status == "MATCH" for v in verdicts)
    explained = sum(v.status == "EXPLAINED" for v in verdicts)
    print(
        f"\n{len(verdicts)} real suites: {matched} match, {explained} explained, "
        + f"{sum(v.status == 'DIVERGE' for v in verdicts)} diverged, "
        + f"{sum(v.status == 'STALE-LEDGER' for v in verdicts)} stale-ledgers, "
        + f"{sum(v.status == 'HARNESS-ERROR' for v in verdicts)} harness-errors"
    )
    return 1 if bad else 0
