"""Event routing system for test execution events.

This module provides infrastructure for routing test events from Rust
to multiple Python consumers (terminal, VS Code, JSON export, etc.).
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rustest.rust import (
        CollectionErrorEvent,
        FileCompletedEvent,
        FileStartedEvent,
        SuiteCompletedEvent,
        SuiteStartedEvent,
        TestCompletedEvent,
    )

    EventType = (
        SuiteStartedEvent
        | SuiteCompletedEvent
        | FileStartedEvent
        | FileCompletedEvent
        | TestCompletedEvent
        | CollectionErrorEvent
    )


class EventConsumer(Protocol):
    """Protocol for event consumers.

    Consumers implement a handle() method that processes events.
    """

    def handle(self, event: EventType) -> None:
        """Handle a test execution event.

        Args:
            event: Event object from rust module (FileStartedEvent, TestCompletedEvent, etc.)
        """
        ...


class EventRouter:
    """Routes events from Rust to multiple Python consumers.

    This class is called from Rust threads (via PyO3), but the GIL
    ensures that only one call happens at a time, providing natural
    serialization of events.

    Example:
        router = EventRouter()
        router.subscribe(RichRenderer())
        router.subscribe(VSCodeAdapter())

        # Pass router.emit to Rust
        rust.run(..., event_callback=router.emit)
    """

    def __init__(self) -> None:
        super().__init__()
        self._consumers: list[EventConsumer] = []
        self._lock = threading.Lock()

    def subscribe(self, consumer: EventConsumer) -> None:
        """Subscribe a consumer to receive events.

        Args:
            consumer: Object with a handle(event) method
        """
        with self._lock:
            self._consumers.append(consumer)

    def unsubscribe(self, consumer: EventConsumer) -> None:
        """Unsubscribe a consumer from receiving events.

        Args:
            consumer: Previously subscribed consumer
        """
        with self._lock:
            if consumer in self._consumers:
                self._consumers.remove(consumer)

    def emit(self, event: EventType) -> None:
        """Emit an event to all subscribed consumers.

        Called from Rust thread (with GIL held). The GIL serializes
        calls automatically, so events are processed one at a time.

        Args:
            event: Event object from rust module
        """
        # Make a copy of consumers to avoid holding lock during iteration
        with self._lock:
            consumers = list(self._consumers)

        # Dispatch to all consumers
        for consumer in consumers:
            try:
                consumer.handle(event)
            except Exception as e:
                # Don't let one consumer break others
                print(
                    f"Error in event consumer {consumer.__class__.__name__}: {e}",
                    file=sys.stderr,
                )
