"""Integration tests for the mocker fixture."""

from __future__ import annotations

from pathlib import Path

from .helpers import run_tree


def _write_mocker_test_module(target: Path) -> None:
    target.write_text(
        """
import os
from unittest.mock import MagicMock


# Example class for testing spy functionality
class Calculator:
    def add(self, a, b):
        return a + b

    def multiply(self, a, b):
        return a * b


# Example function for patching
def expensive_function():
    return "expensive"


def test_basic_patch(mocker):
    '''Test basic patching functionality.'''
    mock_remove = mocker.patch('os.remove')
    os.remove('/tmp/test.txt')
    mock_remove.assert_called_once_with('/tmp/test.txt')


def test_patch_with_return_value(mocker):
    '''Test patching with a return value.'''
    mock_exists = mocker.patch('os.path.exists', return_value=True)
    result = os.path.exists('/nonexistent')
    assert result is True
    mock_exists.assert_called_once_with('/nonexistent')


def test_patch_object(mocker):
    '''Test patch.object functionality.'''
    calc = Calculator()
    mock_add = mocker.patch.object(calc, 'add', return_value=99)
    result = calc.add(2, 3)
    assert result == 99
    mock_add.assert_called_once_with(2, 3)


def test_spy(mocker):
    '''Test spy functionality - calls through to original.'''
    calc = Calculator()
    spy = mocker.spy(calc, 'add')
    result = calc.add(2, 3)
    # Spy calls through to the original
    assert result == 5
    spy.assert_called_once_with(2, 3)


def test_spy_multiple_calls(mocker):
    '''Test spy with multiple calls.'''
    calc = Calculator()
    spy = mocker.spy(calc, 'multiply')
    calc.multiply(2, 3)
    calc.multiply(4, 5)
    assert spy.call_count == 2
    spy.assert_any_call(2, 3)
    spy.assert_any_call(4, 5)


def test_stub(mocker):
    '''Test stub creation.'''
    callback = mocker.stub(name='callback')
    callback('arg1', 'arg2')
    callback.assert_called_once_with('arg1', 'arg2')


def test_stub_multiple_calls(mocker):
    '''Test stub with multiple calls.'''
    callback = mocker.stub()
    callback(1)
    callback(2)
    callback(3)
    assert callback.call_count == 3


def test_direct_mock_usage(mocker):
    '''Test using Mock and MagicMock directly.'''
    mock_obj = mocker.MagicMock()
    mock_obj.method.return_value = 'result'
    assert mock_obj.method() == 'result'
    mock_obj.method.assert_called_once()


def test_mock_any(mocker):
    '''Test using mocker.ANY.'''
    mock_fn = mocker.Mock()
    mock_fn('test', 123)
    mock_fn.assert_called_once_with('test', mocker.ANY)


def test_mock_call(mocker):
    '''Test using mocker.call.'''
    mock_fn = mocker.Mock()
    mock_fn(1, 2)
    mock_fn(3, 4)
    assert mock_fn.call_args_list == [mocker.call(1, 2), mocker.call(3, 4)]


def test_resetall_resets_patches_not_bare_mocks(mocker):
    '''resetall() resets what the fixture CREATED -- patches -- and not a bare Mock().

    This is pytest-mock's own rule, not a limitation. `MockerFixture.resetall` iterates
    `self._mock_cache`, which is filled by `mocker.patch*` and `create_autospec`;
    `mocker.Mock` is a plain alias for `unittest.mock.Mock` (pytest_mock/plugin.py
    l. 88-104), so an object built through it was never registered and is not reset.

    rustest's v1 fixture WRAPPED the Mock classes to track every instance, so a bare
    `mocker.Mock()` was reset too -- a divergence from the library being emulated, and this
    test asserted it. v2 is the faithful port; the assertion follows.
    '''
    patched = mocker.patch('os.remove')
    bare = mocker.Mock(return_value=42)

    os.remove('/tmp/file')
    assert bare() == 42
    patched.assert_called_once()
    bare.assert_called_once()

    mocker.resetall()

    patched.assert_not_called()
    bare.assert_called_once()  # untracked by the fixture, so untouched by resetall()


def test_stopall(mocker):
    '''Test stopall functionality.'''
    mock_remove = mocker.patch('os.remove')
    os.remove('/tmp/file')
    mock_remove.assert_called_once()

    mocker.stopall()
    # After stopall, patches are removed but we can't easily test this
    # without side effects, so we just ensure stopall doesn't error


def test_patch_dict(mocker):
    '''Test patch.dict functionality.'''
    original = {'key': 'value'}
    mocker.patch.dict(original, {'key': 'patched', 'new': 'item'})
    assert original['key'] == 'patched'
    assert original['new'] == 'item'


def test_patch_multiple(mocker):
    '''Test patch.multiple functionality.'''
    import os
    mocker.patch.multiple(os, remove=MagicMock(), path=MagicMock())
    # Just verify it doesn't error
    assert True


def test_async_stub(mocker):
    '''Test async_stub creation.'''
    async_callback = mocker.async_stub(name='async_callback')
    # Just verify it's created correctly
    assert async_callback is not None


def test_property_mock(mocker):
    '''Test PropertyMock access.'''
    # Just verify PropertyMock is accessible
    prop_mock = mocker.PropertyMock(return_value='property_value')
    assert prop_mock is not None


def test_mock_open(mocker):
    '''Test mock_open access.'''
    m = mocker.mock_open(read_data='file content')
    mocker.patch('builtins.open', m)
    with open('/tmp/test.txt') as f:
        content = f.read()
    assert content == 'file content'


def test_sentinel(mocker):
    '''Test sentinel access.'''
    sentinel_value = mocker.sentinel.some_value
    assert sentinel_value is mocker.sentinel.some_value


def test_stop_specific_patch(mocker):
    '''Test stopping a specific patch.'''
    mock1 = mocker.patch('os.remove')
    mock2 = mocker.patch('os.path.exists', return_value=True)

    mocker.stop(mock1)
    # mock2 should still be active
    assert os.path.exists('/nonexistent') is True


def test_automatic_cleanup(mocker):
    '''Test that patches are automatically cleaned up.'''
    # This test verifies cleanup happens automatically
    # The cleanup is tested by running multiple tests in sequence
    mocker.patch('os.remove')
    # Cleanup happens automatically at test end
    assert True
"""
    )


def test_mocker_basic_functionality(tmp_path: Path) -> None:
    """Test that the mocker fixture works with basic patching."""
    test_file = tmp_path / "test_mocker_basic.py"
    _write_mocker_test_module(test_file)

    report = run_tree(str(test_file))
    assert report.passed == 20
    assert report.failed == 0
