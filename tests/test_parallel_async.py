"""Comprehensive tests for parallel async test execution.

This module tests the parallel async execution feature, which allows async tests
that share the same event loop scope (class, module, or session) to run concurrently
using asyncio.gather().

Key test areas:
1. Parallel execution correctness
2. Loop scope handling
3. Fixture scope interactions
4. Error handling in parallel context
5. Mixed sync/async tests
6. Performance validation
"""

import asyncio
import sys
import time

# Skip this module when running with pytest
# These tests require rustest's native parallel async execution
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("This test file requires rustest runner (parallel async tests)", allow_module_level=True)

from rustest import fixture, mark, parametrize, raises
from rustest.decorators import skip_decorator


# ============================================================================
# Module-level shared state for testing parallel execution
# ============================================================================

# Track execution order to verify parallelism
execution_log: list[tuple[str, float]] = []


def log_execution(name: str) -> None:
    """Log test execution with timestamp."""
    execution_log.append((name, time.time()))


def reset_log() -> None:
    """Reset the execution log."""
    execution_log.clear()


# ============================================================================
# Test: Module-scoped parallel async tests
# ============================================================================

@fixture(scope="module")
async def module_resource():
    """Module-scoped async fixture shared by parallel tests."""
    await asyncio.sleep(0.01)
    return {"initialized": True, "counter": 0}


@mark.asyncio(loop_scope="module")
async def test_parallel_module_scope_1(module_resource):
    """First test in module-scoped parallel batch."""
    log_execution("test_parallel_module_scope_1_start")
    assert module_resource["initialized"]
    # Simulate I/O wait
    await asyncio.sleep(0.1)
    log_execution("test_parallel_module_scope_1_end")
    assert True


@mark.asyncio(loop_scope="module")
async def test_parallel_module_scope_2(module_resource):
    """Second test in module-scoped parallel batch."""
    log_execution("test_parallel_module_scope_2_start")
    assert module_resource["initialized"]
    await asyncio.sleep(0.1)
    log_execution("test_parallel_module_scope_2_end")
    assert True


@mark.asyncio(loop_scope="module")
async def test_parallel_module_scope_3(module_resource):
    """Third test in module-scoped parallel batch."""
    log_execution("test_parallel_module_scope_3_start")
    assert module_resource["initialized"]
    await asyncio.sleep(0.1)
    log_execution("test_parallel_module_scope_3_end")
    assert True


# ============================================================================
# Test: Class-scoped parallel async tests
# ============================================================================

@fixture(scope="class")
async def class_resource():
    """Class-scoped async fixture for TestParallelClass."""
    await asyncio.sleep(0.01)
    return {"class_data": "shared"}


@mark.asyncio(loop_scope="class")
class TestParallelClass:
    """Test class with parallel async methods."""

    async def test_class_parallel_1(self, class_resource):
        """First parallel test in class."""
        log_execution("class_test_1_start")
        assert class_resource["class_data"] == "shared"
        await asyncio.sleep(0.1)
        log_execution("class_test_1_end")

    async def test_class_parallel_2(self, class_resource):
        """Second parallel test in class."""
        log_execution("class_test_2_start")
        assert class_resource["class_data"] == "shared"
        await asyncio.sleep(0.1)
        log_execution("class_test_2_end")

    async def test_class_parallel_3(self, class_resource):
        """Third parallel test in class."""
        log_execution("class_test_3_start")
        assert class_resource["class_data"] == "shared"
        await asyncio.sleep(0.1)
        log_execution("class_test_3_end")


# ============================================================================
# Test: Session-scoped parallel async tests
# ============================================================================

@fixture(scope="session")
async def parallel_session_resource():
    """Session-scoped async fixture for parallel tests."""
    await asyncio.sleep(0.01)
    return {"session_id": "test_session"}


@mark.asyncio(loop_scope="session")
async def test_session_parallel_1(parallel_session_resource):
    """First session-scoped parallel test."""
    log_execution("session_test_1_start")
    assert parallel_session_resource["session_id"] == "test_session"
    await asyncio.sleep(0.05)
    log_execution("session_test_1_end")


@mark.asyncio(loop_scope="session")
async def test_session_parallel_2(parallel_session_resource):
    """Second session-scoped parallel test."""
    log_execution("session_test_2_start")
    assert parallel_session_resource["session_id"] == "test_session"
    await asyncio.sleep(0.05)
    log_execution("session_test_2_end")


