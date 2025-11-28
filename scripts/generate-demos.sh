#!/bin/bash
# Generate demo recordings for documentation
#
# Requirements:
#   - VHS: https://github.com/charmbracelet/vhs
#   - Built rustest extension (run: uv run maturin develop)
#
# Usage:
#   ./scripts/generate-demos.sh

set -e

echo "🎬 Generating rustest demo recordings..."

# Check if VHS is installed
if ! command -v vhs &> /dev/null; then
    echo "❌ VHS is not installed"
    echo ""
    echo "Install VHS:"
    echo "  macOS:  brew install vhs"
    echo "  Linux:  go install github.com/charmbracelet/vhs@latest"
    echo "  Binary: https://github.com/charmbracelet/vhs/releases"
    exit 1
fi

# Ensure rustest is built
if [ ! -f ".venv/lib/python3.11/site-packages/rustest/rust.so" ]; then
    echo "⚙️  Building rustest..."
    uv run maturin develop
fi

# Create output directory
mkdir -p docs/assets

# Generate demos
echo "📹 Recording: Basic output demo..."
vhs demos/basic-output.tape

echo "📹 Recording: Full suite demo..."
vhs demos/full-suite.tape

echo ""
echo "✅ Demo recordings generated:"
echo "   - docs/assets/rustest-output.gif"
echo "   - docs/assets/rustest-output.png"
echo "   - docs/assets/rustest-output.webm"
echo "   - docs/assets/rustest-full-suite.gif"
echo "   - docs/assets/rustest-full-suite.png"
echo "   - docs/assets/rustest-full-suite.webm"
echo ""
echo "📝 Use in docs with:"
echo "   ![rustest output](assets/rustest-output.gif)"
