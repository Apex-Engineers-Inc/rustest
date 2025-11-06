"""Watch mode implementation for hot-reloading tests."""

from __future__ import annotations

import ast
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ._cli import Colors, _print_report
from ._reporting import RunReport
from .core import run


class DependencyTracker:
    """Track dependencies between test files and source files."""

    def __init__(self) -> None:
        # Map of source file -> set of test files that import it
        self.dependencies: dict[str, set[str]] = {}
        # Map of test file -> set of source files it imports
        self.test_imports: dict[str, set[str]] = {}

    def analyze_file(self, file_path: str) -> set[str]:
        """Analyze a Python file to extract its imports.

        Returns a set of absolute file paths that this file imports.
        """
        imports: set[str] = set()
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=file_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_path = self._resolve_import(alias.name, file_path)
                        if module_path:
                            imports.add(module_path)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module_path = self._resolve_import(node.module, file_path)
                        if module_path:
                            imports.add(module_path)
        except (SyntaxError, OSError):
            # If we can't parse the file, assume no dependencies
            pass

        return imports

    def _resolve_import(self, module_name: str, from_file: str) -> str | None:
        """Resolve an import statement to an actual file path."""
        # Handle relative imports
        if module_name.startswith("."):
            base_dir = Path(from_file).parent
            # Count the dots for relative levels
            level = len(module_name) - len(module_name.lstrip("."))
            for _ in range(level - 1):
                base_dir = base_dir.parent
            module_name = module_name.lstrip(".")
            if module_name:
                parts = module_name.split(".")
                target = base_dir / "/".join(parts)
            else:
                target = base_dir
        else:
            # Try to resolve absolute import
            parts = module_name.split(".")
            # Check in sys.path
            for path in sys.path:
                if not path:
                    path = "."
                target = Path(path) / "/".join(parts)
                # Try as a module
                if (target.with_suffix(".py")).exists():
                    return str(target.with_suffix(".py").resolve())
                # Try as a package
                if (target / "__init__.py").exists():
                    return str((target / "__init__.py").resolve())
            # Module not found in sys.path
            return None

        # Check if it's a file or package
        if target.with_suffix(".py").exists():
            return str(target.with_suffix(".py").resolve())
        if (target / "__init__.py").exists():
            return str((target / "__init__.py").resolve())

        return None

    def update_dependencies(self, test_file: str) -> None:
        """Update dependency tracking for a test file."""
        test_file = str(Path(test_file).resolve())

        # Remove old dependencies for this test file
        if test_file in self.test_imports:
            for source_file in self.test_imports[test_file]:
                if source_file in self.dependencies:
                    self.dependencies[source_file].discard(test_file)

        # Analyze and add new dependencies
        imports = self.analyze_file(test_file)
        self.test_imports[test_file] = imports

        for source_file in imports:
            if source_file not in self.dependencies:
                self.dependencies[source_file] = set()
            self.dependencies[source_file].add(test_file)

    def get_affected_tests(self, changed_file: str) -> set[str]:
        """Get the set of test files affected by a change to the given file."""
        changed_file = str(Path(changed_file).resolve())

        # If the changed file is a test file, return it
        if self._is_test_file(changed_file):
            return {changed_file}

        # Otherwise, return all test files that depend on it
        return self.dependencies.get(changed_file, set()).copy()

    @staticmethod
    def _is_test_file(file_path: str) -> bool:
        """Check if a file is a test file."""
        name = Path(file_path).name
        return name.startswith("test_") or name.endswith("_test.py")


