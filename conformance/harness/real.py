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


def _setup_substitutions(target: RealTarget) -> dict[str, str]:
    """The ``{py}``/``{venv}``/``{repo}``/``{work}``/``{wheels}`` map for ``[env] setup``."""
    return {
        "py": str(target.python),
        "venv": str(target.venv),
        "repo": str(target.repo),
        "work": str(WORK_DIR),
        "wheels": str(WORK_DIR / "_wheels"),
    }


def ensure_env(target: RealTarget, *, force: bool = False) -> None:
    """Create the target's isolated venv and install into it, if it isn't there already.

    Every command comes from the config's ``[env] setup``, with ``{py}``/``{venv}``/
    ``{repo}``/``{work}``/``{wheels}`` substituted. Nothing is ever installed into the
    rustest development venv or into a target repository's own environment.

    **The early return is why :func:`verify_env` exists.** "The interpreter is there" says
    nothing about *what is installed in it*, and this function is the wrong place to learn
    otherwise: reprovisioning a target costs minutes and a `--real all` sweep enters here
    seventeen times. So the cheap invariants -- is the extension loadable, is it the code in
    this tree -- are asked on every invocation, after this returns.
    """
    if target.python.exists() and not force:
        return
    if force and target.venv.exists():
        shutil.rmtree(target.venv)
    subs = _setup_substitutions(target)
    for argv in target.setup:
        _run_command([word.format(**subs) for word in argv], cwd=WORK_DIR)
    if not target.python.exists():
        raise RuntimeError(f"{target.name}: setup finished but {target.python} does not exist")


#: This repository's root: ``conformance/harness/real.py`` -> ``conformance/harness`` ->
#: ``conformance`` -> the tree.
_REPO_ROOT: Final = Path(__file__).resolve().parent.parent.parent

#: What a rustest wheel is built *from*. A wheel older than any of these was built from
#: different code than the one being graded. Deliberately the build inputs and not "every
#: tracked file": a docs edit does not invalidate a measurement, and a check that cries wolf
#: gets bypassed.
_BUILD_INPUT_GLOBS: Final = (
    "src/**/*.rs",
    "python/rustest/**/*.py",
    "Cargo.toml",
    "Cargo.lock",
    "pyproject.toml",
)


def _newest_build_input() -> tuple[float, Path]:
    """The most recently modified build input, and which one it was."""
    newest, where = 0.0, _REPO_ROOT
    for pattern in _BUILD_INPUT_GLOBS:
        for path in _REPO_ROOT.glob(pattern):
            mtime = path.stat().st_mtime
            if mtime > newest:
                newest, where = mtime, path
    return newest, where


def _wheels_named_by(target: RealTarget) -> list[Path]:
    """The rustest wheels **this target's own** ``[env] setup`` installs.

    Read out of the config rather than globbed off disk, because each target names its own
    ABI on purpose (``member-designer`` is ``cp314t``, ``werkzeug`` is 3.13) and "the" wheel
    is not a thing that exists. ``uv pip install <explicit path>`` does **not** ABI-check a
    direct path, so installing the wrong one across targets by hand succeeds and then fails
    minutes later inside the run.
    """
    subs = _setup_substitutions(target)
    wheels_dir = Path(subs["wheels"])
    named: list[Path] = []
    for argv in target.setup:
        for word in argv:
            path = Path(word.format(**subs))
            if path.suffix == ".whl" and path.parent == wheels_dir:
                named.append(path)
    return named