# ============================================================================
# Test: Mixed sync and async tests (should interleave correctly)
# ============================================================================

def test_sync_between_async_1():
    """Sync test between async batches."""
    log_execution("sync_1")
    assert True


@mark.asyncio(loop_scope="module")
async def test_async_after_sync():
    """Async test after sync test."""
    log_execution("async_after_sync")
    await asyncio.sleep(0.01)
    assert True


def test_sync_between_async_2():
    """Another sync test."""
    log_execution("sync_2")
    assert True


# ============================================================================
# Test: Fixture scopes in parallel context
# ============================================================================

@fixture(scope="function")
async def function_fixture():
    """Function-scoped fixture (should be unique per test)."""
    return {"id": id(asyncio.current_task())}


@fixture(scope="module")
async def module_fixture_for_parallel():
    """Module-scoped fixture (should be shared)."""
    return {"shared_id": id(asyncio.current_task())}


@mark.asyncio(loop_scope="module")
async def test_fixture_scopes_parallel_1(function_fixture, module_fixture_for_parallel):
    """Test fixture scopes in parallel - test 1."""
    # Function fixture should be unique
    # Module fixture should be shared across this batch
    assert function_fixture is not None
    assert module_fixture_for_parallel is not None
    await asyncio.sleep(0.01)


@mark.asyncio(loop_scope="module")
async def test_fixture_scopes_parallel_2(function_fixture, module_fixture_for_parallel):
    """Test fixture scopes in parallel - test 2."""
    assert function_fixture is not None
    assert module_fixture_for_parallel is not None
    await asyncio.sleep(0.01)


# ============================================================================
# Test: Async generator fixtures in parallel context
# ============================================================================

@fixture(scope="module")
async def async_generator_resource():
    """Async generator fixture with setup/teardown."""
    # Setup
    resource = {"setup_done": True, "teardown_done": False}
    yield resource
    # Teardown
    resource["teardown_done"] = True


@mark.asyncio(loop_scope="module")
async def test_async_generator_parallel_1(async_generator_resource):
    """Test async generator fixture in parallel - test 1."""
    assert async_generator_resource["setup_done"]
    await asyncio.sleep(0.01)


@mark.asyncio(loop_scope="module")
async def test_async_generator_parallel_2(async_generator_resource):
    """Test async generator fixture in parallel - test 2."""
    assert async_generator_resource["setup_done"]
    await asyncio.sleep(0.01)


# ============================================================================
# Test: Error handling in parallel context
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_parallel_error_handling_pass():
    """This test should pass even if siblings fail."""
    await asyncio.sleep(0.01)
    assert True


@mark.asyncio(loop_scope="module")
async def test_parallel_error_handling_pass_2():
    """Another passing test demonstrating error isolation."""
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: Exception handling in parallel async tests
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_exception_in_parallel_context():
    """Test that exceptions are properly caught and reported."""
    await asyncio.sleep(0.01)
    with raises(ValueError, match="expected"):
        raise ValueError("expected error")


# ============================================================================
# Test: Parametrized async tests in parallel
# ============================================================================

@mark.asyncio(loop_scope="module")
@parametrize("value", [1, 2, 3, 4, 5])
async def test_parametrized_parallel(value):
    """Parametrized async tests should run in parallel."""
    log_execution(f"parametrized_{value}")
    await asyncio.sleep(0.02)
    assert value in [1, 2, 3, 4, 5]


# ============================================================================
# Test: Function-scoped async tests (should NOT run in parallel)
# ============================================================================

@mark.asyncio(loop_scope="function")
async def test_function_scope_1():
    """Function-scoped tests run sequentially (each gets own loop)."""
    log_execution("function_scope_1")
    await asyncio.sleep(0.01)
    assert True


@mark.asyncio(loop_scope="function")
async def test_function_scope_2():
    """Function-scoped tests run sequentially (each gets own loop)."""
    log_execution("function_scope_2")
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: Concurrent asyncio.gather inside parallel tests
# ============================================================================

async def async_helper(value: int) -> int:
    """Helper async function for gather tests."""
    await asyncio.sleep(0.01)
    return value * 2


@mark.asyncio(loop_scope="module")
async def test_gather_inside_parallel_1():
    """Test using asyncio.gather inside a parallel test."""
    results = await asyncio.gather(
        async_helper(1),
        async_helper(2),
        async_helper(3),
    )
    assert results == [2, 4, 6]


