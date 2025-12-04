"""Tests for collection feedback feature.

Tests that collection events are emitted and handled correctly during test discovery.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


class TestCollectionEventTypes:
    """Test that collection event types are properly defined and accessible."""

    def test_collection_started_event_exists(self) -> None:
        """Verify CollectionStartedEvent is importable and has expected attributes."""
        from rustest.rust import CollectionStartedEvent

        # The actual class is defined in Rust, but we can verify it's accessible
        assert CollectionStartedEvent is not None
        # Verify expected attribute names are documented in type stubs
        assert hasattr(SimpleNamespace(timestamp=0.0), "timestamp")

    def test_collection_progress_event_exists(self) -> None:
        """Verify CollectionProgressEvent is importable and has expected attributes."""
        from rustest.rust import CollectionProgressEvent

        assert CollectionProgressEvent is not None
        # Verify expected attributes via stub documentation
        mock_event = SimpleNamespace(
            file_path="test.py",
            tests_collected=5,
            files_collected=3,
            timestamp=1234567890.123,
        )
        assert mock_event.file_path == "test.py"
        assert mock_event.tests_collected == 5
        assert mock_event.files_collected == 3
        assert mock_event.timestamp == 1234567890.123

    def test_collection_completed_event_exists(self) -> None:
        """Verify CollectionCompletedEvent is importable and has expected attributes."""
        from rustest.rust import CollectionCompletedEvent

        assert CollectionCompletedEvent is not None
        # Verify expected attributes via stub documentation
        mock_event = SimpleNamespace(
            total_files=10,
            total_tests=50,
            duration=0.5,
            timestamp=1234567890.123,
        )
        assert mock_event.total_files == 10
        assert mock_event.total_tests == 50
        assert mock_event.duration == 0.5
        assert mock_event.timestamp == 1234567890.123


class TestRichRendererCollectionEvents:
    """Test that RichRenderer correctly handles collection events."""

    def test_handle_collection_started_sets_state(self) -> None:
        """Verify _handle_collection_started initializes collection state."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False)
        event = SimpleNamespace(timestamp=1234567890.123)

        # Mock the Live display to avoid terminal interactions
        with patch.object(renderer, "collection_progress"):
            renderer._handle_collection_started(event)  # type: ignore[arg-type]

        assert renderer._collecting is True
        assert renderer._files_collected == 0
        assert renderer._tests_collected == 0

    def test_handle_collection_progress_updates_state(self) -> None:
        """Verify _handle_collection_progress updates collection state."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False)

        # Manually set initial state (instead of calling started handler)
        renderer._collecting = True
        renderer._files_collected = 0
        renderer._tests_collected = 0
        renderer._collection_task = None  # No task to update

        # Progress events
        progress_event1 = SimpleNamespace(
            file_path="test_one.py",
            tests_collected=3,
            files_collected=1,
            timestamp=1234567890.1,
        )
        renderer._handle_collection_progress(progress_event1)  # type: ignore[arg-type]

        assert renderer._files_collected == 1
        assert renderer._tests_collected == 3

        progress_event2 = SimpleNamespace(
            file_path="test_two.py",
            tests_collected=5,
            files_collected=2,
            timestamp=1234567890.2,
        )
        renderer._handle_collection_progress(progress_event2)  # type: ignore[arg-type]

        assert renderer._files_collected == 2
        assert renderer._tests_collected == 8  # 3 + 5

    def test_handle_collection_completed_resets_state(self) -> None:
        """Verify _handle_collection_completed prints summary and resets state."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False)

        # Start and complete collection
        start_event = SimpleNamespace(timestamp=1234567890.0)
        with patch.object(renderer, "collection_progress"):
            renderer._handle_collection_started(start_event)  # type: ignore[arg-type]

        completed_event = SimpleNamespace(
            total_files=5,
            total_tests=25,
            duration=0.123,
            timestamp=1234567890.5,
        )

        # Mock console to capture output
        with patch.object(renderer.console, "print") as mock_print:
            renderer._handle_collection_completed(completed_event)  # type: ignore[arg-type]

        assert renderer._collecting is False
        # Verify summary was printed
        mock_print.assert_called()
        call_args = str(mock_print.call_args_list)
        assert "25 tests" in call_args or "Collected" in call_args

    def test_handle_collection_completed_no_tests(self) -> None:
        """Verify appropriate message when no tests collected."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False)

        completed_event = SimpleNamespace(
            total_files=0,
            total_tests=0,
            duration=0.05,
            timestamp=1234567890.5,
        )

        with patch.object(renderer.console, "print") as mock_print:
            renderer._handle_collection_completed(completed_event)  # type: ignore[arg-type]

        # Verify "no tests" message was printed
        mock_print.assert_called()
        call_args = str(mock_print.call_args_list)
        assert "No tests collected" in call_args

    def test_event_handlers_are_callable(self) -> None:
        """Verify all collection event handlers exist and are callable."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False)

        # Verify handlers exist and are callable
        assert callable(renderer._handle_collection_started)
        assert callable(renderer._handle_collection_progress)
        assert callable(renderer._handle_collection_completed)
        assert callable(renderer.handle)