def assert_build_is_current(target: RealTarget) -> None:
    """Refuse to grade a target whose installed rustest predates this tree's source.

    Phase 4c lost a member-designer run to this and *very nearly published the number*.
    :func:`ensure_env` returns the moment the venv's interpreter exists, so a `--real` run
    silently measures whatever rustest was installed last -- and the failure has no symptom:
    the suite runs, the ids match, and the wall-clock is a real measurement of the wrong
    build. The first run of that task was started against wheels built seven hours earlier
    and would have graded the *previous* commit; it was caught by hand, by someone who
    happened to think to check.

    Two hops, because each can be stale on its own:

    * **wheel vs. source** -- a wheel older than the newest build input was built from
      different code. Caught by mtime rather than by a version string, because the version
      does not move between commits (`0.16.2` for this entire phase) and a content hash
      would have to be stamped into the wheel by a build step that does not exist.
    * **install vs. wheel** -- a wheel rebuilt but never reinstalled. The venv is asked for
      the file it would actually import, and that file is stat'd.

    Refusal, not repair: rebuilding is per-ABI (`maturin build --release -i python3.14t` for
    the free-threaded target) and reinstalling "the" wheel across targets is the foot-gun
    documented on :func:`_wheels_named_by`. The message names the wheel, the source file
    that outran it, and both timestamps, so the reader can tell a real staleness from a
    fresh `git checkout` rewriting every mtime.
    """
    newest_source, source_path = _newest_build_input()
    wheels = _wheels_named_by(target)
    if not wheels:
        # A target that installs rustest some other way (editable, say) has nothing to
        # compare; `verify_env`'s import probe is still the backstop.
        return
    for wheel in wheels:
        if not wheel.exists():
            raise RuntimeError(
                f"{target.name}: its [env] setup installs {wheel}, which does not exist. "
                + "Build it (`uv run maturin build --release -o "
                + f"{wheel.parent}`, with `-i` naming this target's ABI) and re-run with "
                + "--real-rebuild-env."
            )
        wheel_mtime = wheel.stat().st_mtime
        if wheel_mtime < newest_source:
            raise RuntimeError(
                f"{target.name}: STALE WHEEL -- {wheel.name} was built "
                + f"{_stamp(wheel_mtime)} but {source_path.relative_to(_REPO_ROOT).as_posix()} "
                + f"changed {_stamp(newest_source)}. Grading this would measure the previous "
                + "build and produce entirely plausible numbers about it. Rebuild "
                + f"(`uv run maturin build --release -o {wheel.parent}`, `-i` naming this "
                + "target's ABI -- each target names its own on purpose) and re-run with "
                + "--real-rebuild-env."
            )
        installed = _installed_rustest_path(target)
        if installed is not None and installed.exists() and installed.stat().st_mtime < wheel_mtime:
            raise RuntimeError(
                f"{target.name}: STALE INSTALL -- {wheel.name} was built "
                + f"{_stamp(wheel_mtime)} but this venv's {installed.name} is from "
                + f"{_stamp(installed.stat().st_mtime)}, so the wheel was rebuilt and never "
                + "reinstalled here. Re-run with --real-rebuild-env."
            )