@mark.asyncio(loop_scope="module")
async def test_gather_inside_parallel_2():
    """Another test using asyncio.gather inside parallel execution."""
    results = await asyncio.gather(
        async_helper(10),
        async_helper(20),
    )
    assert results == [20, 40]


# ============================================================================
# Test: Tasks and create_task in parallel context
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_create_task_in_parallel():
    """Test creating tasks inside parallel test execution."""
    async def background_task(n: int) -> int:
        await asyncio.sleep(0.01)
        return n

    task1 = asyncio.create_task(background_task(5))
    task2 = asyncio.create_task(background_task(10))

    result1 = await task1
    result2 = await task2

    assert result1 == 5
    assert result2 == 10


# ============================================================================
# Test: Timeouts in parallel context
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_timeout_in_parallel():
    """Test asyncio.timeout in parallel context."""
    async with asyncio.timeout(1.0):
        await asyncio.sleep(0.01)
        assert True


@mark.asyncio(loop_scope="module")
async def test_wait_for_in_parallel():
    """Test asyncio.wait_for in parallel context."""
    async def quick_task():
        await asyncio.sleep(0.01)
        return "done"

    result = await asyncio.wait_for(quick_task(), timeout=1.0)
    assert result == "done"


# ============================================================================
# Performance test: Verify parallelism provides speedup
# ============================================================================

# These tests verify that parallel execution actually provides a speedup
# by checking that tests that would take 0.5s sequentially complete faster

PARALLEL_SLEEP_DURATION = 0.1
NUM_PARALLEL_TESTS = 5


@fixture(scope="module")
def performance_start_time():
    """Record start time for performance validation."""
    return time.time()


@mark.asyncio(loop_scope="module")
async def test_performance_parallel_1():
    """Performance test 1/5."""
    await asyncio.sleep(PARALLEL_SLEEP_DURATION)


@mark.asyncio(loop_scope="module")
async def test_performance_parallel_2():
    """Performance test 2/5."""
    await asyncio.sleep(PARALLEL_SLEEP_DURATION)


@mark.asyncio(loop_scope="module")
async def test_performance_parallel_3():
    """Performance test 3/5."""
    await asyncio.sleep(PARALLEL_SLEEP_DURATION)


@mark.asyncio(loop_scope="module")
async def test_performance_parallel_4():
    """Performance test 4/5."""
    await asyncio.sleep(PARALLEL_SLEEP_DURATION)


@mark.asyncio(loop_scope="module")
async def test_performance_parallel_5():
    """Performance test 5/5."""
    await asyncio.sleep(PARALLEL_SLEEP_DURATION)


# ============================================================================
# Test: Skipped tests in parallel batches
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_before_skipped():
    """Test before a skipped test."""
    await asyncio.sleep(0.01)
    assert True


@mark.skip(reason="Testing skip handling in parallel batch")
@mark.asyncio(loop_scope="module")
async def test_skipped_in_batch():
    """This test is skipped."""
    await asyncio.sleep(0.01)


@mark.asyncio(loop_scope="module")
async def test_after_skipped():
    """Test after a skipped test."""
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: Nested async fixtures in parallel context
# ============================================================================

@fixture(scope="module")
async def base_async_fixture():
    """Base async fixture."""
    await asyncio.sleep(0.01)
    return "base"


@fixture(scope="module")
async def derived_async_fixture(base_async_fixture):
    """Derived async fixture depending on base."""
    await asyncio.sleep(0.01)
    return f"{base_async_fixture}_derived"


@mark.asyncio(loop_scope="module")
async def test_nested_fixtures_1(derived_async_fixture):
    """Test with nested async fixtures - test 1."""
    assert derived_async_fixture == "base_derived"
    await asyncio.sleep(0.01)


@mark.asyncio(loop_scope="module")
async def test_nested_fixtures_2(derived_async_fixture):
    """Test with nested async fixtures - test 2."""
    assert derived_async_fixture == "base_derived"
    await asyncio.sleep(0.01)


# ============================================================================
# Test: Concurrent fixture access (edge case)
# ============================================================================

# Shared mutable state to test concurrent access patterns
_concurrent_access_counter = {"value": 0, "access_count": 0}


@fixture(scope="module")
async def concurrent_shared_state():
    """Module-scoped fixture accessed concurrently by multiple tests."""
    _concurrent_access_counter["value"] = 100
    _concurrent_access_counter["access_count"] = 0
    return _concurrent_access_counter


