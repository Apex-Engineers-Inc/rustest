# Pre-Commit Checklist

## IMPORTANT: Run these commands before EVERY commit

### Rust Code Changes
If you modified any `.rs` files:
```bash
# Format Rust code
cargo fmt

# Check formatting
cargo fmt --check

# Lint with Clippy
cargo clippy --lib -- -D warnings
```

### Python Code Changes
If you modified any Python files:
```bash
# Format Python code
uv run ruff format python

# Lint Python code
uv run ruff check python

# Type check Python code
uv run basedpyright python
```

### General Workflow
1. Make changes to code
2. Run appropriate formatters and linters above
3. Fix any issues reported
4. Run tests if applicable
5. Commit only after all checks pass

## Quick Commands

```bash
# All Rust checks
cargo fmt && cargo fmt --check && cargo clippy --lib -- -D warnings

# All Python checks
uv run ruff format python && uv run ruff check python && uv run basedpyright python
```

## CI Will Fail If...
- Rust code is not formatted (cargo fmt --check fails)
- Rust code has clippy warnings
- Python code is not formatted (ruff format --check fails)
- Python code has linting errors
- Python code has type errors

Always run these checks locally before pushing to avoid CI failures!