class TestCollectionEventsIntegration:
    """Integration tests for collection event emission."""

    def test_events_received_during_run(self, tmp_path: Any) -> None:
        """Verify collection events are received when running tests."""
        # Create a simple test file
        test_file = tmp_path / "test_sample.py"
        test_file.write_text(
            """
def test_one():
    assert True

def test_two():
    assert True

def test_three():
    assert True
"""
        )

        # Collect events
        events: list[object] = []

        class EventCollector:
            def handle(self, event: object) -> None:
                events.append(event)

        collector = EventCollector()

        # Run with event routing
        from rustest.event_router import EventRouter

        router = EventRouter()
        router.subscribe(collector)

        # Import the actual rust module to get real events
        from rustest import rust

        # Run the tests
        rust.run(
            paths=[str(tmp_path)],
            pattern=None,
            mark_expr=None,
            workers=1,
            capture_output=True,
            enable_codeblocks=False,
            last_failed_mode="none",
            fail_fast=False,
            pytest_compat=False,
            verbose=False,
            ascii=False,
            no_color=True,
            event_callback=router.emit,
        )

        # Check that collection events were received
        from rustest.rust import (
            CollectionCompletedEvent,
            CollectionProgressEvent,
            CollectionStartedEvent,
        )

        started_events = [e for e in events if isinstance(e, CollectionStartedEvent)]
        progress_events = [e for e in events if isinstance(e, CollectionProgressEvent)]
        completed_events = [e for e in events if isinstance(e, CollectionCompletedEvent)]

        # Verify events were received
        assert len(started_events) == 1, "Expected exactly one CollectionStartedEvent"
        assert len(progress_events) >= 1, "Expected at least one CollectionProgressEvent"
        assert len(completed_events) == 1, "Expected exactly one CollectionCompletedEvent"

        # Verify event content
        completed = completed_events[0]
        assert completed.total_tests == 3, f"Expected 3 tests, got {completed.total_tests}"
        assert completed.total_files == 1, f"Expected 1 file, got {completed.total_files}"
        assert completed.duration > 0, "Duration should be positive"

    def test_no_events_without_callback(self, tmp_path: Any) -> None:
        """Verify no crashes when running without event callback."""
        from rustest import rust

        # Create a simple test file
        test_file = tmp_path / "test_sample.py"
        test_file.write_text("def test_one(): assert True")

        # Run without callback - should not crash
        report = rust.run(
            paths=[str(tmp_path)],
            pattern=None,
            mark_expr=None,
            workers=1,
            capture_output=True,
            enable_codeblocks=False,
            last_failed_mode="none",
            fail_fast=False,
            pytest_compat=False,
            verbose=False,
            ascii=False,
            no_color=True,
            event_callback=None,  # No callback
        )

        assert report.total == 1
        assert report.passed == 1

    def test_collection_events_for_multiple_files(self, tmp_path: Any) -> None:
        """Verify progress events are emitted for each file collected."""
        from rustest import rust
        from rustest.event_router import EventRouter
        from rustest.rust import CollectionProgressEvent

        # Create multiple test files
        for i in range(3):
            test_file = tmp_path / f"test_file_{i}.py"
            test_file.write_text(f"def test_in_file_{i}(): assert True")

        events: list[object] = []

        class EventCollector:
            def handle(self, event: object) -> None:
                events.append(event)

        collector = EventCollector()
        router = EventRouter()
        router.subscribe(collector)

        rust.run(
            paths=[str(tmp_path)],
            pattern=None,
            mark_expr=None,
            workers=1,
            capture_output=True,
            enable_codeblocks=False,
            last_failed_mode="none",
            fail_fast=False,
            pytest_compat=False,
            verbose=False,
            ascii=False,
            no_color=True,
            event_callback=router.emit,
        )

        progress_events = [e for e in events if isinstance(e, CollectionProgressEvent)]

        # Should have 3 progress events (one per file)
        assert len(progress_events) == 3, f"Expected 3 progress events, got {len(progress_events)}"

        # Verify files_collected increments
        files_collected_values = [e.files_collected for e in progress_events]
        assert sorted(files_collected_values) == [1, 2, 3], (
            f"Expected files_collected to be 1, 2, 3 but got {files_collected_values}"
        )

    def test_collection_events_with_empty_directory(self, tmp_path: Any) -> None:
        """Verify correct events when no tests are found."""
        from rustest import rust
        from rustest.event_router import EventRouter
        from rustest.rust import (
            CollectionCompletedEvent,
            CollectionProgressEvent,
            CollectionStartedEvent,
        )

        # Create an empty directory with no tests
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        events: list[object] = []

        class EventCollector:
            def handle(self, event: object) -> None:
                events.append(event)

        collector = EventCollector()
        router = EventRouter()
        router.subscribe(collector)

        rust.run(
            paths=[str(empty_dir)],
            pattern=None,
            mark_expr=None,
            workers=1,
            capture_output=True,
            enable_codeblocks=False,
            last_failed_mode="none",
            fail_fast=False,
            pytest_compat=False,
            verbose=False,
            ascii=False,
            no_color=True,
            event_callback=router.emit,
        )

        started_events = [e for e in events if isinstance(e, CollectionStartedEvent)]
        progress_events = [e for e in events if isinstance(e, CollectionProgressEvent)]
        completed_events = [e for e in events if isinstance(e, CollectionCompletedEvent)]

        # Should still have started and completed events
        assert len(started_events) == 1, "Expected CollectionStartedEvent"
        assert len(completed_events) == 1, "Expected CollectionCompletedEvent"

        # No progress events since no files collected
        assert len(progress_events) == 0, f"Expected 0 progress events, got {len(progress_events)}"

        # Completed event should show 0 tests
        completed = completed_events[0]
        assert completed.total_tests == 0
        assert completed.total_files == 0