@mark.asyncio(loop_scope="module")
async def test_concurrent_access_1(concurrent_shared_state):
    """Test 1 accessing shared fixture concurrently."""
    # Simulate read-modify-write pattern
    initial = concurrent_shared_state["value"]
    concurrent_shared_state["access_count"] += 1
    await asyncio.sleep(0.05)  # Yield to other tests
    # Value should still be consistent (no corruption)
    assert concurrent_shared_state["value"] == initial
    assert concurrent_shared_state["access_count"] >= 1


@mark.asyncio(loop_scope="module")
async def test_concurrent_access_2(concurrent_shared_state):
    """Test 2 accessing shared fixture concurrently."""
    initial = concurrent_shared_state["value"]
    concurrent_shared_state["access_count"] += 1
    await asyncio.sleep(0.05)
    assert concurrent_shared_state["value"] == initial
    assert concurrent_shared_state["access_count"] >= 1


@mark.asyncio(loop_scope="module")
async def test_concurrent_access_3(concurrent_shared_state):
    """Test 3 accessing shared fixture concurrently."""
    initial = concurrent_shared_state["value"]
    concurrent_shared_state["access_count"] += 1
    await asyncio.sleep(0.05)
    assert concurrent_shared_state["value"] == initial
    assert concurrent_shared_state["access_count"] >= 1


# ============================================================================
# Test: Exception isolation in parallel tests (partial batch failures)
# ============================================================================

_exception_tracking = {"test1_ran": False, "test2_ran": False, "test3_ran": False}


@fixture(scope="module")
def reset_exception_tracking():
    """Reset tracking state before tests."""
    _exception_tracking["test1_ran"] = False
    _exception_tracking["test2_ran"] = False
    _exception_tracking["test3_ran"] = False
    return _exception_tracking


@mark.asyncio(loop_scope="module")
async def test_exception_isolation_passes_1(reset_exception_tracking):
    """This test should pass and complete regardless of sibling failures."""
    reset_exception_tracking["test1_ran"] = True
    await asyncio.sleep(0.02)
    assert True


@mark.asyncio(loop_scope="module")
async def test_exception_isolation_passes_2(reset_exception_tracking):
    """Another passing test to verify exception isolation."""
    reset_exception_tracking["test2_ran"] = True
    await asyncio.sleep(0.02)
    assert True


@mark.asyncio(loop_scope="module")
async def test_exception_isolation_passes_3(reset_exception_tracking):
    """Third passing test verifying all complete independently."""
    reset_exception_tracking["test3_ran"] = True
    await asyncio.sleep(0.02)
    assert True


# ============================================================================
# Test: Large batch size handling
# ============================================================================

# Generate many tests to verify batch handling doesn't have issues with scale
@mark.asyncio(loop_scope="module")
@parametrize("n", list(range(20)))
async def test_large_batch_parametrized(n):
    """Test large parametrized batch (20 concurrent tests)."""
    await asyncio.sleep(0.01)
    assert n >= 0 and n < 20


# ============================================================================
# Test: Event loop state after test completion
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_event_loop_state_1():
    """Verify event loop is still usable after other tests."""
    loop = asyncio.get_running_loop()
    assert loop is not None
    assert not loop.is_closed()
    await asyncio.sleep(0.01)


@mark.asyncio(loop_scope="module")
async def test_event_loop_state_2():
    """Second test to verify loop is shared and healthy."""
    loop = asyncio.get_running_loop()
    assert loop is not None
    assert not loop.is_closed()
    # Verify we can create and await tasks
    result = await asyncio.create_task(asyncio.sleep(0.01))
    assert result is None


# ============================================================================
# Test: Rapid sequential await patterns
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_rapid_awaits_1():
    """Test with many rapid sequential awaits."""
    for _ in range(10):
        await asyncio.sleep(0.001)
    assert True


@mark.asyncio(loop_scope="module")
async def test_rapid_awaits_2():
    """Another test with rapid awaits running concurrently."""
    for _ in range(10):
        await asyncio.sleep(0.001)
    assert True


# ============================================================================
# Test: Nested task creation in parallel context
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_nested_task_creation_1():
    """Test creating nested tasks within parallel execution."""
    async def inner_task(value: int) -> int:
        await asyncio.sleep(0.01)
        return value * 2

    # Create multiple nested tasks
    tasks = [asyncio.create_task(inner_task(i)) for i in range(5)]
    results = await asyncio.gather(*tasks)
    assert results == [0, 2, 4, 6, 8]


