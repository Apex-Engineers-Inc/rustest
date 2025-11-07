# Example Test Markdown

This is a test markdown file with Python code blocks.

## Test 1: Simple Addition

```python
x = 1 + 1
assert x == 2
```

## Test 2: String Operations

```python
text = "hello world"
assert text.startswith("hello")
assert "world" in text
```

## Test 3: List Operations

```python
numbers = [1, 2, 3, 4, 5]
assert sum(numbers) == 15
assert len(numbers) == 5
```

## Non-Python Code Block (should be ignored)

```javascript
console.log("This is JavaScript, not Python");
```

## Test 4: Dictionary

```python
person = {"name": "Alice", "age": 30}
assert person["name"] == "Alice"
assert person["age"] == 30
```
