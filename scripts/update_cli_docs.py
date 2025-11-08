#!/usr/bin/env python3
"""
Script to automatically capture rustest CLI help output and update documentation.

Usage:
    python scripts/update_cli_docs.py
"""

import subprocess
import re
from pathlib import Path


def get_cli_help() -> str:
    """Capture the output of rustest --help."""
    result = subprocess.run(
        ["python", "-m", "rustest", "--help"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "python"},
    )
    return result.stdout


def update_cli_docs(help_text: str) -> None:
    """Update the CLI documentation with the captured help text."""
    cli_doc_path = Path("docs/guide/cli.md")

    if not cli_doc_path.exists():
        print(f"Error: {cli_doc_path} not found")
        return

    content = cli_doc_path.read_text()

    # Define the pattern to match the help output section
    pattern = r'(## Quick Reference\n\n```bash\nrustest --help\n```\n\n```\n)(.*?)(```\n\n## Basic Commands)'

    # Replace the help output
    new_content = re.sub(
        pattern,
        f'\\1{help_text}\n\\3',
        content,
        flags=re.DOTALL
    )

    if new_content == content:
        print("Warning: No changes made. Pattern might not match.")
        return

    cli_doc_path.write_text(new_content)
    print(f"✓ Updated {cli_doc_path}")


def main():
    print("Capturing rustest CLI help output...")
    help_text = get_cli_help()

    if not help_text:
        print("Error: Failed to capture CLI help")
        return

    print("Updating CLI documentation...")
    update_cli_docs(help_text)
    print("\nDone! Review the changes and commit if they look good.")


if __name__ == "__main__":
    main()
