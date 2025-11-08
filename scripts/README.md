# Scripts

Utility scripts for rustest development.

## update_cli_docs.py

Automatically captures the rustest CLI help output and updates the documentation.

**Usage:**

```bash
python scripts/update_cli_docs.py
```

**When to run:**

- After changing CLI arguments in `python/rustest/cli.py`
- Before committing changes that affect the CLI
- When you notice the CLI docs are out of sync

**What it does:**

1. Runs `rustest --help` to capture the current help output
2. Updates `docs/guide/cli.md` with the captured output
3. Preserves the rest of the documentation structure

**Example workflow:**

```bash
# 1. Make changes to CLI
vim python/rustest/cli.py

# 2. Update docs automatically
python scripts/update_cli_docs.py

# 3. Review changes
git diff docs/guide/cli.md

# 4. Commit if they look good
git add docs/guide/cli.md
git commit -m "Update CLI documentation"
```

## Future Scripts

Other utility scripts will be added here as needed:

- Test data generators
- Benchmark runners
- Documentation validators
- Release automation
