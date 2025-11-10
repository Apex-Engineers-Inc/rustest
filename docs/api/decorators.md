# Decorators

Rustest provides decorators for defining fixtures, parametrizing tests, skipping tests, and marking tests.

## fixture

::: rustest.decorators.fixture

## parametrize

::: rustest.decorators.parametrize

## skip

::: rustest.decorators.skip

## mark

The `mark` object allows you to create custom marks for organizing tests.

### Usage

```python
from rustest import mark

@mark.slow
def test_expensive():
    pass

@mark.integration
@mark.critical
def test_important():
    pass

@mark.timeout(seconds=30)
def test_with_args():
    pass
```

### Mark Types

::: rustest.decorators.MarkGenerator
    options:
      members:
        - __getattr__

::: rustest.decorators.MarkDecorator

## raises

::: rustest.decorators.raises

### RaisesContext

::: rustest.decorators.RaisesContext
    options:
      members:
        - __enter__
        - __exit__
        - value
        - type

### ExceptionInfo

::: rustest.decorators.ExceptionInfo

## Examples

### Fixture with Scope

```python
from rustest import fixture

@fixture(scope="session")
def database():
    db = create_database()
    yield db
    db.close()

def test_query(database):
    result = database.query("SELECT 1")
    assert result is not None
```

### Parametrization with IDs

```python
from rustest import parametrize

@parametrize("value,expected", [
    (2, 4),
    (3, 9),
    (4, 16),
], ids=["two", "three", "four"])
def test_square(value, expected):
    assert value ** 2 == expected
```

### Multiple Marks

```python
from rustest import mark

@mark.slow
@mark.integration
@mark.requires_database
def test_complex_operation():
    pass
```

### Exception Testing with Match

```python
from rustest import raises

def test_validation():
    with raises(ValueError, match="Email cannot be empty"):
        validate_email("")
```

### Async Testing

```python
from rustest import mark
import asyncio

@mark.asyncio
async def test_async_operation():
    """Test async function."""
    result = await some_async_function()
    assert result == expected_value

@mark.asyncio(loop_scope="module")
async def test_with_module_loop():
    """Test with module-scoped event loop."""
    await another_async_operation()

@mark.asyncio(loop_scope="class")
class TestAsyncAPI:
    """All async methods share an event loop."""

    async def test_get(self):
        result = await api.get()
        assert result is not None

    async def test_post(self):
        result = await api.post(data)
        assert result["status"] == "success"
```

## See Also

- [Fixtures Guide](../guide/fixtures.md) - Detailed fixture usage
- [Parametrization Guide](../guide/parametrization.md) - Parametrization patterns
- [Marks & Skipping Guide](../guide/marks.md) - Using marks effectively
- [Assertions Guide](../guide/assertions.md) - Using raises() and approx()