class TestFileEventHandler(FileSystemEventHandler):
    """Handle file system events and trigger test runs."""

    def __init__(
        self,
        paths: tuple[str, ...],
        pattern: str | None,
        workers: int | None,
        capture_output: bool,
        verbose: bool,
        ascii_mode: bool,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.pattern = pattern
        self.workers = workers
        self.capture_output = capture_output
        self.verbose = verbose
        self.ascii_mode = ascii_mode
        self.dependency_tracker = DependencyTracker()
        self.last_run_time = 0.0
        self.debounce_delay = 0.5  # seconds
        self.pending_changes: set[str] = set()

        # Initialize dependency tracking
        self._initialize_dependencies()

    def _initialize_dependencies(self) -> None:
        """Build initial dependency graph."""
        for path_str in self.paths:
            path = Path(path_str)
            if path.is_file():
                if self.dependency_tracker._is_test_file(str(path)):
                    self.dependency_tracker.update_dependencies(str(path))
            elif path.is_dir():
                for test_file in path.rglob("test_*.py"):
                    self.dependency_tracker.update_dependencies(str(test_file))
                for test_file in path.rglob("*_test.py"):
                    self.dependency_tracker.update_dependencies(str(test_file))

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification events."""
        if event.is_directory:
            return

        file_path = event.src_path
        if not file_path.endswith(".py"):
            return

        # Add to pending changes
        self.pending_changes.add(file_path)

        # Debounce: only run tests if enough time has passed
        current_time = time.time()
        if current_time - self.last_run_time >= self.debounce_delay:
            self._run_affected_tests()

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation events."""
        if event.is_directory:
            return

        file_path = event.src_path
        if not file_path.endswith(".py"):
            return

        # For new test files, add to dependency tracking
        if self.dependency_tracker._is_test_file(file_path):
            self.dependency_tracker.update_dependencies(file_path)

        self.pending_changes.add(file_path)

        current_time = time.time()
        if current_time - self.last_run_time >= self.debounce_delay:
            self._run_affected_tests()

    def _run_affected_tests(self) -> None:
        """Run tests affected by pending changes."""
        if not self.pending_changes:
            return

        self.last_run_time = time.time()

        # Collect all affected test files
        affected_tests: set[str] = set()
        for changed_file in self.pending_changes:
            # Update dependencies if it's a test file
            if self.dependency_tracker._is_test_file(changed_file):
                self.dependency_tracker.update_dependencies(changed_file)

            # Get affected tests
            tests = self.dependency_tracker.get_affected_tests(changed_file)
            affected_tests.update(tests)

        self.pending_changes.clear()

        if not affected_tests:
            return

        # Print what's being re-run
        print(f"\n{Colors.cyan}{'=' * 70}{Colors.reset}")
        print(
            f"{Colors.bold}File changes detected. Re-running {len(affected_tests)} affected test file(s)...{Colors.reset}"
        )
        for test_file in sorted(affected_tests):
            rel_path = os.path.relpath(test_file)
            print(f"  {Colors.dim}{rel_path}{Colors.reset}")
        print(f"{Colors.cyan}{'=' * 70}{Colors.reset}\n")

        # Run the affected tests
        report = run(
            paths=tuple(affected_tests),
            pattern=self.pattern,
            workers=self.workers,
            capture_output=self.capture_output,
        )

        _print_report(report, verbose=self.verbose, ascii_mode=self.ascii_mode)

        print(f"\n{Colors.dim}Watching for changes...{Colors.reset}")


def watch_mode(
    paths: tuple[str, ...],
    pattern: str | None,
    workers: int | None,
    capture_output: bool,
    verbose: bool,
    ascii_mode: bool,
) -> int:
    """Run tests in watch mode, re-running affected tests on file changes."""
    # Run tests initially
    print(f"{Colors.bold}Running initial test suite...{Colors.reset}\n")
    report = run(
        paths=paths,
        pattern=pattern,
        workers=workers,
        capture_output=capture_output,
    )
    _print_report(report, verbose=verbose, ascii_mode=ascii_mode)

    # Set up file watcher
    event_handler = TestFileEventHandler(
        paths=paths,
        pattern=pattern,
        workers=workers,
        capture_output=capture_output,
        verbose=verbose,
        ascii_mode=ascii_mode,
    )

    observer = Observer()
    for path in paths:
        path_obj = Path(path)
        if path_obj.is_dir():
            observer.schedule(event_handler, str(path_obj), recursive=True)
        elif path_obj.is_file():
            # Watch the parent directory for file changes
            observer.schedule(event_handler, str(path_obj.parent), recursive=False)

    observer.start()

    print(f"\n{Colors.green}Watch mode enabled.{Colors.reset}")
    print(f"{Colors.dim}Watching for changes in {len(paths)} path(s)...{Colors.reset}")
    print(f"{Colors.dim}Press Ctrl+C to exit.{Colors.reset}\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print(f"\n{Colors.yellow}Watch mode stopped.{Colors.reset}")

    observer.join()
    return 0 if report.failed == 0 else 1