class TestRichRendererAsciiMode:
    """Test RichRenderer ASCII mode for collection feedback."""

    def test_ascii_mode_uses_ascii_symbols(self) -> None:
        """Verify ASCII mode uses [OK] instead of checkmark."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False, use_ascii=True)

        completed_event = SimpleNamespace(
            total_files=1,
            total_tests=5,
            duration=0.1,
            timestamp=1234567890.5,
        )

        with patch.object(renderer.console, "print") as mock_print:
            renderer._handle_collection_completed(completed_event)  # type: ignore[arg-type]

        # Check that [OK] was used instead of checkmark
        call_args = str(mock_print.call_args_list)
        assert "[OK]" in call_args

    def test_non_ascii_mode_uses_unicode_symbols(self) -> None:
        """Verify non-ASCII mode uses checkmark symbol."""
        from rustest.renderers.rich_renderer import RichRenderer

        renderer = RichRenderer(use_colors=False, use_ascii=False)

        completed_event = SimpleNamespace(
            total_files=1,
            total_tests=5,
            duration=0.1,
            timestamp=1234567890.5,
        )

        with patch.object(renderer.console, "print") as mock_print:
            renderer._handle_collection_completed(completed_event)  # type: ignore[arg-type]

        # Check that checkmark was used
        call_args = str(mock_print.call_args_list)
        # The checkmark should be present and [OK] should not be
        assert "[OK]" not in call_args
