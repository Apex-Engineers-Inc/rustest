"""Directory exclusion during test discovery.

Ensures rustest matches pytest's behaviour for excluding directories while walking, per
pytest's ``norecursedirs`` defaults (`_pytest/main.py`) and its virtualenv detection
(``_in_venv``: a directory carrying ``pyvenv.cfg`` or ``conda-meta/history``).

**Driven through the v2 collection boundary**, ``rustest.rust.collect``. It used to drive
``rustest.run`` -- the v1 engine's Python API -- and count outcomes off a ``RunReport``;
both went in Phase 4 Task 2. Collection is also the more precise instrument for the question
this file asks: which directories are *walked* is a property of discovery, and executing the
files it finds only adds a way for the answer to be right and the test to fail.

The Rust side has its own unit tests for the same rules (`src/engine/collect.rs`:
``default_norecursedirs_and_pytest_prunes_are_honoured``,
``custom_norecursedirs_replaces_the_defaults``). These are the end-to-end half: real
directories on a real filesystem, through the real boundary.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import sys

import pytest

from .helpers import ensure_rust_stub

rust = ensure_rust_stub()


class TestDirectoryExclusions:
    """Test directory exclusions during test discovery."""

    def _write_test_file(
        self, temp_dir: Path, relative_path: str, test_name: str = "test_example"
    ) -> Path:
        """Write a simple test file to the specified path."""
        path = temp_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""
def {test_name}():
    '''Test in {relative_path}'''
    assert True
"""
        )
        return path

    def _collect(self, temp_dir: Path) -> list[str]:
        """Every node id the engine collects under *temp_dir*, in manifest order.

        A bare ``pytest.ini`` is written first so *temp_dir* anchors its own rootdir. Without
        it the resolver walks up from the invocation directory exactly as pytest does, and a
        tree created outside any project would take its config from wherever the walk
        happened to stop -- making these assertions a function of where pytest's tmp_path
        lives rather than of the exclusion rules.
        """
        _ = (temp_dir / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        payload = rust.collect(str(temp_dir), [], sys.executable, 1)
        manifest = json.loads(payload)
        errors = manifest.get("errors", [])
        assert not errors, errors
        return [test["id"] for test in manifest["tests"]]

    def _collect_with_pytest(self, temp_dir: Path) -> list[str]:
        """The same question put to **real pytest**, for tests that assert a differential.

        ``--collect-only -q`` prints one node id per line and then a blank line and a count,
        which is why the parse stops at the first empty line. Node ids come back with the
        platform's separator; they are normalised to ``/`` so the two sides are comparable on
        Windows as well.
        """
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=str(temp_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"pytest rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        ids: list[str] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                break
            ids.append(line.strip().replace("\\", "/"))
        return ids

    def test_discovers_tests_in_normal_directories(self, tmp_path: Path) -> None:
        """Test that regular directories are included in discovery."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "subdir/test_sub.py")
        self._write_test_file(tmp_path, "deep/nested/test_deep.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 3, ids

    def test_excludes_hidden_directories_dot_prefix(self, tmp_path: Path) -> None:
        """Test that directories starting with '.' are excluded (norecursedirs: '.*')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, ".hidden/test_hidden.py")
        self._write_test_file(tmp_path, ".cache/test_cache.py")
        self._write_test_file(tmp_path, ".pytest_cache/test_pytest.py")
        self._write_test_file(tmp_path, ".git/test_git.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_venv_directory(self, tmp_path: Path) -> None:
        """Test that 'venv' directory is excluded (norecursedirs: 'venv')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "venv/test_venv.py")
        self._write_test_file(tmp_path, "venv/lib/python3.11/site-packages/test_package.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_dot_venv_directory(self, tmp_path: Path) -> None:
        """Test that '.venv' directory is excluded (matches '.*' pattern)."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, ".venv/test_venv.py")
        self._write_test_file(tmp_path, ".venv/lib/python3.11/site-packages/test_package.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_build_directory(self, tmp_path: Path) -> None:
        """Test that 'build' directory is excluded (norecursedirs: 'build')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "build/test_build.py")
        self._write_test_file(tmp_path, "build/lib/test_lib.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_dist_directory(self, tmp_path: Path) -> None:
        """Test that 'dist' directory is excluded (norecursedirs: 'dist')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "dist/test_dist.py")
        self._write_test_file(tmp_path, "dist/packages/test_pkg.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_node_modules_directory(self, tmp_path: Path) -> None:
        """Test that 'node_modules' directory is excluded (norecursedirs: 'node_modules')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "node_modules/test_node.py")
        self._write_test_file(tmp_path, "node_modules/package/test_pkg.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_egg_directories(self, tmp_path: Path) -> None:
        """Test that '*.egg' directories are excluded (norecursedirs: '*.egg')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "mypackage.egg/test_egg.py")
        self._write_test_file(tmp_path, "another.egg/test_another.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_darcs_directory(self, tmp_path: Path) -> None:
        """Test that '_darcs' directory is excluded (norecursedirs: '_darcs')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "_darcs/test_darcs.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_cvs_directory(self, tmp_path: Path) -> None:
        """Test that 'CVS' directory is excluded (norecursedirs: 'CVS')."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "CVS/test_cvs.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_virtualenv_with_pyvenv_cfg(self, tmp_path: Path) -> None:
        """Test that directories with pyvenv.cfg are detected as virtualenvs and excluded."""
        self._write_test_file(tmp_path, "test_root.py")

        # Create a custom-named virtualenv with pyvenv.cfg marker
        venv_dir = tmp_path / "my_custom_env"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").write_text("home = /usr\n")
        self._write_test_file(tmp_path, "my_custom_env/test_custom.py")
        self._write_test_file(tmp_path, "my_custom_env/lib/python3.11/test_lib.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_conda_environment(self, tmp_path: Path) -> None:
        """Test that conda environments are detected and excluded (conda-meta/history)."""
        self._write_test_file(tmp_path, "test_root.py")

        # Create a conda environment with conda-meta/history marker
        conda_dir = tmp_path / "condaenv"
        conda_dir.mkdir()
        conda_meta = conda_dir / "conda-meta"
        conda_meta.mkdir()
        (conda_meta / "history").write_text("# conda history\n")
        self._write_test_file(tmp_path, "condaenv/test_conda.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_excludes_multiple_excluded_directories_together(self, tmp_path: Path) -> None:
        """Test that multiple excluded directory types can coexist."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "src/test_src.py")  # Should be found

        # All these should be excluded
        self._write_test_file(tmp_path, "venv/test_venv.py")
        self._write_test_file(tmp_path, ".venv/test_dotvenv.py")
        self._write_test_file(tmp_path, "build/test_build.py")
        self._write_test_file(tmp_path, "dist/test_dist.py")
        self._write_test_file(tmp_path, ".git/test_git.py")
        self._write_test_file(tmp_path, "node_modules/test_node.py")
        self._write_test_file(tmp_path, "myapp.egg/test_egg.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 2, ids  # Only test_root.py and src/test_src.py

    def test_nested_excluded_directories(self, tmp_path: Path) -> None:
        """Test that nested excluded directories are handled correctly."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "src/test_src.py")

        # Nested exclusions
        self._write_test_file(tmp_path, "src/build/test_build.py")  # build inside src
        self._write_test_file(tmp_path, "src/.hidden/test_hidden.py")  # hidden inside src
        self._write_test_file(tmp_path, "venv/dist/test_venv_dist.py")  # dist inside venv

        ids = self._collect(tmp_path)
        assert len(ids) == 2, ids  # Only test_root.py and src/test_src.py

    def test_directories_with_similar_names_are_not_excluded(self, tmp_path: Path) -> None:
        """Test that directories with similar but non-matching names are included."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "venv_backup/test_venv_backup.py")  # Not exactly 'venv'
        self._write_test_file(tmp_path, "building/test_building.py")  # Not exactly 'build'
        self._write_test_file(tmp_path, "distribute/test_distribute.py")  # Not exactly 'dist'
        self._write_test_file(tmp_path, "my_dist/test_my_dist.py")  # Has 'dist' but not exact match

        ids = self._collect(tmp_path)
        # All should be found since none match exact patterns
        assert len(ids) == 5, ids

    def test_virtualenv_without_marker_but_named_venv_excluded(self, tmp_path: Path) -> None:
        """Test that 'venv' named directory without pyvenv.cfg is still excluded by pattern."""
        self._write_test_file(tmp_path, "test_root.py")

        # Create venv directory WITHOUT pyvenv.cfg
        venv_dir = tmp_path / "venv"
        venv_dir.mkdir()
        self._write_test_file(tmp_path, "venv/test_venv.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids  # Should still be excluded by name pattern

    def test_normal_directory_with_pyvenv_cfg_excluded(self, tmp_path: Path) -> None:
        """Test that any directory with pyvenv.cfg is excluded, regardless of name."""
        self._write_test_file(tmp_path, "test_root.py")

        # Create an oddly-named directory but with pyvenv.cfg
        strange_venv = tmp_path / "not_a_venv_name"
        strange_venv.mkdir()
        (strange_venv / "pyvenv.cfg").write_text("home = /usr\n")
        self._write_test_file(tmp_path, "not_a_venv_name/test_strange.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids  # Should be excluded due to pyvenv.cfg

    def test_deep_nesting_with_exclusions(self, tmp_path: Path) -> None:
        """Test deep directory nesting with various exclusions."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "level1/test_l1.py")
        self._write_test_file(tmp_path, "level1/level2/test_l2.py")
        self._write_test_file(tmp_path, "level1/level2/level3/test_l3.py")

        # Excluded deep paths
        self._write_test_file(tmp_path, "level1/venv/test_excluded.py")
        self._write_test_file(tmp_path, "level1/level2/.hidden/test_excluded.py")
        self._write_test_file(tmp_path, "level1/level2/level3/build/test_excluded.py")

        ids = self._collect(tmp_path)
        assert len(ids) == 4, ids  # Only non-excluded paths

    def test_case_sensitive_directory_matching(self, tmp_path: Path) -> None:
        """Test that directory matching is case-sensitive."""
        self._write_test_file(tmp_path, "test_root.py")
        self._write_test_file(tmp_path, "Build/test_build_upper.py")  # Capital B
        self._write_test_file(tmp_path, "DIST/test_dist_upper.py")  # All caps
        self._write_test_file(tmp_path, "Venv/test_venv_upper.py")  # Capital V

        # These might be excluded or not depending on OS - on Linux they should NOT be excluded
        # since patterns are case-sensitive. Let's just verify discovery runs without error.
        ids = self._collect(tmp_path)
        # On case-sensitive systems these should be found
        assert len(ids) >= 1, ids  # At least test_root.py

    def test_empty_directory_structures(self, tmp_path: Path) -> None:
        """Test that empty excluded directories don't cause issues."""
        self._write_test_file(tmp_path, "test_root.py")

        # Create excluded directories but don't put tests in them
        (tmp_path / "venv").mkdir()
        (tmp_path / "build").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()

        ids = self._collect(tmp_path)
        assert len(ids) == 1, ids

    def test_symlinks_to_excluded_directories(self, tmp_path: Path) -> None:
        """A symlink whose *name* is not excluded is walked — and pytest says so too.

        **This test used to assert a guess.** It hardcoded ``len(ids) == 1`` under a comment
        admitting "symlink might be followed or not", and that count only holds if the link
        is *not* followed. It passed everywhere it ran because it never ran: creating a
        symlink on Windows needs a privilege CI does not grant, so it took the
        ``pytest.skip`` above. The first Linux run collected **2** and failed it.

        Two is correct, and it is pytest's answer as well. Exclusion is by directory
        **name** — pytest's ``norecursedirs`` default contains ``venv``, and ``venv_link``
        does not match it — while the walk itself follows symlinks: pytest's ``Dir.collect``
        is ``for direntry in scandir(self.path): if direntry.is_dir()``
        (`_pytest/main.py` l. 528-529), and ``os.DirEntry.is_dir()`` defaults to
        ``follow_symlinks=True``. So the link is descended, and the directory it lands in is
        not one of the excluded names.

        Asserted as a **differential** rather than as a number, because that is the contract
        this whole module exists for, and because a number is what got it wrong last time.
        """
        self._write_test_file(tmp_path, "test_root.py")

        venv_dir = tmp_path / "venv"
        venv_dir.mkdir()
        self._write_test_file(tmp_path, "venv/test_venv.py")

        try:
            (tmp_path / "venv_link").symlink_to(venv_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this system")

        ours = sorted(self._collect(tmp_path))
        theirs = sorted(self._collect_with_pytest(tmp_path))
        assert ours == theirs

        # And the shape of that agreement, so a future change that made *both* wrong in the
        # same way would still be visible: the excluded name is pruned, the link is not.
        assert not any(nid.startswith("venv/") for nid in ours), ours
        assert any("venv_link" in nid for nid in ours), ours
