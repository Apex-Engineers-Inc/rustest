# Demo Recordings

This directory contains VHS tape files for generating demonstration recordings of rustest's terminal output.

## Prerequisites

Install [VHS](https://github.com/charmbracelet/vhs):

```bash
# macOS
brew install vhs

# Linux
go install github.com/charmbracelet/vhs@latest

# Or download from releases
# https://github.com/charmbracelet/vhs/releases
```

## Generating Demos

### Quick Start

Run the automated script:

```bash
./scripts/generate-demos.sh
```

Or use the poe task:

```bash
poe demos
```

### Manual Generation

Generate individual demos:

```bash
# Basic output demo
vhs demos/basic-output.tape

# Full test suite demo
vhs demos/full-suite.tape
```

## Available Demos

### `basic-output.tape`
Shows rustest running a small test suite with 3 tests. Good for quick demonstrations.

**Outputs:**
- `docs/assets/rustest-output.gif` - Animated GIF for GitHub/docs
- `docs/assets/rustest-output.png` - Static screenshot
- `docs/assets/rustest-output.webm` - Video format

### `full-suite.tape`
Shows rustest running the complete test suite with multiple files and progress bars.

**Outputs:**
- `docs/assets/rustest-full-suite.gif`
- `docs/assets/rustest-full-suite.png`
- `docs/assets/rustest-full-suite.webm`

## Customizing Demos

Edit the `.tape` files to customize:

- `Set FontSize <number>` - Adjust font size
- `Set Width <number>` - Terminal width (columns)
- `Set Height <number>` - Terminal height (rows)
- `Set Theme "<theme>"` - Color scheme (see VHS docs)
- `Set PlaybackSpeed <number>` - Playback speed multiplier

Available themes include:
- "Catppuccin Mocha" (current)
- "Dracula"
- "Nord"
- "Tokyo Night"
- See [VHS themes](https://github.com/charmbracelet/vhs/tree/main/themes)

## Automation

Demos are automatically regenerated when:
- Output rendering code changes (`src/output/**`, `python/rustest/renderers/**`)
- Tape files are updated (`demos/**`)
- Manually triggered via GitHub Actions

The workflow commits updated demo files back to the repository.

## Using in Documentation

### Markdown

```markdown
![rustest output demo](assets/rustest-output.gif)
```

### HTML (with fallback)

```html
<picture>
  <source srcset="assets/rustest-output.webm" type="video/webm">
  <img src="assets/rustest-output.gif" alt="rustest demo">
</picture>
```

## Troubleshooting

### VHS command not found

Ensure VHS is installed and in your PATH:

```bash
which vhs
```

### Build errors

Ensure rustest is built before generating demos:

```bash
uv run maturin develop
```

### Recording looks wrong

Check your terminal theme and font support Unicode characters:
- Use a modern terminal (iTerm2, Alacritty, WezTerm, etc.)
- Ensure terminal supports 256 colors
- Use a font with good Unicode coverage (Nerd Fonts recommended)