def _stamp(mtime: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


def _installed_rustest_path(target: RealTarget) -> Path | None:
    """Where *target*'s own interpreter would import ``rustest.rust`` from, or ``None``.

    Asked of the interpreter rather than assembled from ``site-packages`` conventions, for
    the same reason :func:`verify_env` asks: the answer is what an ``import`` resolves to,
    which is the only thing the run depends on.
    """
    proc = subprocess.run(  # noqa: S603 - argv is this module's own constant
        [str(target.python), "-c", "import rustest.rust as r; print(r.__file__)"],
        capture_output=True,
        timeout=120,
        check=False,
        text=True,
        encoding=_ENCODING,
        errors=_ERRORS,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None  # `verify_env` reports this properly, with the ABI diagnosis.
    return Path(proc.stdout.strip().splitlines()[-1])


#: Asks the target's own interpreter whether the rustest it would import is the compiled
#: extension, and which ABI it is. ``Py_GIL_DISABLED`` is the discriminator, **not**
#: ``sys.version_info``: a free-threaded build reports ``3.14`` exactly like a GIL build, so
#: a version check cannot tell ``cp314`` from ``cp314t``.
_ABI_PROBE: Final = (
    "import sysconfig,rustest.rust as r;"
    "print('t' if sysconfig.get_config_var('Py_GIL_DISABLED') else '',"
    "hasattr(r,'run'), r.__file__)"
)


def verify_env(target: RealTarget) -> None:
    """Refuse to run a target whose venv holds the wrong rustest, loudly and by name.

    Phase 4 Task 1c lost a Member Designer run to exactly this and could not see it: MD's
    venv is a **free-threaded** (``cp314t``) build, a blanket wheel-install loop keyed on
    ``sys.version_info`` -- which reports ``3.14`` for both ABIs -- and force-installed the
    GIL wheel over it. The ``.pyd`` cannot load under free-threading, so ``import
    rustest.rust`` fell back to the pure-Python ``rust.py`` shim and the run died several
    minutes later with ``AttributeError: module 'rustest.rust' has no attribute 'run'``.
    The target's own config had pinned the right wheel all along.

    So the check is not "did the config name the right wheel" -- it did -- but "is what is
    *installed right now* usable", asked of the interpreter that will run the suite, on
    **every** invocation rather than only at venv creation. That is the half that matters:
    the venv already existed, so the creation path was never re-entered.

    The ABI is read from ``Py_GIL_DISABLED`` and the message says so, because the next
    person to write an install loop will reach for ``sys.version_info`` again otherwise.

    This answers "is it *usable*". :func:`assert_build_is_current` answers "is it *this
    tree's*", which is the other half and fails without any symptom at all.
    """
    proc = subprocess.run(  # noqa: S603 - argv is this module's own constant
        [str(target.python), "-c", _ABI_PROBE],
        capture_output=True,
        timeout=120,
        check=False,
        text=True,
        encoding=_ENCODING,
        errors=_ERRORS,
    )
    abi = "cp314t/free-threaded" if proc.stdout.startswith("t ") else "GIL"
    if proc.returncode != 0 or "True" not in proc.stdout:
        raise RuntimeError(
            f"{target.name}: {target.python} cannot import a working rustest extension "
            + f"(this venv is a {abi} build -- detect that with "
            + "sysconfig.get_config_var('Py_GIL_DISABLED'), never sys.version_info, which "
            + "reports 3.14 for both). Re-run with --real-rebuild-env, or install the wheel "
            + f"the target's [env] setup names.\n{proc.stdout[-500:]}\n{proc.stderr[-500:]}"
        )


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
    """``PYTHONPATH`` for one runner: the plugin directory, plus any ``[env] pythonpath``.

    That second part is a **compensation lever** and no target uses it as of Phase 4 Task 1.
    It existed for Member Designer's ``pythonpath = ["src"]``, which pytest honoured and v2
    did not implement, so without it every one of that suite's ~285 files failed to import
    and the sweep measured one missing ini option instead of a suite. v2 implements the ini
    now (``_worker::_apply_pythonpath``), so the key is empty everywhere; it is kept
    because a future target may need the same kind of scaffolding, and an empty list is a
    visible "nothing is being compensated here".
    """
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

    **The ``addopts`` compensation is retired (Phase 4 Task 1).** Through Phase 3 this
    passed ``-o addopts=`` to neutralize the target's own ``addopts`` and replayed the value
    to *both* runners on the command line, because rustest parsed the key and applied it
    nowhere -- letting pytest honour it alone would have asked the two runners different
    questions (click: 1 686 tests against 32 686). rustest now applies ``addopts`` itself
    (``cli.py::main``, the port of ``Config._preparse``), so **each runner reads the
    target's config for itself**, which is the thing the sweep is supposed to measure.

    ``[addopts] replay``/``dropped`` stay in the configs as documentation of what each
    target declares, and ``dropped`` keeps its meaning for a future target with a flag
    rustest cannot accept -- but nothing is replayed on the command line any more.
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

    **Flagless** now means it: the ``addopts`` replay is gone (see :func:`run_pytest_real`),
    so rustest reads the target's ``addopts`` -- and its ``pythonpath`` -- out of the
    target's own config exactly as pytest does.
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
        verify_env(target)
        # AFTER `verify_env`, so a venv holding a *broken* extension gets that diagnosis
        # (which names the ABI trap) rather than a staleness one derived from it.
        assert_build_is_current(target)
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
    # STREAMED, not collected-then-printed, and the difference is two hours of wall clock.
    #
    # This was `verdicts = [run_target(...) for name in names]` followed by a print loop, so
    # a full `--real all` sweep -- upwards of two hours, most of it psutil -- emitted NOTHING
    # until the last target finished. Kill it at minute 100 and every verdict is gone with
    # the process. That happened twice: the Phase 4b speed wave lost its sweep ~11 minutes
    # into psutil and had to regrade twelve targets from the per-target artifacts on disk,
    # and the wall-clock column could not be recovered at all because the harness measures it
    # in-process and never writes it down.
    #
    # (That report diagnosed the loss as stdout block-buffering. It is not -- `python -u`
    # does not help, because nothing had been *written* yet. The buffer was this list.)
    #
    # Printed and flushed as each target completes, so an interrupted sweep is worth exactly
    # the targets it finished. `flush=True` on the print itself because stdout is a pipe
    # whenever the sweep is redirected to a log, which is how a two-hour run is always driven.
    verdicts: list[RealVerdict] = []
    for name in names:
        verdict = run_target(name, setup_only=setup_only, rebuild_env=rebuild_env)
        verdicts.append(verdict)
        _print_verdict(verdict)
        sys.stdout.flush()
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