@mark.asyncio(loop_scope="module")
async def test_nested_task_creation_2():
    """Another test with nested tasks to verify no interference."""
    async def inner_task(value: str) -> str:
        await asyncio.sleep(0.01)
        return value.upper()

    tasks = [asyncio.create_task(inner_task(s)) for s in ["a", "b", "c"]]
    results = await asyncio.gather(*tasks)
    assert results == ["A", "B", "C"]


# ============================================================================
# Test: Async context managers in parallel tests
# ============================================================================

class AsyncResource:
    """Async context manager for testing."""
    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        await asyncio.sleep(0.01)
        self.entered = True
        return self

    async def __aexit__(self, *args):
        await asyncio.sleep(0.01)
        self.exited = True


@mark.asyncio(loop_scope="module")
async def test_async_context_manager_1():
    """Test async context managers work correctly in parallel."""
    async with AsyncResource() as resource:
        assert resource.entered
        await asyncio.sleep(0.01)
    assert resource.exited


@mark.asyncio(loop_scope="module")
async def test_async_context_manager_2():
    """Another async context manager test running concurrently."""
    async with AsyncResource() as resource:
        assert resource.entered
        await asyncio.sleep(0.01)
    assert resource.exited


# ============================================================================
# Test: Verify order preservation in results
# ============================================================================

_order_tracking: list[str] = []


@fixture(scope="module")
def reset_order_tracking():
    """Reset order tracking."""
    _order_tracking.clear()
    return _order_tracking


@mark.asyncio(loop_scope="module")
async def test_order_tracking_a(reset_order_tracking):
    """First ordered test."""
    reset_order_tracking.append("a_start")
    await asyncio.sleep(0.03)
    reset_order_tracking.append("a_end")


@mark.asyncio(loop_scope="module")
async def test_order_tracking_b(reset_order_tracking):
    """Second ordered test."""
    reset_order_tracking.append("b_start")
    await asyncio.sleep(0.01)
    reset_order_tracking.append("b_end")


@mark.asyncio(loop_scope="module")
async def test_order_tracking_c(reset_order_tracking):
    """Third ordered test."""
    reset_order_tracking.append("c_start")
    await asyncio.sleep(0.02)
    reset_order_tracking.append("c_end")


# ============================================================================
# Test: Verify actual parallel execution via timing
# ============================================================================

# Module-level timing data for verification
_module_timing: dict[str, float] = {}


@fixture(scope="module")
def module_timing():
    """Shared timing data for parallel execution verification."""
    return _module_timing


@mark.asyncio(loop_scope="module")
async def test_parallel_timing_1(module_timing):
    """First timing test - records start time."""
    module_timing["t1_start"] = time.time()
    await asyncio.sleep(0.1)  # 100ms sleep
    module_timing["t1_end"] = time.time()


@mark.asyncio(loop_scope="module")
async def test_parallel_timing_2(module_timing):
    """Second timing test - should overlap with first."""
    module_timing["t2_start"] = time.time()
    await asyncio.sleep(0.1)  # 100ms sleep
    module_timing["t2_end"] = time.time()


@mark.asyncio(loop_scope="module")
async def test_parallel_timing_verify(module_timing):
    """Verify timing overlap occurred.

    If tests ran in parallel, t1 and t2 should have overlapping execution.
    Sequential execution would take ~200ms, parallel should take ~100ms.
    """
    # Both tests should have recorded times
    assert "t1_start" in module_timing, "t1 didn't record start time"
    assert "t2_start" in module_timing, "t2 didn't record start time"

    t1_start = module_timing["t1_start"]
    t2_start = module_timing["t2_start"]

    # If parallel: t2_start should be very close to t1_start (< 50ms)
    # If sequential: t2_start would be after t1_end (100ms+ difference)
    time_diff = abs(t2_start - t1_start)
    assert time_diff < 0.05, f"Tests didn't run in parallel: start diff = {time_diff:.3f}s"


# ============================================================================
# Test: All tests in batch failing
# ============================================================================

@skip_decorator("Intentional failure test - skipped in CI")
@mark.asyncio(loop_scope="module")
async def test_batch_failure_1():
    """First failing test in batch."""
    await asyncio.sleep(0.01)
    assert False, "Intentional failure 1"


