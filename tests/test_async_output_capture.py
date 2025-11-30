"""Test that output capture works for async tests.

This test verifies that stdout/stderr from async tests are properly captured,
including tests that may be gathered for concurrent execution.
"""

import asyncio
import sys

from rustest import mark


@mark.asyncio(loop_scope="module")
async def test_async_with_stdout():
    """Async test that prints to stdout."""
    print("Hello from async stdout")
    await asyncio.sleep(0.001)
    assert True


@mark.asyncio(loop_scope="module")
async def test_async_with_stderr():
    """Async test that prints to stderr."""
    print("Hello from async stderr", file=sys.stderr)
    await asyncio.sleep(0.001)
    assert True


@mark.asyncio(loop_scope="module")
async def test_async_with_multiline_output():
    """Async test with multiline output."""
    for i in range(3):
        print(f"Async line {i}")
        await asyncio.sleep(0.001)
    assert True


@mark.asyncio(loop_scope="function")
async def test_function_scope_with_output():
    """Function-scoped async test with output (runs sequentially)."""
    print("Function-scoped async output")
    await asyncio.sleep(0.001)
    assert True


async def test_plain_async_with_output():
    """Plain async test (no explicit loop_scope) with output."""
    print("Plain async output")
    await asyncio.sleep(0.001)
    assert True
