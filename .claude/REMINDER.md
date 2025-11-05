# 🚨 REMINDER FOR CLAUDE 🚨

## Before EVERY commit, you MUST run:

### For Rust changes:
```bash
cargo fmt
```

### For Python changes:
```bash
uv run ruff format python
```

## Then verify with:
```bash
cargo fmt --check
cargo clippy --lib -- -D warnings
uv run ruff check python
```

**NO EXCEPTIONS!** CI will fail if you skip these steps.

See `.claude/pre-commit-checklist.md` for full details.