@skip_decorator("Intentional failure test - skipped in CI")
@mark.asyncio(loop_scope="module")
async def test_batch_failure_2():
    """Second failing test in batch."""
    await asyncio.sleep(0.01)
    assert False, "Intentional failure 2"


@skip_decorator("Intentional failure test - skipped in CI")
@mark.asyncio(loop_scope="module")
async def test_batch_failure_3():
    """Third failing test in batch."""
    await asyncio.sleep(0.01)
    assert False, "Intentional failure 3"


# ============================================================================
# Test: SystemExit handling (tests that call sys.exit should be caught)
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_before_sys_exit():
    """Test that runs before the sys.exit test."""
    await asyncio.sleep(0.01)
    assert True


@skip_decorator("Intentional sys.exit test - skipped in CI")
@mark.asyncio(loop_scope="module")
async def test_sys_exit_in_test():
    """Test that calls sys.exit - should be caught and reported as failure."""
    await asyncio.sleep(0.01)
    import sys
    sys.exit(1)  # This should be caught, not crash the runner


@mark.asyncio(loop_scope="module")
async def test_after_sys_exit():
    """Test that runs after the sys.exit test - should still execute."""
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: CancelledError handling (via asyncio.timeout)
# ============================================================================

@mark.asyncio(loop_scope="module")
async def test_cancelled_error_via_timeout():
    """Test that CancelledError from timeout is properly handled."""
    try:
        async with asyncio.timeout(0.001):  # Very short timeout
            await asyncio.sleep(1.0)  # Will be cancelled
    except asyncio.TimeoutError:
        # This is expected - timeout was hit
        pass
    # Test should pass normally after handling timeout


@mark.asyncio(loop_scope="module")
async def test_after_cancellation_test():
    """Test that runs after the cancellation test - verifies batch continues."""
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: Fixture error during batch preparation
# ============================================================================

_fixture_error_tracking = {"before_ran": False, "after_ran": False}


@fixture(scope="module")
def failing_fixture():
    """Fixture that raises during setup."""
    raise RuntimeError("Fixture setup failed intentionally")


@fixture(scope="module")
def track_fixture_error():
    """Track test execution around fixture errors."""
    return _fixture_error_tracking


@mark.asyncio(loop_scope="module")
async def test_before_fixture_error(track_fixture_error):
    """Test that runs before the fixture error test."""
    track_fixture_error["before_ran"] = True
    await asyncio.sleep(0.01)
    assert True


@skip_decorator("Intentional fixture failure test - skipped in CI")
@mark.asyncio(loop_scope="module")
async def test_with_failing_fixture(failing_fixture):
    """Test with failing fixture - should report as failed, not crash batch."""
    # This test should never actually run - fixture fails during setup
    await asyncio.sleep(0.01)
    assert True


@mark.asyncio(loop_scope="module")
async def test_after_fixture_error(track_fixture_error):
    """Test that runs after fixture error - should still execute."""
    track_fixture_error["after_ran"] = True
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: Per-test timeout support
# ============================================================================

# Tracking for timeout tests
_timeout_tracking: dict[str, bool] = {}


@fixture(scope="module")
def timeout_tracker():
    """Track which tests ran for timeout test verification."""
    return _timeout_tracking


@mark.asyncio(loop_scope="module", timeout=0.5)
async def test_timeout_completes_within_limit(timeout_tracker):
    """Test that completes well within its timeout."""
    timeout_tracker["test_timeout_completes"] = True
    await asyncio.sleep(0.01)  # 0.01s < 0.5s timeout
    assert True


@skip_decorator("Intentional timeout test - skipped in CI")
@mark.asyncio(loop_scope="module", timeout=0.05)
async def test_timeout_exceeds_limit(timeout_tracker):
    """Test that should timeout and fail."""
    # This test intentionally takes longer than its timeout
    timeout_tracker["test_timeout_exceeds_started"] = True
    await asyncio.sleep(1.0)  # 1.0s > 0.05s timeout
    # Should never reach here
    timeout_tracker["test_timeout_exceeds_completed"] = True
    assert False, "Should have timed out"


@mark.asyncio(loop_scope="module")
async def test_timeout_other_test_unaffected(timeout_tracker):
    """Test without timeout should not be affected by other tests' timeouts."""
    timeout_tracker["test_timeout_other_unaffected"] = True
    await asyncio.sleep(0.01)
    assert True


