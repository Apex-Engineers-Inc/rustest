"""Rich terminal renderer for test execution progress.

Provides beautiful, real-time progress display using the rich library.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

if TYPE_CHECKING:
    from rustest.rust import (
        CollectionErrorEvent,
        FileCompletedEvent,
        FileStartedEvent,
        SuiteCompletedEvent,
        SuiteStartedEvent,
        TestCompletedEvent,
    )


class RichRenderer:
    """Real-time terminal renderer using rich library.

    Displays file-level progress bars with spinners, updating in real-time
    as tests complete. Supports parallel execution with multiple files
    updating simultaneously.

    Thread-safety: handle() is called from Rust threads (via GIL),
    but calls are serialized by the GIL. Rich's Live is also thread-safe.
    """

    def __init__(self, *, use_colors: bool = True):
        """Initialize the rich renderer.

        Args:
            use_colors: Whether to use colored output
        """
        self.console = Console(force_terminal=use_colors, file=sys.stderr)
        self.use_colors = use_colors

        # Progress bar for file execution
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=self.console,
        )

        # Map file paths to progress task IDs
        self.file_tasks: dict[str, int] = {}

        # Overall statistics
        self.total_tests = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0

        # Collect failures to display at the end
        self.failures: list[tuple[str, str, str]] = []  # (test_id, file_path, message)

        # Collect collection errors
        self.collection_errors: list[tuple[str, str]] = []  # (path, message)

        # Rich Live display (thread-safe!)
        self.live: Live | None = None
        self._started = False

    def _ensure_started(self):
        """Ensure the live display is started."""
        if not self._started:
            self.live = Live(self.progress, console=self.console, refresh_per_second=10)
            self.live.start()
            self._started = True

    def handle(self, event) -> None:
        """Handle a test execution event.

        Called from Rust threads, but serialized by GIL.
        No explicit locking needed!

        Args:
            event: Event from rust module
        """
        from rustest.rust import (
            CollectionErrorEvent,
            FileCompletedEvent,
            FileStartedEvent,
            SuiteCompletedEvent,
            SuiteStartedEvent,
            TestCompletedEvent,
        )

        if isinstance(event, SuiteStartedEvent):
            self._handle_suite_started(event)
        elif isinstance(event, FileStartedEvent):
            self._handle_file_started(event)
        elif isinstance(event, TestCompletedEvent):
            self._handle_test_completed(event)
        elif isinstance(event, FileCompletedEvent):
            self._handle_file_completed(event)
        elif isinstance(event, SuiteCompletedEvent):
            self._handle_suite_completed(event)
        elif isinstance(event, CollectionErrorEvent):
            self._handle_collection_error(event)

    def _handle_suite_started(self, event: SuiteStartedEvent) -> None:
        """Handle suite start event."""
        self.total_tests = event.total_tests
        self._ensure_started()

    def _handle_file_started(self, event: FileStartedEvent) -> None:
        """Handle file start event."""
        self._ensure_started()

        # Add progress bar for this file
        task_id = self.progress.add_task(
            f"[cyan]{event.file_path}[/cyan]",
            total=event.total_tests,
        )
        self.file_tasks[event.file_path] = task_id

    def _handle_test_completed(self, event: TestCompletedEvent) -> None:
        """Handle test completion event."""
        # Update the progress bar for this file
        task_id = self.file_tasks.get(event.file_path)

        if task_id is not None:
            self.progress.update(task_id, advance=1)

        # Update overall stats
        if event.status == "passed":
            self.passed += 1
        elif event.status == "failed":
            self.failed += 1
            # Store failure for later display
            if event.message:
                self.failures.append((event.test_id, event.file_path, event.message))
        elif event.status == "skipped":
            self.skipped += 1

    def _handle_file_completed(self, event: FileCompletedEvent) -> None:
        """Handle file completion event."""
        task_id = self.file_tasks.get(event.file_path)

        if task_id is not None:
            # Update description to show completion status
            if event.failed > 0:
                symbol = "✗" if not self.use_colors else "[red]✗[/red]"
                color = "red"
            else:
                symbol = "✓" if not self.use_colors else "[green]✓[/green]"
                color = "green"

            # Format duration
            if event.duration < 1:
                duration_str = f"{event.duration * 1000:.0f}ms"
            else:
                duration_str = f"{event.duration:.2f}s"

            self.progress.update(
                task_id,
                description=f"{symbol} [{color}]{event.file_path}[/{color}] [dim]({duration_str})[/dim]",
                completed=event.passed + event.failed + event.skipped,
            )

    def _handle_collection_error(self, event: CollectionErrorEvent) -> None:
        """Handle collection error event."""
        self.collection_errors.append((event.path, event.message))

    def _handle_suite_completed(self, event: SuiteCompletedEvent) -> None:
        """Handle suite completion event."""
        # Stop the live display
        if self.live:
            self.live.stop()
            self._started = False

        # Print collection errors
        if self.collection_errors:
            self.console.print()
            self.console.print("[bold red]ERRORS[/bold red]")
            self.console.print()

            for path, message in self.collection_errors:
                self.console.print(f"[bold red]ERROR collecting {path}[/bold red]")
                self.console.print("[dim]" + "─" * 70 + "[/dim]")
                self.console.print(message)
                self.console.print()

        # Print failures
        if self.failures:
            self.console.print()
            self.console.print("[bold red]FAILURES[/bold red]")
            self.console.print()

            for test_id, file_path, message in self.failures:
                # Extract test name from test_id
                test_name = test_id.split("::")[-1] if "::" in test_id else test_id

                self.console.print(f"[bold]{test_name}[/bold] [dim]({file_path})[/dim]")
                self.console.print("[dim]" + "─" * 70 + "[/dim]")
                self.console.print(message)
                self.console.print()

        # Print summary
        self.console.print()

        # Format duration
        if event.duration < 1:
            duration_str = f"{event.duration * 1000:.0f}ms"
        else:
            duration_str = f"{event.duration:.2f}s"

        # Build summary parts
        parts = []
        if event.passed > 0:
            parts.append(f"[green]✓ {event.passed} passed[/green]")
        if event.failed > 0:
            parts.append(f"[red]✗ {event.failed} failed[/red]")
        if event.skipped > 0:
            parts.append(f"[yellow]⊘ {event.skipped} skipped[/yellow]")
        if event.errors > 0:
            parts.append(f"[red]{event.errors} error[/red]")

        if not parts:
            parts.append("0 tests")

        summary = ", ".join(parts) + f" [dim]in {duration_str}[/dim]"
        self.console.print(summary)
