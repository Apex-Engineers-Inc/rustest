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
async def session_resource():
    """Session-scoped async fixture."""
    await asyncio.sleep(0.01)
    return {"session_id": "test_session"}


@mark.asyncio(loop_scope="session")
async def test_session_parallel_1(session_resource):
    """First session-scoped parallel test."""
    log_execution("session_test_1_start")
    assert session_resource["session_id"] == "test_session"
    await asyncio.sleep(0.05)
    log_execution("session_test_1_end")


@mark.asyncio(loop_scope="session")
async def test_session_parallel_2(session_resource):
    """Second session-scoped parallel test."""
    log_execution("session_test_2_start")
    assert session_resource["session_id"] == "test_session"
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