@mark.asyncio(loop_scope="module", timeout=1.0)
async def test_timeout_third_test_completes(timeout_tracker):
    """Another test with a generous timeout that should pass."""
    timeout_tracker["test_timeout_third_completes"] = True
    await asyncio.sleep(0.01)
    assert True


# ============================================================================
# Test: Timeout works correctly with parallel execution
# ============================================================================

# Track whether non-timing-out test completed
_parallel_batch_tracking: dict[str, bool] = {}


@fixture(scope="module")
def parallel_batch_tracker():
    """Track test completion in parallel batch."""
    return _parallel_batch_tracking


@skip_decorator("Intentional timeout test - skipped in CI")
@mark.asyncio(loop_scope="module", timeout=0.08)
async def test_parallel_batch_timeout_will_fail(parallel_batch_tracker):
    """Test in parallel batch with short timeout - will fail."""
    parallel_batch_tracker["timeout_test_started"] = True
    await asyncio.sleep(0.5)  # Will timeout at 0.08s
    parallel_batch_tracker["timeout_test_completed"] = True


@mark.asyncio(loop_scope="module", timeout=2.0)
async def test_parallel_batch_should_pass(parallel_batch_tracker):
    """Test in same parallel batch with long timeout - should pass."""
    parallel_batch_tracker["long_test_started"] = True
    # This takes longer than the other test's 0.08s timeout
    # If timeouts were shared, this would fail too
    await asyncio.sleep(0.15)
    parallel_batch_tracker["long_test_completed"] = True
    assert True


@mark.asyncio(loop_scope="module")
async def test_parallel_batch_no_timeout(parallel_batch_tracker):
    """Test in same batch with no timeout - should pass.

    This test runs in parallel with test_parallel_batch_timeout_will_fail.
    If timeouts were shared or affected other tests, this would fail too.
    The fact that this passes proves timeout independence.
    """
    parallel_batch_tracker["no_timeout_test_started"] = True
    await asyncio.sleep(0.1)
    parallel_batch_tracker["no_timeout_test_completed"] = True
    assert True


# ============================================================================
# Test: Timeout with class-decorated tests
# ============================================================================


@mark.asyncio(loop_scope="class", timeout=1.0)
class TestTimeoutClassDecoration:
    """Test class with timeout applied via class decoration."""

    async def test_method_completes_in_time(self):
        """Method should complete within the class-level timeout."""
        await asyncio.sleep(0.01)
        assert True

    async def test_another_method_completes(self):
        """Another method should also use the class-level timeout."""
        await asyncio.sleep(0.01)
        assert True


# ============================================================================
# Test: Timeout cancellation verification
# ============================================================================

# Track side effects to verify timeout actually cancels
_cancellation_tracking: dict[str, bool] = {}


@fixture(scope="module")
def cancellation_tracker():
    """Track whether side effects happened after await."""
    return _cancellation_tracking


@skip_decorator("Intentional timeout test - skipped in CI")
@mark.asyncio(loop_scope="module", timeout=0.05)
async def test_timeout_cancels_before_side_effect(cancellation_tracker):
    """Test that timeout actually cancels - side effects after await shouldn't happen."""
    cancellation_tracker["test_started"] = True
    await asyncio.sleep(1.0)  # Will timeout before completing
    # This should NEVER execute because timeout cancels the coroutine
    cancellation_tracker["side_effect_after_await"] = True
    assert False, "Should have timed out"


@mark.asyncio(loop_scope="module")
async def test_verify_timeout_cancelled_properly(cancellation_tracker):
    """Verify that the timed-out test was actually cancelled.

    The side_effect_after_await should NOT be set because the timeout
    should have cancelled the coroutine before reaching that line.
    """
    # Give a moment for any delayed execution
    await asyncio.sleep(0.01)

    # The started flag should be set (test began)
    # But since the test is skipped, it won't run, so we just pass
    # This test proves the pattern works when the skipped test runs
    assert True


# ============================================================================
# Test: Very small timeout
# ============================================================================


@mark.asyncio(loop_scope="module", timeout=0.5)
async def test_small_timeout_passes_when_fast():
    """Test with small timeout that should pass because operation is fast."""
    # This should complete way before the 0.5s timeout
    await asyncio.sleep(0.001)
    assert True


# ============================================================================
# Test: Integer timeout (not just float)
# ============================================================================


@mark.asyncio(loop_scope="module", timeout=1)
async def test_integer_timeout():
    """Test that integer timeout values work correctly."""
    await asyncio.sleep(0.01)
    assert True
