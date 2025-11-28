# Documentation Assets

This directory contains automatically generated demo recordings of rustest's terminal output.

## Files

Demo recordings are generated from VHS tape files in `demos/`:

- `rustest-output.{gif,png,webm}` - Basic demo showing small test suite
- `rustest-full-suite.{gif,png,webm}` - Full test suite with multiple files

## Generation

These files are automatically regenerated when output rendering code changes via:

1. **Locally**: Run `./scripts/generate-demos.sh` or `poe demos`
2. **CI**: GitHub Actions workflow `.github/workflows/update-demos.yml`

## Usage

Use in documentation:

```markdown
![rustest output](assets/rustest-output.gif)
```

See `demos/README.md` for more details on customizing and generating demos.
