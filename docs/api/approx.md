# Assertion Utilities

The `approx()` function for tolerant numeric comparisons.

## approx

::: rustest.approx.approx

## Usage

The `approx()` function creates a comparison helper that checks if values are approximately equal within specified tolerances.

### Basic Comparison

```python
from rustest import approx

def test_floating_point():
    assert 0.1 + 0.2 == approx(0.3)
```

### With Tolerance

```python
from rustest import approx

def test_with_tolerance():
    # Relative tolerance (default: 1e-6)
    assert 100.0 == approx(100.0001, rel=1e-6)

    # Absolute tolerance (default: 1e-12)
    assert 1.0 == approx(1.001, abs=0.01)

    # Both tolerances
    assert 1.0 == approx(1.001, rel=1e-6, abs=0.01)
```

## Parameters

### value

**Type:** `float | complex | Iterable`

The expected value to compare against. Can be:
- A scalar number (int, float, complex)
- A collection (list, tuple) of numbers
- Any iterable of numbers

### rel

**Type:** `float` (optional, default: `1e-6`)

Maximum relative difference allowed. The test passes if:

```
abs(actual - expected) <= rel * abs(expected)
```

### abs

**Type:** `float` (optional, default: `1e-12`)

Maximum absolute difference allowed. The test passes if:

```
abs(actual - expected) <= abs_tolerance
```

### How Tolerances Work

The comparison passes if **either** the relative **or** absolute tolerance is satisfied:

```python
from rustest import approx

def test_tolerance_logic():
    actual = 1.0001
    expected = 1.0

    # Passes if within relative OR absolute tolerance
    assert actual == approx(expected, rel=1e-6, abs=1e-12)
```

## Examples

### Floating-Point Precision

```python
from rustest import approx

def test_float_precision():
    # Without approx - FAILS
    # assert 0.1 + 0.2 == 0.3  # False!

    # With approx - PASSES
    assert 0.1 + 0.2 == approx(0.3)
```

### Scientific Computing

```python
from rustest import approx

def test_physics_calculation():
    # Velocity calculation: v = d / t
    velocity = 100.0 / 9.8
    assert velocity == approx(10.204081632653061, rel=1e-9)
```

### Financial Calculations

```python
from rustest import approx

def test_price_with_tax():
    price = 19.99
    tax = 0.08
    total = price * (1 + tax)

    # Round to cents
    assert total == approx(21.59, abs=0.01)
```

### Comparing Collections

```python
from rustest import approx

def test_list_comparison():
    calculated = [0.1 + 0.1, 0.2 + 0.1, 0.3 + 0.1]
    expected = [0.2, 0.3, 0.4]

    assert calculated == approx(expected)
```

### Complex Numbers

```python
from rustest import approx

def test_complex_numbers():
    result = complex(1.0 + 1e-7, 2.0 + 1e-7)
    expected = complex(1.0, 2.0)

    assert result == approx(expected)
```

### Strict Tolerance

```python
from rustest import approx

def test_strict_tolerance():
    # Very strict relative tolerance
    assert 1.0000001 == approx(1.0, rel=1e-9)

    # Very strict absolute tolerance
    assert 1.0001 == approx(1.0, abs=0.001)
```

### Loose Tolerance

```python
from rustest import approx

def test_loose_tolerance():
    # 1% relative tolerance
    assert 100 == approx(101, rel=0.01)

    # 5 unit absolute tolerance
    assert 100 == approx(104, abs=5)
```

## Common Use Cases

### Comparing Means/Averages

```python
from rustest import approx

def test_mean():
    values = [1.1, 2.2, 3.3, 4.4, 5.5]
    mean = sum(values) / len(values)

    assert mean == approx(3.3, rel=1e-9)
```

### Testing Percentages

```python
from rustest import approx

def test_percentage():
    total = 100
    part = 33
    percentage = (part / total) * 100

    assert percentage == approx(33.0, abs=0.1)
```

### Mathematical Constants

```python
from rustest import approx
import math

def test_pi():
    calculated_pi = 22 / 7
    assert calculated_pi == approx(math.pi, abs=0.01)
```

### Temperature Conversions

```python
from rustest import approx

def celsius_to_fahrenheit(c):
    return (c * 9/5) + 32

def test_temperature():
    assert celsius_to_fahrenheit(0) == approx(32.0)
    assert celsius_to_fahrenheit(100) == approx(212.0)
    assert celsius_to_fahrenheit(37) == approx(98.6, abs=0.1)
```

## Best Practices

### Choose Appropriate Tolerances

```python
# Good - tolerance matches the domain
def test_scientific():
    # Science needs tight tolerance
    assert measurement == approx(expected, rel=1e-9)

def test_financial():
    # Money rounds to cents
    assert total == approx(expected, abs=0.01)

# Too loose - may hide bugs
def test_bad():
    assert 100 == approx(200, rel=0.5)  # 50% tolerance!
```

### Don't Use approx() for Exact Values

```python
from rustest import approx

def test_when_to_use_approx():
    # Good - exact integers
    assert 2 + 2 == 4

    # Unnecessary - integers are exact
    # assert 2 + 2 == approx(4)  # Works but not needed

    # Good - floating point needs approx
    assert 0.1 + 0.2 == approx(0.3)
```

### Be Explicit About Tolerances

```python
from rustest import approx

def test_explicit_tolerance():
    value = 100.0001
    expected = 100.0

    # Good - explicit tolerance for clarity
    assert value == approx(expected, rel=1e-6)

    # Also works - relies on default
    assert value == approx(expected)
```

## See Also

- [Assertions Guide](../guide/assertions.md) - Detailed usage and patterns
- [raises()](decorators.md#raises) - Exception testing
- [Writing Tests](../guide/writing-tests.md) - General testing guide
